"""Tests for the TLS-pin lifecycle (OG4) — TOFU + env-var + opt-out.

Closes the rogue-CA / cert-mis-issuance residual. The runtime egress
firewall already restricts to ``api.anthropic.com:443``, but the TLS
handshake validates against the system CA store — a compromised or
compelled CA could mint a valid cert for the host. Pinning binds the
trust decision to a specific SPKI fingerprint instead.

The lifecycle has four cases on every kato startup:

  1. **Env var pin** — ``KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256`` holds
     one or more comma-separated base64 SHA-256 SPKI fingerprints.
     Match → silent. Mismatch → refuse.
  2. **Opt-out** — ``KATO_SANDBOX_ALLOW_NO_TLS_PIN=true`` skips the
     pin entirely and prints a yellow warning every startup.
  3. **First run** — neither env var nor saved file. Connect, extract
     SPKI, save to ``~/.kato/anthropic-tls-pin``, print yellow box,
     continue.
  4. **Subsequent run** — file exists. Read fingerprint, compare to
     live cert. Match → silent. Mismatch → refuse with full context.

Edge cases: network unreachable (first run / subsequent run), file
unreadable, file malformed, parent dir uncreatable, both env vars
set. Each refuses with an operator-actionable message.

Color: ANSI yellow on TTY, no codes when stderr is redirected.
"""

from __future__ import annotations

import io
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sandbox_core_lib.sandbox_core_lib.tls_pin import (
    TlsPinError,
    _format_first_run_box,
    _read_pin_file,
    _save_pin_file,
    is_pinning_enabled,
    validate_anthropic_tls_pin_or_refuse,
)


# Two arbitrary base64-SHA256 strings shaped like real SPKI pins.
# 32 bytes of A's, B's, C's encoded as base64 → 44-char padded base64
# strings that decode to exactly 32 bytes (SHA-256 output size).
_FAKE_PRIMARY_PIN = 'QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE='  # b'A' * 32
_FAKE_BACKUP_PIN = 'QkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkI='   # b'B' * 32
_FAKE_WRONG_FINGERPRINT = 'Q0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0M='  # b'C' * 32


class _TtyStringIO(io.StringIO):
    """Fake stderr that reports as a TTY — for color-output tests."""

    def isatty(self) -> bool:
        return True


class _NonTtyStringIO(io.StringIO):
    """Fake stderr that reports as NOT a TTY — for non-color tests."""

    def isatty(self) -> bool:
        return False


def _temp_pin_path() -> Path:
    """Disposable path under a temp dir. Caller cleans up the parent."""
    td = tempfile.mkdtemp()
    return Path(td) / '.kato' / 'anthropic-tls-pin'


# --------------------------------------------------------------------------
# Predicate (legacy)
# --------------------------------------------------------------------------


class IsPinningEnabledTests(unittest.TestCase):
    def test_no_env_means_disabled(self) -> None:
        self.assertFalse(is_pinning_enabled({}))

    def test_empty_env_means_disabled(self) -> None:
        self.assertFalse(
            is_pinning_enabled({'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': ''})
        )

    def test_single_pin_means_enabled(self) -> None:
        self.assertTrue(
            is_pinning_enabled({
                'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN,
            }),
        )


# --------------------------------------------------------------------------
# Case 1 — env-var pin
# --------------------------------------------------------------------------


class Case1EnvVarPinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stderr = _NonTtyStringIO()
        self.pin_path = _temp_pin_path()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        if self.pin_path.parent.exists():
            for child in self.pin_path.parent.iterdir():
                child.unlink()
            self.pin_path.parent.rmdir()
        if self.pin_path.parent.parent.exists():
            try:
                self.pin_path.parent.parent.rmdir()
            except OSError:
                pass

    def test_matching_env_var_pin_passes_silently(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        # Silent success — no stderr output.
        self.assertEqual(self.stderr.getvalue(), '')

    def test_backup_pin_in_list_also_matches(self) -> None:
        # Operator lists primary,backup. Either match passes.
        validate_anthropic_tls_pin_or_refuse(
            env={
                'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256':
                    f'{_FAKE_PRIMARY_PIN},{_FAKE_BACKUP_PIN}',
            },
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_BACKUP_PIN,
            pin_file_path=self.pin_path,
        )
        self.assertEqual(self.stderr.getvalue(), '')

    def test_mismatch_raises_with_full_refusal(self) -> None:
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN},
                stderr=self.stderr,
                fetch_live_fingerprint=lambda: _FAKE_WRONG_FINGERPRINT,
                pin_file_path=self.pin_path,
            )
        # Short summary in the exception (for logger.error).
        self.assertIn('mismatch', str(cm.exception).lower())
        # Full refusal on stderr — names both fingerprints + recovery.
        out = self.stderr.getvalue()
        self.assertIn('TLS PIN MISMATCH', out)
        self.assertIn(_FAKE_PRIMARY_PIN, out)
        self.assertIn(_FAKE_WRONG_FINGERPRINT, out)
        # Env-var origin recovery names the env var, not the file.
        self.assertIn('KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256', out)
        # OG4 doc ref present.
        self.assertIn('OG4', out)

    def test_env_var_wins_over_existing_file(self) -> None:
        # Pre-create a file with a DIFFERENT pin. Env var overrides.
        self.pin_path.parent.mkdir(parents=True, exist_ok=True)
        self.pin_path.write_text(
            f'{_FAKE_BACKUP_PIN}\n# pinned: 2026-01-01T00:00:00+00:00\n'
        )
        validate_anthropic_tls_pin_or_refuse(
            env={'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        # File still exists (env var wins but doesn't delete file).
        self.assertTrue(self.pin_path.exists())
        # Info note printed.
        out = self.stderr.getvalue()
        self.assertIn('TLS pin loaded from env var', out)
        self.assertIn('ignored', out)

    def test_network_failure_with_env_var_refuses(self) -> None:
        def _raise() -> str:
            raise OSError('connection refused')
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN},
                stderr=self.stderr,
                fetch_live_fingerprint=_raise,
                pin_file_path=self.pin_path,
            )
        self.assertIn('Cannot reach', str(cm.exception))
        self.assertIn('Cannot reach', self.stderr.getvalue())


# --------------------------------------------------------------------------
# Case 2 — opt-out
# --------------------------------------------------------------------------


class Case2OptOutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stderr = _NonTtyStringIO()
        self.pin_path = _temp_pin_path()

    def test_optout_returns_silently_with_warning_on_stderr(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={'KATO_SANDBOX_ALLOW_NO_TLS_PIN': 'true'},
            stderr=self.stderr,
            pin_file_path=self.pin_path,
        )
        out = self.stderr.getvalue()
        # Warning text is verbatim per the spec.
        self.assertIn('TLS pin disabled', out)
        self.assertIn('KATO_SANDBOX_ALLOW_NO_TLS_PIN=true', out)
        self.assertIn('Rogue-CA', out)
        self.assertIn('OG4', out)

    def test_optout_does_not_call_fetch_live(self) -> None:
        called: list[bool] = []

        def _fetch() -> str:
            called.append(True)
            return _FAKE_PRIMARY_PIN

        validate_anthropic_tls_pin_or_refuse(
            env={'KATO_SANDBOX_ALLOW_NO_TLS_PIN': 'true'},
            stderr=self.stderr,
            fetch_live_fingerprint=_fetch,
            pin_file_path=self.pin_path,
        )
        # The whole point of opt-out is to skip the network call.
        self.assertEqual(called, [])

    def test_optout_does_not_create_pin_file(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={'KATO_SANDBOX_ALLOW_NO_TLS_PIN': 'true'},
            stderr=self.stderr,
            pin_file_path=self.pin_path,
        )
        self.assertFalse(self.pin_path.exists())


# --------------------------------------------------------------------------
# Case 3 — first run (TOFU)
# --------------------------------------------------------------------------


class Case3FirstRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stderr = _NonTtyStringIO()
        self.pin_path = _temp_pin_path()

    def test_first_run_pins_and_saves_to_file(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        # File created with the live fingerprint.
        self.assertTrue(self.pin_path.exists())
        text = self.pin_path.read_text()
        self.assertTrue(text.startswith(_FAKE_PRIMARY_PIN))
        self.assertIn('# pinned:', text)

    def test_first_run_file_has_mode_0600(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        # Strip the file-type bits so we just compare permissions.
        mode = stat.S_IMODE(os.stat(self.pin_path).st_mode)
        self.assertEqual(
            mode, 0o600,
            f'pin file mode {oct(mode)} != 0o600',
        )

    def test_first_run_parent_dir_has_mode_0700(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        mode = stat.S_IMODE(os.stat(self.pin_path.parent).st_mode)
        self.assertEqual(
            mode, 0o700,
            f'parent dir mode {oct(mode)} != 0o700',
        )

    def test_first_run_prints_yellow_box(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        out = self.stderr.getvalue()
        # Box characters present.
        self.assertIn('╔', out)
        self.assertIn('╚', out)
        self.assertIn('║', out)
        # Title verbatim.
        self.assertIn('TLS PIN — First run', out)
        # OG4 doc ref present.
        self.assertIn('OG4', out)

    def test_first_run_box_emits_color_on_tty(self) -> None:
        tty_stderr = _TtyStringIO()
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=tty_stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        out = tty_stderr.getvalue()
        # ANSI yellow + reset escapes wrap the message on TTY.
        self.assertIn('\033[33m', out)
        self.assertIn('\033[0m', out)

    def test_first_run_no_color_on_non_tty(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        out = self.stderr.getvalue()
        # No ANSI escapes when stderr is not a TTY (CI / pipes).
        self.assertNotIn('\033[', out)

    def test_first_run_uses_provided_now_for_timestamp(self) -> None:
        fixed_now = datetime(2026, 5, 3, 12, 30, 45, tzinfo=timezone.utc)
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
            now=lambda: fixed_now,
        )
        text = self.pin_path.read_text()
        self.assertIn('2026-05-03T12:30:45+00:00', text)

    def test_first_run_network_failure_refuses_without_writing_file(self) -> None:
        def _raise() -> str:
            raise OSError('DNS lookup failed')
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=self.stderr,
                fetch_live_fingerprint=_raise,
                pin_file_path=self.pin_path,
            )
        # Spec-mandated phrasing: cannot reach + establish.
        self.assertIn('Cannot reach', str(cm.exception))
        self.assertIn('establish', str(cm.exception))
        # No placeholder file written when we can't determine the pin.
        self.assertFalse(self.pin_path.exists())

    def test_first_run_save_failure_refuses_with_path(self) -> None:
        # Set the path to a location where save will fail. The
        # parent of the parent doesn't exist and isn't writable —
        # but ``mkdir(parents=True)`` would normally succeed under
        # /tmp. Force the failure by pointing at a path under a
        # regular file instead of a directory.
        with tempfile.NamedTemporaryFile() as f:
            bad_path = Path(f.name) / 'subdir' / 'pin'
            with self.assertRaises(TlsPinError) as cm:
                validate_anthropic_tls_pin_or_refuse(
                    env={},
                    stderr=self.stderr,
                    fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
                    pin_file_path=bad_path,
                )
            self.assertIn('Cannot save TLS pin', str(cm.exception))


# --------------------------------------------------------------------------
# Case 4 — subsequent run (file exists)
# --------------------------------------------------------------------------


class Case4SubsequentRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stderr = _NonTtyStringIO()
        self.pin_path = _temp_pin_path()
        self.pin_path.parent.mkdir(parents=True, exist_ok=True)

    def _write_pin_file(self, fingerprint: str, *, pinned_at: str = '2026-01-15T08:00:00+00:00') -> None:
        self.pin_path.write_text(
            f'{fingerprint}\n# pinned: {pinned_at}\n'
        )

    def test_match_returns_silently(self) -> None:
        self._write_pin_file(_FAKE_PRIMARY_PIN)
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        self.assertEqual(self.stderr.getvalue(), '')

    def test_mismatch_refuses_with_full_context(self) -> None:
        self._write_pin_file(_FAKE_PRIMARY_PIN, pinned_at='2026-01-15T08:00:00+00:00')
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=self.stderr,
                fetch_live_fingerprint=lambda: _FAKE_WRONG_FINGERPRINT,
                pin_file_path=self.pin_path,
            )
        self.assertIn('mismatch', str(cm.exception).lower())
        out = self.stderr.getvalue()
        # All four operator-actionable pieces present.
        self.assertIn('TLS PIN MISMATCH', out)
        self.assertIn(_FAKE_PRIMARY_PIN, out)         # saved pin
        self.assertIn(_FAKE_WRONG_FINGERPRINT, out)   # live pin
        self.assertIn('2026-01-15T08:00:00+00:00', out)  # pinned-at timestamp
        # Recovery names the file path with rm.
        self.assertIn('rm', out)
        self.assertIn('anthropic-tls-pin', out)

    def test_mismatch_message_distinguishes_expected_vs_unexpected(self) -> None:
        # The two-branch interpretation is what makes the message
        # operator-actionable. Without it the message reads like an
        # error code, not a decision tree.
        self._write_pin_file(_FAKE_PRIMARY_PIN)
        with self.assertRaises(TlsPinError):
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=self.stderr,
                fetch_live_fingerprint=lambda: _FAKE_WRONG_FINGERPRINT,
                pin_file_path=self.pin_path,
            )
        out = self.stderr.getvalue()
        self.assertIn('If you EXPECTED this', out)
        self.assertIn('If you did NOT expect this', out)
        # The "trusted source" cross-check guidance is the load-bearing
        # bit — it's the difference between cargo-cult re-pinning and
        # actually catching a MITM.
        self.assertIn('trusted source', out)

    def test_network_failure_on_subsequent_run_refuses(self) -> None:
        self._write_pin_file(_FAKE_PRIMARY_PIN)

        def _raise() -> str:
            raise OSError('network unreachable')

        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=self.stderr,
                fetch_live_fingerprint=_raise,
                pin_file_path=self.pin_path,
            )
        # Spec: refuse with "Cannot reach" + "verify" message.
        self.assertIn('Cannot reach', str(cm.exception))
        self.assertIn('verify', str(cm.exception))

    def test_unreadable_file_refuses_with_path_and_remediation(self) -> None:
        self._write_pin_file(_FAKE_PRIMARY_PIN)
        # Strip read permission so the read raises PermissionError
        # (which is an OSError subclass).
        os.chmod(self.pin_path, 0)
        try:
            # On macOS / Linux as a regular user, mode 0 means
            # PermissionError. As root the chmod is ignored — skip
            # the test rather than assert wrong behavior on root.
            try:
                self.pin_path.read_text()
            except (PermissionError, OSError):
                pass
            else:
                self.skipTest(
                    'running as a user that can read mode-0 files (root?)'
                )
            with self.assertRaises(TlsPinError) as cm:
                validate_anthropic_tls_pin_or_refuse(
                    env={},
                    stderr=self.stderr,
                    fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
                    pin_file_path=self.pin_path,
                )
            self.assertIn('cannot be read', str(cm.exception))
            self.assertIn('Delete and re-run', str(cm.exception))
        finally:
            # Restore so cleanup can delete it.
            os.chmod(self.pin_path, 0o600)

    def test_malformed_file_refuses_with_remediation(self) -> None:
        # First line is not valid base64.
        self.pin_path.write_text('not-valid-base64-#@!\n')
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=self.stderr,
                fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
                pin_file_path=self.pin_path,
            )
        self.assertIn('malformed', str(cm.exception))
        self.assertIn('Delete and re-run', str(cm.exception))

    def test_empty_file_refuses_as_malformed(self) -> None:
        self.pin_path.write_text('')
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=self.stderr,
                fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
                pin_file_path=self.pin_path,
            )
        self.assertIn('malformed', str(cm.exception))

    def test_wrong_length_decoded_fingerprint_refuses_as_malformed(self) -> None:
        # 16 bytes of A's is valid base64 but wrong length for SHA-256.
        short_fingerprint = 'QUFBQUFBQUFBQUFBQUFBQQ=='  # b'A' * 16
        self.pin_path.write_text(f'{short_fingerprint}\n# pinned: x\n')
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=self.stderr,
                fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
                pin_file_path=self.pin_path,
            )
        self.assertIn('malformed', str(cm.exception))


# --------------------------------------------------------------------------
# Edge cases — ambiguous configuration
# --------------------------------------------------------------------------


class EdgeCaseAmbiguousConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stderr = _NonTtyStringIO()
        self.pin_path = _temp_pin_path()

    def test_env_var_and_optout_both_set_refuses(self) -> None:
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={
                    'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN,
                    'KATO_SANDBOX_ALLOW_NO_TLS_PIN': 'true',
                },
                stderr=self.stderr,
                pin_file_path=self.pin_path,
            )
        # Names both env vars and the disambiguation.
        msg = str(cm.exception)
        self.assertIn('KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256', msg)
        self.assertIn('KATO_SANDBOX_ALLOW_NO_TLS_PIN', msg)
        self.assertIn('Pick one', msg)


# --------------------------------------------------------------------------
# File format helpers — round-trip
# --------------------------------------------------------------------------


class PinFileFormatTests(unittest.TestCase):
    """Round-trip ``_save_pin_file`` ↔ ``_read_pin_file``.

    Locks the on-disk format so a future refactor can't silently
    change the parser without updating the writer (or vice versa).
    """

    def setUp(self) -> None:
        self.pin_path = _temp_pin_path()

    def test_save_then_read_round_trips_fingerprint(self) -> None:
        fixed_now = datetime(2026, 5, 3, 12, 30, 45, tzinfo=timezone.utc)
        _save_pin_file(self.pin_path, _FAKE_PRIMARY_PIN, now=lambda: fixed_now)
        fingerprint, pinned_at = _read_pin_file(self.pin_path)
        self.assertEqual(fingerprint, _FAKE_PRIMARY_PIN)
        self.assertEqual(pinned_at, '2026-05-03T12:30:45+00:00')

    def test_file_format_first_line_is_fingerprint(self) -> None:
        _save_pin_file(self.pin_path, _FAKE_PRIMARY_PIN)
        first_line = self.pin_path.read_text().splitlines()[0]
        self.assertEqual(first_line, _FAKE_PRIMARY_PIN)

    def test_file_format_second_line_is_pinned_comment(self) -> None:
        _save_pin_file(self.pin_path, _FAKE_PRIMARY_PIN)
        second_line = self.pin_path.read_text().splitlines()[1]
        self.assertTrue(second_line.startswith('# pinned:'))

    def test_read_pin_file_returns_none_when_no_pinned_comment(self) -> None:
        # Branch 284->289: the loop walks ``lines[1:]`` and exits
        # without ever hitting a ``# pinned:`` line — ``pinned_at``
        # stays ``None`` and the parser returns successfully. Locks the
        # backward-compat case where a hand-written file omits the
        # timestamp comment entirely.
        self.pin_path.parent.mkdir(parents=True, exist_ok=True)
        self.pin_path.write_text(f'{_FAKE_PRIMARY_PIN}\n# unrelated note\n')
        fingerprint, pinned_at = _read_pin_file(self.pin_path)
        self.assertEqual(fingerprint, _FAKE_PRIMARY_PIN)
        self.assertIsNone(pinned_at)

    def test_read_pin_file_skips_non_pinned_comment_lines(self) -> None:
        # Branch 286->284: a comment line that does NOT start with
        # ``# pinned:`` must be skipped (loop continues to next line)
        # rather than aborting the parse. Locks tolerance for inline
        # operator notes above the timestamp.
        self.pin_path.parent.mkdir(parents=True, exist_ok=True)
        self.pin_path.write_text(
            f'{_FAKE_PRIMARY_PIN}\n'
            '# operator note: rotated 2026-01-01\n'
            '# pinned: 2026-02-02T03:04:05+00:00\n'
        )
        fingerprint, pinned_at = _read_pin_file(self.pin_path)
        self.assertEqual(fingerprint, _FAKE_PRIMARY_PIN)
        self.assertEqual(pinned_at, '2026-02-02T03:04:05+00:00')

    def test_first_run_box_truncates_overlong_path(self) -> None:
        # Line 323: when a row's text exceeds the 66-char inner width
        # (e.g. an unusually deep ``Saved to:`` path on a CI runner),
        # it must be truncated to fit the boxed banner rather than
        # blowing the layout. Locks the truncate branch of ``row()``.
        long_path = Path('/' + 'long_directory_segment/' * 6 + 'pin')
        box = _format_first_run_box(long_path)
        # Banner top/bottom rules stay intact (inner width = 66).
        self.assertIn('═' * 66, box)
        # Every emitted line stays the same width — no overflow.
        body_lines = [
            line for line in box.split('\n')
            if line.startswith('║') and line.endswith('║')
        ]
        self.assertTrue(body_lines)  # sanity: rows were emitted
        widths = {len(line) for line in body_lines}
        self.assertEqual(widths, {68})  # 66 inner + two ║ borders


# --------------------------------------------------------------------------
# OG4 defensive branches — every protection-critical path must fail closed
# or degrade safely without weakening the pin contract.
# --------------------------------------------------------------------------


class TlsPinDefensiveBranchTests(unittest.TestCase):
    """OG4 TLS-pinning security: defensive paths that must fail-closed
    or degrade gracefully without weakening the pin contract.

    Each test names the exact protection-critical branch it locks down.
    Lines covered: 111, 130-144, 159-165, 185, 188-189, 210-211, 239-243,
    272, 367-368, 418-419, 424, 534.
    """

    def test_default_pin_file_path_lives_under_home(self) -> None:
        # Line 111: ``_default_pin_file_path`` — operator-visible
        # location for the TOFU pin file. Locked so a refactor can't
        # silently move the pin file to a less-private location.
        from sandbox_core_lib.sandbox_core_lib.tls_pin import (
            _default_pin_file_path,
        )
        path = _default_pin_file_path()
        self.assertEqual(path.name, 'anthropic-tls-pin')
        self.assertEqual(path.parent.name, '.kato')

    def test_spki_fingerprint_falls_back_to_whole_cert_when_cryptography_missing(
        self,
    ) -> None:
        # Lines 139-143: when the ``cryptography`` package isn't
        # installed, hash the whole DER cert instead. This preserves
        # the security property ("trust THIS byte sequence, not any
        # CA-signed cert") even though the pin breaks on rotation.
        import builtins
        from unittest.mock import patch
        from sandbox_core_lib.sandbox_core_lib.tls_pin import (
            _spki_fingerprint_from_der_cert,
        )
        real_import = builtins.__import__

        def fail_cryptography(name, *args, **kwargs):
            if name.startswith('cryptography'):
                raise ImportError('mocked: cryptography not installed')
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, '__import__', fail_cryptography):
            fingerprint = _spki_fingerprint_from_der_cert(
                b'\x00fake-der-cert-bytes',
            )
        # Output shape is preserved: 44-char base64 SHA-256.
        self.assertEqual(len(fingerprint), 44)
        # Deterministic: the same DER bytes must hash to the same pin.
        import base64
        import hashlib
        expected = base64.b64encode(
            hashlib.sha256(b'\x00fake-der-cert-bytes').digest(),
        ).decode('ascii')
        self.assertEqual(fingerprint, expected)

    def test_spki_fingerprint_uses_cryptography_when_available(self) -> None:
        # Lines 130-138: the normal SPKI extraction path. Pin computed
        # from the cert's public key (SPKI), not the whole DER cert —
        # so the pin survives routine cert rotation.
        try:
            from cryptography import x509  # noqa: F401
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography import x509 as _x509
            from datetime import datetime, timedelta, timezone
        except ImportError:
            self.skipTest('cryptography not installed')

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = _x509.Name([
            _x509.NameAttribute(_x509.NameOID.COMMON_NAME, 'test'),
        ])
        cert = (
            _x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(_x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        der = cert.public_bytes(serialization.Encoding.DER)

        from sandbox_core_lib.sandbox_core_lib.tls_pin import (
            _spki_fingerprint_from_der_cert,
        )
        spki_pin = _spki_fingerprint_from_der_cert(der)
        # SPKI path used: re-hashing the WHOLE cert yields a different
        # value (proves the function extracted the SPKI, not the cert).
        import base64
        import hashlib
        whole_cert_hash = base64.b64encode(
            hashlib.sha256(der).digest(),
        ).decode('ascii')
        self.assertNotEqual(spki_pin, whole_cert_hash)
        self.assertEqual(len(spki_pin), 44)

    def test_fetch_live_spki_fingerprint_opens_tls_and_extracts(self) -> None:
        # Lines 159-165: the live-fetch glue. Mocks ssl/socket so no
        # real network call, but exercises the connect→wrap→getpeercert
        # plumbing that feeds the live pin into the lifecycle.
        from unittest.mock import MagicMock, patch
        from sandbox_core_lib.sandbox_core_lib.tls_pin import (
            _fetch_live_spki_fingerprint,
        )

        fake_der = b'\x00fake-peer-cert-der'
        fake_tls = MagicMock()
        fake_tls.getpeercert.return_value = fake_der
        fake_tls.__enter__ = MagicMock(return_value=fake_tls)
        fake_tls.__exit__ = MagicMock(return_value=False)

        fake_raw = MagicMock()
        fake_raw.__enter__ = MagicMock(return_value=fake_raw)
        fake_raw.__exit__ = MagicMock(return_value=False)

        fake_ctx = MagicMock()
        fake_ctx.wrap_socket.return_value = fake_tls

        with patch('sandbox_core_lib.sandbox_core_lib.tls_pin.ssl.create_default_context',
                   return_value=fake_ctx), \
             patch('sandbox_core_lib.sandbox_core_lib.tls_pin.socket.create_connection',
                   return_value=fake_raw), \
             patch(
                 'sandbox_core_lib.sandbox_core_lib.tls_pin._spki_fingerprint_from_der_cert',
                 return_value=_FAKE_PRIMARY_PIN,
             ) as fake_hash:
            result = _fetch_live_spki_fingerprint(
                host='example.com', port=443, timeout=1.0,
            )
        # Glue passes the live DER bytes through to the hash function.
        fake_hash.assert_called_once_with(fake_der)
        self.assertEqual(result, _FAKE_PRIMARY_PIN)

    def test_fetch_live_spki_fingerprint_raises_oserror_on_empty_cert(
        self,
    ) -> None:
        # Lines 163-164: empty getpeercert → OSError. Critical: if the
        # peer somehow returns no cert, we must refuse rather than
        # silently pin the empty bytes.
        from unittest.mock import MagicMock, patch
        from sandbox_core_lib.sandbox_core_lib.tls_pin import (
            _fetch_live_spki_fingerprint,
        )

        fake_tls = MagicMock()
        fake_tls.getpeercert.return_value = None
        fake_tls.__enter__ = MagicMock(return_value=fake_tls)
        fake_tls.__exit__ = MagicMock(return_value=False)
        fake_raw = MagicMock()
        fake_raw.__enter__ = MagicMock(return_value=fake_raw)
        fake_raw.__exit__ = MagicMock(return_value=False)
        fake_ctx = MagicMock()
        fake_ctx.wrap_socket.return_value = fake_tls

        with patch('sandbox_core_lib.sandbox_core_lib.tls_pin.ssl.create_default_context',
                   return_value=fake_ctx), \
             patch('sandbox_core_lib.sandbox_core_lib.tls_pin.socket.create_connection',
                   return_value=fake_raw):
            with self.assertRaises(OSError) as ctx:
                _fetch_live_spki_fingerprint(
                    host='example.com', port=443, timeout=1.0,
                )
        self.assertIn('no peer cert', str(ctx.exception))

    def test_is_tty_false_when_stream_has_no_isatty(self) -> None:
        # Line 185: stream lacking ``isatty`` attribute → False. Used
        # so we don't crash when stderr is a custom buffer.
        from sandbox_core_lib.sandbox_core_lib.tls_pin import _is_tty

        class _NoIsatty:
            pass

        self.assertFalse(_is_tty(_NoIsatty()))

    def test_is_tty_false_when_isatty_raises_valueerror(self) -> None:
        # Lines 188-189: closed/odd streams raise on isatty() — must
        # be swallowed so kato startup banners never crash. Without
        # this swallow, a closed stderr in CI containers could prevent
        # the security warning from rendering at all.
        from sandbox_core_lib.sandbox_core_lib.tls_pin import _is_tty

        class _RaisesValueError:
            def isatty(self):
                raise ValueError('I/O operation on closed file')

        self.assertFalse(_is_tty(_RaisesValueError()))

    def test_is_tty_false_when_isatty_raises_oserror(self) -> None:
        # Line 188-189: also swallows OSError (Bad file descriptor).
        from sandbox_core_lib.sandbox_core_lib.tls_pin import _is_tty

        class _RaisesOSError:
            def isatty(self):
                raise OSError(9, 'Bad file descriptor')

        self.assertFalse(_is_tty(_RaisesOSError()))

    def test_write_stderr_swallows_oserror(self) -> None:
        # Lines 210-211: ``_write_stderr`` must swallow OSError so a
        # broken stderr never crashes the security message path. If
        # the warning can't be displayed, the operator still gets the
        # TlsPinError exception — but kato must not die mid-write.
        from sandbox_core_lib.sandbox_core_lib.tls_pin import _write_stderr

        class _BrokenStream:
            def write(self, _text):
                raise OSError('disk full')

            def flush(self):
                pass

        # Must not raise.
        _write_stderr('x', _BrokenStream())

    def test_write_stderr_swallows_valueerror(self) -> None:
        # Lines 210-211: also swallows ValueError (closed stream).
        from sandbox_core_lib.sandbox_core_lib.tls_pin import _write_stderr

        class _ClosedStream:
            def write(self, _text):
                raise ValueError('I/O operation on closed file')

            def flush(self):
                pass

        _write_stderr('x', _ClosedStream())

    def test_write_stderr_falls_back_to_sys_stderr_when_none(self) -> None:
        # Line 206: ``stderr=None`` falls back to ``sys.stderr`` so the
        # security message always has somewhere to go even when the
        # caller forgot to pass an explicit stream.
        import sys
        from unittest.mock import patch
        from sandbox_core_lib.sandbox_core_lib.tls_pin import _write_stderr

        with patch.object(sys, 'stderr', new=io.StringIO()) as fake:
            _write_stderr('hello', None)
        self.assertEqual(fake.getvalue(), 'hello')

    def test_save_pin_file_swallows_chmod_oserror(self) -> None:
        # Lines 239-243: chmod failure on the parent dir (some
        # filesystems silently reject chmod — network mounts, WSL
        # paths to Windows-side drives) must NOT abort the save.
        # The pin data isn't a secret; it's operator-private metadata.
        # Aborting here would prevent first-run TOFU on those FSes.
        from unittest.mock import patch
        path = _temp_pin_path()
        with patch(
            'sandbox_core_lib.sandbox_core_lib.tls_pin.os.chmod',
            side_effect=OSError('chmod not supported'),
        ):
            _save_pin_file(path, _FAKE_PRIMARY_PIN)
        # File still written despite chmod failure.
        self.assertTrue(path.exists())
        first_line = path.read_text().splitlines()[0]
        self.assertEqual(first_line, _FAKE_PRIMARY_PIN)

    def test_read_pin_file_rejects_empty_first_line(self) -> None:
        # Line 272: first line is blank after strip() → ValueError.
        # Prevents an attacker from creating a pin file whose first
        # line is whitespace, which would otherwise sneak past the
        # 'not lines' check and fail later in a less-obvious way.
        path = _temp_pin_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('   \nsome trailing line\n')
        with self.assertRaises(ValueError) as ctx:
            _read_pin_file(path)
        self.assertIn('no fingerprint', str(ctx.exception))

    def test_mismatch_refusal_lists_all_pins_when_multiple_configured(
        self,
    ) -> None:
        # Lines 367-368: env-var pin can be a comma-separated list
        # (primary + backup). The mismatch refusal must enumerate
        # ALL configured pins so the operator can compare. If we
        # showed only the first, the operator couldn't tell which
        # backup, if any, was supposed to match.
        env = {
            _PIN_ENV_KEY: f'{_FAKE_PRIMARY_PIN},{_FAKE_BACKUP_PIN}',
        }
        stderr = _NonTtyStringIO()
        with self.assertRaises(TlsPinError):
            validate_anthropic_tls_pin_or_refuse(
                env=env,
                stderr=stderr,
                fetch_live_fingerprint=lambda: _FAKE_WRONG_FINGERPRINT,
                pin_file_path=_temp_pin_path(),
            )
        message = stderr.getvalue()
        # Both saved pins enumerated.
        self.assertIn(_FAKE_PRIMARY_PIN, message)
        self.assertIn(_FAKE_BACKUP_PIN, message)
        # Live (wrong) fingerprint named so operator can investigate.
        self.assertIn(_FAKE_WRONG_FINGERPRINT, message)
        # 'Saved pins' plural header used (not 'Saved pin').
        self.assertIn('Saved pins:', message)

    def test_tilde_path_falls_back_when_home_unavailable(self) -> None:
        # Lines 418-419: Path.home() can raise RuntimeError or
        # KeyError in odd environments (no $HOME, no user db).
        # Must not crash — fall back to str(path).
        from unittest.mock import patch
        from sandbox_core_lib.sandbox_core_lib.tls_pin import _tilde_path
        path = Path('/some/absolute/path')
        with patch(
            'sandbox_core_lib.sandbox_core_lib.tls_pin.Path.home',
            side_effect=RuntimeError("no $HOME"),
        ):
            result = _tilde_path(path)
        self.assertEqual(result, '/some/absolute/path')

    def test_tilde_path_falls_back_when_home_keyerror(self) -> None:
        # Lines 418-419: also handles KeyError (passwd lookup miss).
        from unittest.mock import patch
        from sandbox_core_lib.sandbox_core_lib.tls_pin import _tilde_path
        path = Path('/some/absolute/path')
        with patch(
            'sandbox_core_lib.sandbox_core_lib.tls_pin.Path.home',
            side_effect=KeyError('USER'),
        ):
            result = _tilde_path(path)
        self.assertEqual(result, '/some/absolute/path')

    def test_tilde_path_renders_with_tilde_when_under_home(self) -> None:
        # Line 424: success branch. When the path sits under $HOME,
        # render it with the leading ``~/`` for operator-friendly
        # display in the warning boxes.
        from unittest.mock import patch
        from sandbox_core_lib.sandbox_core_lib.tls_pin import _tilde_path
        with tempfile.TemporaryDirectory() as fake_home_str:
            fake_home = Path(fake_home_str)
            inside = fake_home / 'subdir' / 'file.txt'
            inside.parent.mkdir(parents=True)
            inside.write_text('x')
            with patch(
                'sandbox_core_lib.sandbox_core_lib.tls_pin.Path.home',
                return_value=fake_home,
            ):
                result = _tilde_path(inside)
        self.assertTrue(result.startswith('~/'))
        self.assertIn('subdir/file.txt', result)

    def test_first_run_logs_when_logger_provided(self) -> None:
        # Line 534: logger.info() — operational telemetry that the
        # TOFU first-run pin was established. Helps operators audit
        # *when* their pin was created from logs.
        import logging
        logger = logging.getLogger('test_tls_pin_telemetry')
        captured: list[tuple[str, tuple]] = []

        class _Capture(logging.Handler):
            def emit(self, record):
                captured.append((record.getMessage(), record.args))

        handler = _Capture()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            stderr = _NonTtyStringIO()
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=stderr,
                fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
                pin_file_path=_temp_pin_path(),
                logger=logger,
            )
        finally:
            logger.removeHandler(handler)

        self.assertTrue(
            any('TLS pin established' in msg for msg, _ in captured),
            f'expected pin-established log entry, got {captured!r}',
        )


# Re-export env key name used inside the multi-pin test above.
from sandbox_core_lib.sandbox_core_lib.tls_pin import (  # noqa: E402
    _PIN_ENV_KEY,
)


if __name__ == '__main__':
    unittest.main()
