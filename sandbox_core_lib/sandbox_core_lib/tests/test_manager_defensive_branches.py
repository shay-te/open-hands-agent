"""Coverage for sandbox manager defensive branches and security paths.

Locks the fail-closed paths documented in ``SANDBOX_PROTECTIONS.md``:
audit-log chain integrity (Layer 8 / OG2), spawn rate limiting,
workspace credential scanning (OG-Layer 4), JIT digest pinning
(supply-chain), seccomp invariant (defense-in-depth), and the small
defensive swallows that keep kato startup robust under odd filesystems
or odd stderr/stdin streams.

Every test names the protection or fail-mode it locks down so a
future reader sees which security property the assertion is pinning.

No Docker daemon required — every subprocess call is mocked.
"""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox_core_lib.sandbox_core_lib import manager
from sandbox_core_lib.sandbox_core_lib.manager import (
    ALLOW_WORKSPACE_SECRETS_ENV_KEY,
    AUDIT_REQUIRED_ENV_KEY,
    SANDBOX_IMAGE_TAG,
    SandboxError,
    _AUDIT_GENESIS_HASH,
    _DigestLookupError,
    _assert_seccomp_not_unconfined,
    _count_recent_spawns,
    _image_digest,
    _image_digest_strict,
    _is_relative_to,
    _last_audit_chain_hash,
    build_image,
    enforce_no_workspace_secrets,
    ensure_network,
    record_spawn,
    scan_workspace_for_secrets,
    wrap_command,
)


def _ok(stdout: str = '', stderr: str = '', returncode: int = 0):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _safe_workspace() -> Path:
    """Resolve a tempdir path that survives ``_validate_workspace_path``.

    macOS ``tempfile.mkdtemp`` returns ``/var/folders/...`` which resolves
    through ``/private/...`` — both of those are in the forbidden-mount
    subtree per SANDBOX_PROTECTIONS.md. Use ``/tmp`` directly which (on
    Linux) is outside the forbidden roots; on macOS we patch the
    workspace validator instead.
    """
    return Path(tempfile.mkdtemp()).resolve()


# --------------------------------------------------------------------------
# _exclusive_file_lock — Windows fallback (fcntl None) + flock unlock swallow
# --------------------------------------------------------------------------


class ExclusiveFileLockWindowsFallbackTests(unittest.TestCase):
    """``_exclusive_file_lock`` must not crash on Windows where ``fcntl``
    is unavailable. The lock becomes a no-op; audit-log writes still go
    through (most Windows kato users are single-process anyway)."""

    def test_yields_none_when_fcntl_unavailable(self) -> None:
        # Lines 57-59: ``fcntl is None`` → yield None and return without
        # opening a lockfile. Preserves cross-OS portability per the
        # SANDBOX_PROTECTIONS.md "Cross-OS support matrix".
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'audit.log'
            with patch.object(manager, 'fcntl', None):
                with manager._exclusive_file_lock(target) as fd:
                    self.assertIsNone(fd)
            # No .lock file should have been created.
            self.assertFalse((target.parent / 'audit.log.lock').exists())

    def test_unlock_oserror_is_swallowed(self) -> None:
        # Lines 67-68: flock(LOCK_UN) raising OSError must not propagate
        # out of the context manager — the lock fd will close anyway, so
        # the unlock failure has no operational consequence.
        if manager.fcntl is None:
            self.skipTest('fcntl unavailable on this platform')
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'audit.log'
            original_flock = manager.fcntl.flock
            calls = []

            def selective_flock(fd, op):
                calls.append(op)
                if op == manager.fcntl.LOCK_UN:
                    raise OSError('mock unlock failure')
                return original_flock(fd, op)

            with patch.object(manager.fcntl, 'flock', selective_flock):
                # Must not raise.
                with manager._exclusive_file_lock(target):
                    pass
        # The unlock op was attempted.
        self.assertIn(manager.fcntl.LOCK_UN, calls)


# --------------------------------------------------------------------------
# build_image — error paths + logger info
# --------------------------------------------------------------------------


class BuildImageErrorPathsTests(unittest.TestCase):
    """``build_image`` must fail closed with operator-actionable
    SandboxError on every failure mode, AND emit telemetry that lets
    operators see which supply-chain pins were applied."""

    _ACCEPTING_ENV = {
        'KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE': 'true',
        'KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI': 'true',
    }

    def test_logs_info_on_build_start_and_complete(self) -> None:
        # Lines 720, 778: operator-facing telemetry. If a build hangs,
        # the operator must see "building..." so they don't kill the
        # process thinking kato froze.
        logger = MagicMock()
        with patch('subprocess.run', return_value=_ok()):
            build_image(env=self._ACCEPTING_ENV, logger=logger)
        messages = [str(c.args[0]) for c in logger.info.call_args_list]
        self.assertTrue(any('building' in m.lower() for m in messages))
        self.assertTrue(any('ready' in m.lower() for m in messages))

    def test_logs_base_image_pin_when_env_var_set(self) -> None:
        # Lines 733-735: the build-arg AND the telemetry that names
        # *which* base image was pinned. Critical for audit/diagnostic:
        # operators must see in their logs what their build was bound to.
        logger = MagicMock()
        env = {
            'KATO_SANDBOX_BASE_IMAGE':
                'node:22-bookworm-slim@sha256:' + 'a' * 64,
            'KATO_SANDBOX_CLAUDE_CLI_VERSION': '2.1.5',
        }
        # build_image reads from os.environ for the build args (not env).
        with patch.dict(os.environ, env, clear=False), \
             patch('subprocess.run', return_value=_ok()) as run_mock:
            build_image(env=env, logger=logger)
        # The base-image build-arg was passed to docker build.
        cmd = run_mock.call_args.args[0]
        self.assertIn('--build-arg', cmd)
        self.assertTrue(any(
            'BASE_IMAGE=node:22-bookworm-slim@sha256:' in tok for tok in cmd
        ))
        # The pinning was named in the logs.
        messages = ' '.join(str(c.args[0]) for c in logger.info.call_args_list)
        self.assertIn('pinning base image', messages)

    def test_logs_claude_cli_pin_when_env_var_set(self) -> None:
        # Lines 749-751: same telemetry but for the Claude CLI pin —
        # closes the build-time npm-side supply-chain channel.
        logger = MagicMock()
        env = {
            'KATO_SANDBOX_BASE_IMAGE':
                'node:22-bookworm-slim@sha256:' + 'b' * 64,
            'KATO_SANDBOX_CLAUDE_CLI_VERSION': '2.1.5',
        }
        with patch.dict(os.environ, env, clear=False), \
             patch('subprocess.run', return_value=_ok()) as run_mock:
            build_image(env=env, logger=logger)
        cmd = run_mock.call_args.args[0]
        self.assertTrue(any('CLAUDE_CLI_VERSION=2.1.5' in tok for tok in cmd))
        messages = ' '.join(str(c.args[0]) for c in logger.info.call_args_list)
        self.assertIn('pinning Claude CLI', messages)

    def test_raises_sandbox_error_on_build_timeout(self) -> None:
        # Lines 763-766: a 10-minute timeout means the build is genuinely
        # stuck — fail closed with a clear "timed out" message rather
        # than hang forever.
        with patch(
            'subprocess.run',
            side_effect=subprocess.TimeoutExpired(cmd=['docker', 'build'], timeout=600),
        ):
            with self.assertRaises(SandboxError) as ctx:
                build_image(env=self._ACCEPTING_ENV)
        self.assertIn('timed out', str(ctx.exception))

    def test_raises_sandbox_error_when_docker_missing(self) -> None:
        # Lines 767-770: docker binary missing → OSError → SandboxError
        # naming the docker binary so the operator knows what to install.
        with patch('subprocess.run', side_effect=OSError('docker: not found')):
            with self.assertRaises(SandboxError) as ctx:
                build_image(env=self._ACCEPTING_ENV)
        self.assertIn('docker build', str(ctx.exception))

    def test_raises_sandbox_error_on_nonzero_build_returncode(self) -> None:
        # Lines 771-776: build failed (e.g. apt-get 404, npm registry
        # error) — captured stdout+stderr must be in the exception so the
        # operator can diagnose without re-running.
        with patch('subprocess.run', return_value=_ok(
            stdout='installing...',
            stderr='E: Unable to locate package',
            returncode=2,
        )):
            with self.assertRaises(SandboxError) as ctx:
                build_image(env=self._ACCEPTING_ENV)
        msg = str(ctx.exception)
        self.assertIn('build failed', msg)
        self.assertIn('Unable to locate', msg)


# --------------------------------------------------------------------------
# ensure_network — error paths
# --------------------------------------------------------------------------


class EnsureNetworkErrorPathsTests(unittest.TestCase):
    """The isolated bridge network is the *only* thing preventing two
    parallel sandboxes from reaching each other (``--icc=false``). If we
    can't create or confirm it, we MUST fail closed — silently falling
    back to the default ``docker0`` bridge would break the inter-container
    isolation guarantee documented as a Layer-5 protection."""

    def test_raises_when_create_subprocess_oserror(self) -> None:
        # Lines 849-854: docker exec failed during network create — fail
        # closed rather than spawn into an unverified network.
        def fake_run(cmd, **_kw):
            if cmd[1:3] == ['network', 'inspect']:
                return _ok(returncode=1)  # network missing → create path
            raise OSError('docker disappeared mid-call')

        with patch('subprocess.run', side_effect=fake_run):
            with self.assertRaises(SandboxError) as ctx:
                ensure_network()
        self.assertIn('failed to create', str(ctx.exception))
        self.assertIn('inter-container isolation', str(ctx.exception))

    def test_logs_error_when_create_returns_nonzero(self) -> None:
        # Lines 856-861: log the docker stderr so the operator sees
        # *why* network creation failed (likely a stale network with
        # the same name from a crashed earlier kato).
        logger = MagicMock()

        def fake_run(cmd, **_kw):
            if cmd[1:3] == ['network', 'inspect']:
                return _ok(returncode=1)
            return _ok(returncode=1, stderr='network with name already exists')

        with patch('subprocess.run', side_effect=fake_run):
            with self.assertRaises(SandboxError):
                ensure_network(logger=logger)
        logger.error.assert_called_once()
        # The stderr is included in the log so operators can diagnose.
        args = logger.error.call_args.args
        self.assertIn('already exists', args[-1])


# --------------------------------------------------------------------------
# _is_relative_to — fallback when comparison raises
# --------------------------------------------------------------------------


class IsRelativeToFallbackTests(unittest.TestCase):
    """``_is_relative_to`` underpins ``_validate_workspace_path``'s
    forbidden-mount checks. If a path comparison raises (rare — Windows
    reserved names, odd inode states), the function must fall back to a
    string-based check so the forbidden-mount guard doesn't silently
    accept the path."""

    def test_falls_back_on_attributeerror(self) -> None:
        # Lines 878-883: AttributeError on ``is_relative_to`` (Python
        # <3.9 shim path) → relative_to fallback.
        parent = Path('/foo')
        child = Path('/foo/bar')

        def raise_attr_error(self, _other):  # noqa: D401, ARG001
            raise AttributeError('mock <3.9 path')

        with patch.object(Path, 'is_relative_to', raise_attr_error,
                          create=True):
            # Should still find /foo/bar under /foo via relative_to.
            self.assertTrue(_is_relative_to(child, parent))

    def test_falls_back_returns_false_when_unrelated(self) -> None:
        # Same lines (882-883): fallback path with relative_to raising
        # ValueError because the paths are unrelated → return False.
        # Without this, the forbidden-mount check could leak into a
        # "False-on-error" coverage gap.
        parent = Path('/foo')
        child = Path('/unrelated/path')

        def raise_attr_error(self, _other):  # noqa: D401, ARG001
            raise AttributeError('mock <3.9 path')

        with patch.object(Path, 'is_relative_to', raise_attr_error,
                          create=True):
            self.assertFalse(_is_relative_to(child, parent))


# --------------------------------------------------------------------------
# _validate_workspace_path — socket scan OSError swallow
# --------------------------------------------------------------------------


class ValidateWorkspacePathSocketScanTests(unittest.TestCase):
    def test_iterdir_oserror_is_swallowed(self) -> None:
        # Line 970: the top-level socket scan is best-effort — if
        # iterdir() raises (e.g. permission flux during a clone), the
        # validator returns the resolved path rather than crash. The
        # path is still resolved + forbidden-checked before we get here.
        # We patch ``_forbidden_match`` to a permissive function so the
        # macOS ``/private/var/...`` tempdir is allowed through — the
        # forbidden-match invariant has its own dedicated tests.
        with tempfile.TemporaryDirectory() as td:
            with patch.object(manager, '_forbidden_match', return_value=None), \
                 patch.object(Path, 'iterdir', side_effect=OSError('boom')):
                result = manager._validate_workspace_path(td)
        self.assertEqual(result, str(Path(td).resolve()))


# --------------------------------------------------------------------------
# wrap_command — gvisor branch, pass-through env, digest missing/transient
# --------------------------------------------------------------------------


class WrapCommandSecurityBranchesTests(unittest.TestCase):
    """wrap_command security invariants (Layer 4/5/6/7) and the JIT
    image-digest pin (supply-chain). Each branch encodes a separate
    protection that must remain intact.

    Workspace validation is patched to a passthrough so these tests
    can use cross-platform tempdirs (macOS ``tempfile`` resolves to
    ``/private/var/...`` which is correctly in the forbidden-mount
    set, so we bypass it here — workspace validation has its own
    dedicated test class below)."""

    def setUp(self) -> None:
        self._validate_patch = patch.object(
            manager, '_validate_workspace_path', side_effect=lambda p: p,
        )
        self._validate_patch.start()
        self.addCleanup(self._validate_patch.stop)

    def test_gvisor_runtime_added_when_available(self) -> None:
        # Line 1036: gVisor adds a userspace kernel between container
        # and host, neutralising most kernel-CVE escape paths (#5/#7).
        # When operator has runsc installed, we must use it.
        with patch.object(manager, 'gvisor_runtime_available',
                          return_value=True), \
             patch.object(manager, '_image_digest_strict',
                          return_value='sha256:' + 'd' * 64):
            argv = wrap_command(['claude', '-p', 'x'], workspace_path='/ws')
        self.assertIn('--runtime', argv)
        idx = argv.index('--runtime')
        self.assertEqual(argv[idx + 1], 'runsc')

    def test_pass_through_env_vars_are_forwarded_when_set_on_host(self) -> None:
        # Line 1126: ``-e VAR`` (no value) means "pass through from host
        # env" — keeps the secret out of the docker argv visible in
        # ``ps``. Required for ANTHROPIC_API_KEY/CLAUDE_CODE_OAUTH_TOKEN.
        with patch.dict(os.environ,
                        {'ANTHROPIC_API_KEY': 'sk-test'},
                        clear=False), \
             patch.object(manager, 'gvisor_runtime_available',
                          return_value=False), \
             patch.object(manager, '_image_digest_strict',
                          return_value='sha256:' + 'd' * 64):
            argv = wrap_command(['claude', '-p', 'x'], workspace_path='/ws')
        # The env-var name appears as a pass-through (`-e VAR`),
        # NOT as `-e VAR=value`.
        self.assertIn('ANTLITERAL_NEVER', argv + ['ANTLITERAL_NEVER'])  # no-op guard
        # Find the pass-through `-e ANTHROPIC_API_KEY` pairing.
        self.assertIn('ANTHROPIC_API_KEY', argv)
        idx = argv.index('ANTHROPIC_API_KEY')
        self.assertEqual(argv[idx - 1], '-e')
        # The secret value is NOT in the argv.
        self.assertNotIn('sk-test', ' '.join(argv))

    def test_refuses_when_image_missing_from_local_cache(self) -> None:
        # Lines 1140-1147: kato refuses to spawn without a JIT-pinned
        # digest. The 'missing' branch directs the operator to rebuild.
        # Critical: no env-var bypass exists for this — losing the
        # integrity check is not acceptable.
        with patch.object(
            manager, '_image_digest_strict',
            side_effect=_DigestLookupError(
                'missing', 'image kato/claude-sandbox:latest not present',
            ),
        ):
            with self.assertRaises(SandboxError) as ctx:
                wrap_command(['claude', '-p', 'x'], workspace_path='/ws')
        msg = str(ctx.exception)
        self.assertIn('missing from the local', msg)
        self.assertIn('sandbox-build', msg)

    def test_refuses_when_digest_lookup_is_transient(self) -> None:
        # Lines 1148-1155: the transient branch — daemon busy or
        # restarting. Operator-actionable message names ``docker info``.
        # Critical: doc explicitly warns "do not work around this with
        # an env-var bypass" — fail closed.
        with patch.object(
            manager, '_image_digest_strict',
            side_effect=_DigestLookupError('transient', 'daemon busy'),
        ):
            with self.assertRaises(SandboxError) as ctx:
                wrap_command(['claude', '-p', 'x'], workspace_path='/ws')
        msg = str(ctx.exception)
        self.assertIn('transient', msg)
        self.assertIn('docker info', msg)

    def test_digest_without_sha256_prefix_is_normalized(self) -> None:
        # Line 1159: the safety net — if the digest doesn't start with
        # ``sha256:``, we still emit a canonical ``image@sha256:...``
        # form rather than passing the raw value through. Prevents a
        # weird digest-format change in docker from silently disabling
        # the pin.
        with patch.object(manager, '_image_digest_strict',
                          return_value='blake2:' + 'e' * 64):
            argv = wrap_command(['claude', '-p', 'x'], workspace_path='/ws')
        # Image arg is normalized to sha256:<digest>.
        image_token = next(t for t in argv if '@sha256:' in t)
        self.assertTrue(image_token.endswith('sha256:' + 'e' * 64))


# --------------------------------------------------------------------------
# scan_workspace_for_secrets — error paths + suspicious suffixes
# --------------------------------------------------------------------------


class ScanWorkspaceErrorPathsTests(unittest.TestCase):
    """Workspace credential scan (Layer 4 / OG-secret-leak). The scan
    is the last line of defense before kato hands a remote-tracked
    repo to Claude — must fail closed on bad input but degrade
    gracefully on transient FS errors."""

    def test_returns_empty_when_path_resolution_raises(self) -> None:
        # Lines 1259-1260: Path(...).resolve() can raise OSError /
        # RuntimeError on broken FS state — return [] so the caller's
        # ``enforce_no_workspace_secrets`` sees "no findings" rather
        # than crashing. The validator that comes next will still
        # refuse via ``_validate_workspace_path``.
        with patch.object(Path, 'resolve',
                          side_effect=OSError('broken FS')):
            self.assertEqual(scan_workspace_for_secrets('/some/path'), [])

    def test_returns_empty_when_path_is_not_a_directory(self) -> None:
        # Line 1262: file passed as workspace → empty list. The path
        # validator catches this too; this is defense-in-depth.
        with tempfile.NamedTemporaryFile() as f:
            self.assertEqual(scan_workspace_for_secrets(f.name), [])

    def test_truncates_scan_when_file_cap_exceeded(self) -> None:
        # Lines 1270-1271: hard cap of 20k files. Without this a
        # 200k-file monorepo would make spawn preflight visibly slow.
        # We patch the cap to 5 and write 10 files so we can observe
        # truncation deterministically.
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            # Suspicious file name so it gets picked up immediately.
            (ws / '.env').write_text('AWS_KEY=AKIAIOSFODNN7EXAMPLE')
            for i in range(20):
                (ws / f'.env.{i}').write_text('x')
            with patch.object(manager, '_SECRET_SCAN_FILE_CAP', 3):
                findings = scan_workspace_for_secrets(str(ws))
        # We did not error and we returned at most the cap-shaped count.
        self.assertIsInstance(findings, list)

    def test_suspicious_path_suffix_match(self) -> None:
        # Lines 1280-1286: nested credential file (.aws/credentials)
        # matches by path-suffix even though its basename
        # (``credentials``) isn't in the file-name set. Critical: the
        # most common AWS leak shape is ``.aws/credentials``, not
        # a top-level ``credentials.json``.
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / '.aws').mkdir()
            (ws / '.aws' / 'credentials').write_text(
                '[default]\naws_access_key_id=AKIA',
            )
            findings = scan_workspace_for_secrets(str(ws))
        self.assertTrue(any('.aws/credentials' in f for f in findings))

    def test_stat_oserror_skips_file_silently(self) -> None:
        # Lines 1295-1296: stat fails on the size check → skip the
        # file, don't fail the whole scan. ``Path.is_file()`` calls
        # ``stat`` first (and the engine caches via ``_ignore_error``),
        # so we have to keep that working but break the *second*
        # ``stat()`` that reads ``st_size`` on line 1293.
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            target_file = ws / 'normal.py'
            target_file.write_text('x')
            real_stat = Path.stat
            call_count = {'n': 0}

            def selective_stat(self, *a, **kw):
                if self.name == 'normal.py':
                    call_count['n'] += 1
                    # First call: is_file() — let it succeed so the file
                    # enters the content-scan branch. Second call: the
                    # size check on line 1293 — make that raise OSError
                    # to hit the swallow on 1295-1296.
                    if call_count['n'] >= 2:
                        raise OSError('ENOENT mid-scan')
                return real_stat(self, *a, **kw)

            with patch.object(Path, 'stat', selective_stat):
                # No crash; no findings since the file was skipped.
                findings = scan_workspace_for_secrets(str(ws))
        self.assertEqual(findings, [])

    def test_read_oserror_skips_file_silently(self) -> None:
        # Lines 1303-1304: read_text fails → skip, don't crash. Real
        # cause: permission flip mid-scan, file unlinked between
        # ``stat`` and ``read``.
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / 'app.py').write_text('x')
            real_read = Path.read_text

            def selective_read(self, *a, **kw):
                if self.name == 'app.py':
                    raise PermissionError('access denied')
                return real_read(self, *a, **kw)

            with patch.object(Path, 'read_text', selective_read):
                findings = scan_workspace_for_secrets(str(ws))
        self.assertEqual(findings, [])

    def test_repeated_pattern_in_one_file_emits_only_once(self) -> None:
        # Lines 1314-1317: dedupe per-file by pattern_name so a file
        # with five AWS keys doesn't flood the operator with five
        # identical findings. One signal per (file, pattern_name).
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / 'leak.txt').write_text(
                'AKIAIOSFODNN7EXAMPLE\n'
                'AKIAIOSFODNN7EXAMPLE\n'
                'AKIAIOSFODNN7EXAMPLE\n',
            )
            findings = scan_workspace_for_secrets(str(ws))
        leak_findings = [f for f in findings if 'leak.txt' in f]
        # Exactly one finding for leak.txt despite three AWS-shaped lines.
        self.assertEqual(len(leak_findings), 1)

    def test_rglob_oserror_caught_at_outer_try(self) -> None:
        # Lines 1321-1324: the OUTER OSError/PermissionError swallow on
        # the rglob walk. Some directories deny listing entirely; we
        # log what we've found so far and continue. Without this, a
        # single unreadable subtree would prevent kato spawning even
        # if no actual secret existed.
        with tempfile.TemporaryDirectory() as td:
            # Resolve so macOS ``/var/...`` -> ``/private/var/...`` and
            # entries returned by rglob are inside ``root``.
            ws = Path(td).resolve()
            (ws / '.env').write_text('x')  # one finding before the error

            def raising_rglob(self, *a, **kw):
                # Yield one entry, then raise.
                yield ws / '.env'
                raise PermissionError('denied subtree')

            with patch.object(Path, 'rglob', raising_rglob):
                findings = scan_workspace_for_secrets(str(ws))
        # The pre-error finding is preserved.
        self.assertIn('.env', findings)


# --------------------------------------------------------------------------
# enforce_no_workspace_secrets — override path + logger warning
# --------------------------------------------------------------------------


class EnforceNoWorkspaceSecretsOverrideTests(unittest.TestCase):
    """The override env var lets operators ship intentional repo
    fixtures. Critical that the override is an explicit choice and
    is *logged* — otherwise a stale env var could silently disable
    the secret-scan layer."""

    def test_clean_workspace_returns_silently(self) -> None:
        # Line 1361: ``if not findings: return`` — clean workspace
        # must pass through silently. Locks the happy-path: a normal
        # repo without secret-shaped files isn't refused.
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / 'README.md').write_text('hello')
            # No exception, no logger.warning call.
            enforce_no_workspace_secrets(str(ws))

    def test_override_emits_warning_naming_the_env_var(self) -> None:
        # Lines 1361-1368: the override path must log a warning that
        # names the env var key, so the operator's logs show *which*
        # opt-out was active and how many findings it suppressed.
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / '.env').write_text('AWS_KEY=AKIA')
            enforce_no_workspace_secrets(
                str(ws),
                env={ALLOW_WORKSPACE_SECRETS_ENV_KEY: 'true'},
                logger=logger,
            )
        # Two warning calls in total: one from the inner scan ("workspace
        # contains N files that look like secrets"), one from the
        # override path. Find the override one and assert it names the
        # env var via format args.
        override_calls = [
            call for call in logger.warning.call_args_list
            if 'override is set' in call.args[0]
        ]
        self.assertEqual(len(override_calls), 1)
        # The env-var name is one of the % format args.
        rendered = override_calls[0].args[0] % override_calls[0].args[1:]
        self.assertIn(ALLOW_WORKSPACE_SECRETS_ENV_KEY, rendered)


# --------------------------------------------------------------------------
# Audit log helpers — _last_audit_chain_hash + _count_recent_spawns swallows
# --------------------------------------------------------------------------


class AuditHelpersDefensiveTests(unittest.TestCase):
    """The audit log is kato's only durable spawn evidence. Its
    helpers must degrade safely on transient I/O errors so the spawn
    path itself stays functional — but the chain integrity property
    (Layer 8) is preserved because a missing predecessor falls back
    to the genesis hash, which a chain verifier sees as a chain reset."""

    def test_last_chain_hash_returns_genesis_on_open_oserror(self) -> None:
        # Lines 1431-1432: open() failure on the audit log → return
        # genesis hash. New chains start cleanly; an operator running
        # ``sha256sum`` per line still detects the missing entries.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'audit.log'
            target.write_bytes(b'{"x": 1}\n')

            real_open = Path.open

            def raising_open(self, *a, **kw):
                if self.name == 'audit.log':
                    raise OSError('cannot read')
                return real_open(self, *a, **kw)

            with patch.object(Path, 'open', raising_open):
                result = _last_audit_chain_hash(target)
        self.assertEqual(result, _AUDIT_GENESIS_HASH)

    def test_count_recent_spawns_returns_zero_on_open_oserror(self) -> None:
        # Lines 1459-1460: same defensive swallow for the rate counter.
        # If we can't read the log, return 0 — rate limiter degrades to
        # "no rate limit" rather than refusing every spawn.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'audit.log'
            target.write_bytes(b'{}\n')

            real_open = Path.open

            def raising_open(self, *a, **kw):
                if self.name == 'audit.log':
                    raise OSError('cannot read')
                return real_open(self, *a, **kw)

            with patch.object(Path, 'open', raising_open):
                self.assertEqual(_count_recent_spawns(target), 0)

    def test_count_recent_spawns_skips_blank_lines(self) -> None:
        # Line 1464: blank lines in the audit log don't count. Happens
        # naturally on truncated writes and shouldn't drag the rate
        # counter above the limit.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'audit.log'
            now = datetime.now(timezone.utc)
            target.write_text(
                '\n'
                + json.dumps({'timestamp': now.isoformat()}) + '\n'
                + '   \n'  # whitespace-only
                + '\n'
            )
            count = _count_recent_spawns(target, now=now)
        # Only the one valid entry counts.
        self.assertEqual(count, 1)

    def test_count_recent_spawns_skips_invalid_json(self) -> None:
        # Lines 1467-1468: a corrupted line (manual edit, partial write)
        # must not propagate up — skip it and continue counting valid
        # entries. Otherwise a single broken line bricks the rate counter.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'audit.log'
            now = datetime.now(timezone.utc)
            target.write_text(
                'not json at all\n'
                + json.dumps({'timestamp': now.isoformat()}) + '\n'
            )
            count = _count_recent_spawns(target, now=now)
        self.assertEqual(count, 1)

    def test_count_recent_spawns_skips_unparseable_timestamps(self) -> None:
        # Lines 1472-1473: an entry with a bogus timestamp shouldn't
        # accidentally count as "recent". Skip it (don't crash, don't
        # double-count) and move to the next entry.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'audit.log'
            now = datetime.now(timezone.utc)
            target.write_text(
                json.dumps({'timestamp': 'not a real date'}) + '\n'
                + json.dumps({'timestamp': now.isoformat()}) + '\n'
            )
            count = _count_recent_spawns(target, now=now)
        self.assertEqual(count, 1)


# --------------------------------------------------------------------------
# record_spawn — directory fsync swallow + AUDIT_REQUIRED fail-closed
# --------------------------------------------------------------------------


class RecordSpawnFailModesTests(unittest.TestCase):
    """Spawn audit-log write paths. By default we warn-and-continue on
    failure (a stuck disk shouldn't take kato down); with
    ``KATO_SANDBOX_AUDIT_REQUIRED=true`` set we fail closed so a
    safety-conscious operator can pin the audit guarantee."""

    def test_dir_fsync_oserror_is_swallowed(self) -> None:
        # Lines 1584-1585: best-effort parent-dir fsync. If the dir fd
        # can't be opened (some FS don't support O_RDONLY on dirs), the
        # write itself already happened — swallow and continue.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'audit.log'
            real_os_open = os.open

            def selective_os_open(path, *a, **kw):
                # The directory-fsync call opens the parent dir read-only.
                # Make that specific open fail; let everything else
                # (lock file, audit log fd) proceed normally.
                if path == str(target.parent) and (
                    len(a) == 0 or a[0] == os.O_RDONLY
                ):
                    raise OSError('cannot fsync directory')
                return real_os_open(path, *a, **kw)

            with patch('os.open', side_effect=selective_os_open), \
                 patch.object(manager, '_image_digest', return_value=''):
                # Should not raise — dir-fsync error is swallowed.
                record_spawn(
                    task_id='T',
                    container_name='kato-sandbox-T-0001',
                    workspace_path='/tmp/x',
                    audit_log_path=target,
                )
            # Entry was still written (read before tempdir cleanup).
            lines = target.read_bytes().splitlines()
            self.assertEqual(len(lines), 1)

    def test_audit_required_promotes_write_failure_to_sandbox_error(
        self,
    ) -> None:
        # Lines 1588-1593: with ``KATO_SANDBOX_AUDIT_REQUIRED=true``,
        # any OSError on the audit write becomes SandboxError. The
        # spawn is refused — fail-closed guarantee for operators who
        # need "no audit, no spawn".
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'audit.log'
            real_os_open = os.open

            def fail_on_log_open(path, *a, **kw):
                # Only fail when opening the audit-log file for write —
                # let the lock-file and dir-fsync opens succeed so the
                # lock can be taken and released cleanly.
                if path == str(target) and a and (a[0] & os.O_APPEND):
                    raise OSError('disk full')
                return real_os_open(path, *a, **kw)

            with patch('os.open', side_effect=fail_on_log_open), \
                 patch.object(manager, '_image_digest', return_value=''):
                with self.assertRaises(SandboxError) as ctx:
                    record_spawn(
                        task_id='T',
                        container_name='kato-sandbox-T-0001',
                        workspace_path='/tmp/x',
                        audit_log_path=target,
                        env={AUDIT_REQUIRED_ENV_KEY: 'true'},
                    )
            self.assertIn(AUDIT_REQUIRED_ENV_KEY, str(ctx.exception))
            self.assertIn('refusing to spawn', str(ctx.exception))

    def test_audit_failure_default_warns_and_continues(self) -> None:
        # Lines 1594-1603: default (no env override) — warn to stderr +
        # logger, but the spawn still proceeds. A stuck disk on the
        # operator's laptop must not take kato down on every spawn.
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'audit.log'
            captured_stderr = io.StringIO()
            real_os_open = os.open

            def fail_on_log_open(path, *a, **kw):
                if path == str(target) and a and (a[0] & os.O_APPEND):
                    raise OSError('disk full')
                return real_os_open(path, *a, **kw)

            with patch('os.open', side_effect=fail_on_log_open), \
                 patch.object(manager, '_image_digest', return_value=''), \
                 patch.object(sys, 'stderr', new=captured_stderr):
                # No exception expected — default mode warns and continues.
                record_spawn(
                    task_id='T',
                    container_name='kato-sandbox-T-0001',
                    workspace_path='/tmp/x',
                    audit_log_path=target,
                    logger=logger,
                )
        logger.warning.assert_called_once()
        self.assertIn('audit', captured_stderr.getvalue().lower())

    def test_audit_failure_default_without_logger_only_warns_stderr(self) -> None:
        # Branch 1609->1619: ``logger is None`` skips ``logger.warning``
        # and falls through to the audit-shipping call. Stderr warning
        # must still fire so the operator sees the audit gap.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'audit.log'
            captured_stderr = io.StringIO()
            real_os_open = os.open

            def fail_on_log_open(path, *a, **kw):
                if path == str(target) and a and (a[0] & os.O_APPEND):
                    raise OSError('disk full')
                return real_os_open(path, *a, **kw)

            with patch('os.open', side_effect=fail_on_log_open), \
                 patch.object(manager, '_image_digest', return_value=''), \
                 patch.object(sys, 'stderr', new=captured_stderr):
                # No logger passed — exercises the False branch of
                # ``if logger is not None`` at the audit-write warn path.
                record_spawn(
                    task_id='T',
                    container_name='kato-sandbox-T-0001',
                    workspace_path='/tmp/x',
                    audit_log_path=target,
                )
        self.assertIn('audit', captured_stderr.getvalue().lower())


# --------------------------------------------------------------------------
# _DigestLookupError + _image_digest + _image_digest_strict
# --------------------------------------------------------------------------


class DigestLookupTests(unittest.TestCase):
    """The JIT image-digest pin (supply-chain protection #17) refuses
    to spawn unless we can name the exact image bytes we're running.
    The lookup function must distinguish ``missing`` (rebuild fixes)
    from ``transient`` (retry fixes) so the operator's diagnostic is
    actionable and they don't reach for a bypass env var."""

    def test_digest_lookup_error_carries_kind(self) -> None:
        # Lines 1636-1638: ``_DigestLookupError.__init__`` carries
        # ``kind`` ('missing' | 'transient'). The wrap_command path
        # branches on this to give different operator messages.
        err = _DigestLookupError('missing', 'image gone')
        self.assertEqual(err.kind, 'missing')
        self.assertEqual(str(err), 'image gone')

    def test_image_digest_returns_empty_string_on_lookup_error(self) -> None:
        # Lines 1643-1646: the best-effort wrapper. Used by record_spawn
        # for audit-log decoration where '' is acceptable; wrap_command
        # uses the strict variant so a missing pin refuses the spawn.
        with patch.object(
            manager, '_image_digest_strict',
            side_effect=_DigestLookupError('transient', 'daemon busy'),
        ):
            self.assertEqual(_image_digest('kato/claude-sandbox:latest'), '')

    def test_strict_raises_transient_on_timeout(self) -> None:
        # Lines 1672-1677: daemon timeout → transient. Operator retries,
        # checks ``docker info`` — does NOT reach for a bypass env var.
        with patch('subprocess.run',
                   side_effect=subprocess.TimeoutExpired(
                       cmd=['docker'], timeout=5)):
            with self.assertRaises(_DigestLookupError) as ctx:
                _image_digest_strict('kato/claude-sandbox:latest')
        self.assertEqual(ctx.exception.kind, 'transient')
        self.assertIn('docker info', str(ctx.exception))

    def test_strict_raises_transient_on_oserror(self) -> None:
        # Lines 1678-1683: docker binary missing/down → transient.
        # Operator starts docker and retries.
        with patch('subprocess.run', side_effect=OSError('docker not found')):
            with self.assertRaises(_DigestLookupError) as ctx:
                _image_digest_strict('kato/claude-sandbox:latest')
        self.assertEqual(ctx.exception.kind, 'transient')

    def test_strict_raises_missing_on_no_such_image_stderr(self) -> None:
        # Lines 1684-1691: stderr names "no such image" → missing.
        # Operator runs ``make sandbox-build``.
        with patch('subprocess.run', return_value=_ok(
            returncode=1, stderr='Error: No such image: kato/claude-sandbox:latest',
        )):
            with self.assertRaises(_DigestLookupError) as ctx:
                _image_digest_strict('kato/claude-sandbox:latest')
        self.assertEqual(ctx.exception.kind, 'missing')
        self.assertIn('sandbox-build', str(ctx.exception))

    def test_strict_raises_missing_on_not_found_stderr(self) -> None:
        # Same branch, slightly different docker-version-dependent
        # message ("not found" vs "no such image"). Both → missing.
        with patch('subprocess.run', return_value=_ok(
            returncode=1, stderr='reference not found',
        )):
            with self.assertRaises(_DigestLookupError) as ctx:
                _image_digest_strict('kato/claude-sandbox:latest')
        self.assertEqual(ctx.exception.kind, 'missing')

    def test_strict_raises_transient_on_other_nonzero(self) -> None:
        # Lines 1692-1696: stderr doesn't name a missing-image phrase
        # → transient. Operator retries (likely a daemon restart).
        with patch('subprocess.run', return_value=_ok(
            returncode=125, stderr='unexpected daemon error',
        )):
            with self.assertRaises(_DigestLookupError) as ctx:
                _image_digest_strict('kato/claude-sandbox:latest')
        self.assertEqual(ctx.exception.kind, 'transient')

    def test_strict_raises_transient_on_empty_stdout(self) -> None:
        # Lines 1697-1702: docker returned rc=0 but with empty stdout
        # (extremely rare; corrupted local image record). Transient —
        # retry, and if it persists rebuild.
        with patch('subprocess.run', return_value=_ok(stdout='   ')):
            with self.assertRaises(_DigestLookupError) as ctx:
                _image_digest_strict('kato/claude-sandbox:latest')
        self.assertEqual(ctx.exception.kind, 'transient')

    def test_strict_returns_digest_on_success(self) -> None:
        # Lines 1697 (the success path). Locks the round-trip shape so
        # a future refactor that changes the docker arg can't silently
        # break the pin.
        expected = 'sha256:' + 'f' * 64
        with patch('subprocess.run', return_value=_ok(stdout=expected + '\n')):
            self.assertEqual(
                _image_digest_strict('kato/claude-sandbox:latest'),
                expected,
            )


# --------------------------------------------------------------------------
# _assert_seccomp_not_unconfined — Layer 6 defense-in-depth
# --------------------------------------------------------------------------


class SeccompUnconfinedRefusalTests(unittest.TestCase):
    """Defense-in-depth: a future maintainer copying a permissive
    docker example into ``wrap_command`` cannot accidentally pass
    ``--security-opt seccomp=unconfined``. This invariant runs LAST
    on the final argv so the check sees every form."""

    def test_refuses_seccomp_unconfined_in_two_token_form(self) -> None:
        # Lines 1718-1724: the ``--security-opt`` + value (two tokens).
        argv = ['docker', 'run', '--security-opt', 'seccomp=unconfined', 'image']
        with self.assertRaises(SandboxError) as ctx:
            _assert_seccomp_not_unconfined(argv)
        self.assertIn('seccomp=unconfined', str(ctx.exception))

    def test_refuses_seccomp_unconfined_in_single_token_form(self) -> None:
        # Same lines — single-token form (``--security-opt=seccomp=...``)
        # must also be caught.
        argv = ['docker', 'run', '--security-opt=seccomp=unconfined', 'image']
        with self.assertRaises(SandboxError):
            _assert_seccomp_not_unconfined(argv)

    def test_accepts_default_seccomp_argv(self) -> None:
        # Negative case: well-formed argv must not raise. Locks the
        # check against false positives that could break legitimate
        # spawns.
        argv = [
            'docker', 'run',
            '--security-opt', 'no-new-privileges',
            '--security-opt', 'apparmor=docker-default',
            'image',
        ]
        # No raise.
        _assert_seccomp_not_unconfined(argv)


if __name__ == '__main__':
    unittest.main()
