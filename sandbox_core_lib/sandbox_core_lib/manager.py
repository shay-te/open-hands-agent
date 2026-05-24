"""Build, gate, and wrap the Claude sandbox container.

Three responsibilities:

1. **Preflight** (``check_docker_or_exit``) — called from kato startup
   when ``KATO_CLAUDE_BYPASS_PERMISSIONS=true`` is set. Refuses to
   start the agent if Docker isn't installed and running.

2. **Build** (``ensure_image``) — called lazily on the first
   sandboxed spawn. Builds ``kato/claude-sandbox:latest`` from the
   Dockerfile next to this module if it isn't already present in the
   local image cache. Subsequent spawns are zero-overhead.

3. **Wrap** (``wrap_command``) — turns the existing
   ``[claude, -p, ...]`` argv into a ``[docker, run, ..., claude,
   -p, ...]`` argv. The stdin/stdout NDJSON contract is unchanged so
   the streaming-session reader threads don't care whether they're
   talking to a host process or a container.
"""

from __future__ import annotations
from agent_core_lib.agent_core_lib.helpers.text_utils import text_from_mapping

import contextlib
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
try:
    import fcntl  # POSIX-only; Windows callers fall back to a no-op lock
except ImportError:                                  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


@contextlib.contextmanager
def _exclusive_file_lock(path: Path):
    """Hold an exclusive ``flock`` on ``path`` for the duration of the block.

    Used to serialise audit-log writes across parallel kato spawns —
    both the ``prev_hash`` read and the rate-limit count have to see a
    consistent view of the log, so without this two simultaneous
    spawns can each compute their chain link against the same
    predecessor (one entry's ``prev_hash`` is wrong on read) and each
    see ``N-1`` recent entries (the rate-limit briefly admits one
    extra). On Windows ``fcntl`` is unavailable; we degrade to a
    no-op lock since the audit log on Windows operators' boxes is
    overwhelmingly single-process anyway.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        yield None
        return
    fd = os.open(str(path) + '.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)

SANDBOX_IMAGE_TAG = 'kato/claude-sandbox:latest'
_SANDBOX_DIR = Path(__file__).resolve().parent
_AUTH_VOLUME_NAME = 'kato-claude-config'
_WORKSPACE_MOUNT = '/workspace'
_CLAUDE_HOME = '/home/claude'
# Read-only mount path for the persistent auth volume during *spawn*
# mode. The entrypoint copies a strict allowlist of credential files
# from here into a per-task tmpfs at $CLAUDE_HOME/.claude. Login mode
# bypasses /auth-src entirely and mounts the volume RW directly at
# .claude so ``claude /login`` can write the operator's credentials.
_AUTH_SOURCE_MOUNT = '/auth-src'
# Custom Docker bridge network with inter-container communication
# disabled. Two parallel sandbox containers can each reach
# api.anthropic.com but cannot reach each other, so a malicious turn
# in one task can't pivot through a sibling sandbox.
_SANDBOX_NETWORK_NAME = 'kato-sandbox-net'

# Audit log: one JSON line per sandboxed spawn so the operator has a
# durable record of every container kato launched, surviving kato
# restarts. Lives at ``~/.kato/sandbox-audit.log`` by default; the
# directory is created on first write.
_DEFAULT_AUDIT_LOG_PATH = Path.home() / '.kato' / 'sandbox-audit.log'

# Operator overrides for the two strict-by-default checks. Both
# default to "off" — kato refuses to launch unless the operator
# explicitly opts in. The escape hatches exist for:
#   - macOS / Docker Desktop where gVisor isn't installable,
#   - one-off tasks where committed-secret-shaped files are
#     intentional repo fixtures (e.g. a security-research project).
ALLOW_NO_GVISOR_ENV_KEY = 'KATO_SANDBOX_ALLOW_NO_GVISOR'
ALLOW_WORKSPACE_SECRETS_ENV_KEY = 'KATO_SANDBOX_ALLOW_WORKSPACE_SECRETS'
_TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})


def _env_flag_true(env: dict | None, key: str) -> bool:
    source = env if env is not None else os.environ
    return str(source.get(key, '')).strip().lower() in _TRUE_VALUES

# Resource ceilings — high enough for normal Claude work (lots of
# small file edits, a few hundred MB of model context), low enough
# that a runaway turn can't take down the host.
_MEMORY_LIMIT = '2g'
_PIDS_LIMIT = '256'
_CPUS_LIMIT = '2'

# Env vars on the host that are passed through into the container.
# ``ANTHROPIC_API_KEY`` lets users skip the interactive ``claude
# /login`` flow. The two telemetry / auto-update flags are baked
# into the image already; we re-pass them for explicit override.
_PASS_THROUGH_ENV = (
    'ANTHROPIC_API_KEY',
    'CLAUDE_CODE_OAUTH_TOKEN',
)

# Label the Dockerfile stamps so we can verify the cached image was
# actually built by us, not a same-named image from another source.
_IMAGE_IDENTITY_LABEL = 'org.kato.sandbox'
_IMAGE_IDENTITY_VALUE = 'true'

# Refuse to bind-mount any of these — handing Claude the operator's
# whole machine through a misconfigured workspace path would defeat
# the entire sandbox. The list is intentionally aggressive: better to
# refuse a legitimate-but-weird workspace path than silently expose
# sensitive directories.
#
# Two flavours of refusal:
#
#   * ``_FORBIDDEN_MOUNT_SOURCES_SUBTREE`` — the path itself **and any
#     descendant** is refused. Used for system roots like ``/etc``
#     (mounting ``/etc/foo`` would expose ``/etc/passwd``-adjacent
#     state) and for sensitive subtrees of the operator's home like
#     ``~/.ssh``, ``~/.aws``, ``~/.gnupg``.
#
#   * ``_FORBIDDEN_MOUNT_SOURCES_EXACT`` — only the exact path is
#     refused, descendants are allowed. Used for ``/`` (obviously
#     can't subtree-block since everything is under it), for the
#     "home roots" ``/home`` and ``/Users`` (their immediate
#     subdirectories are user homes, which are legitimate workspace
#     parents), and for the operator's own ``$HOME`` (per-task
#     workspaces typically live somewhere under it, but the home
#     dir itself is never a valid workspace).
_FORBIDDEN_MOUNT_SOURCES_SUBTREE = frozenset({
    Path('/root'),
    Path('/etc'),
    Path('/usr'),
    Path('/var'),
    Path('/bin'),
    Path('/sbin'),
    Path('/lib'),
    Path('/boot'),
    Path('/dev'),
    Path('/proc'),
    Path('/sys'),
    # Docker daemon socket / state — mounting any of these would
    # let the sandboxed Claude talk to the host Docker daemon and
    # spawn an unconstrained container with /:host bind-mounted
    # (classic container escape via docker.sock). Subtree, so
    # ``/var/run/docker.sock``-adjacent paths are blocked too.
    Path('/var/run/docker.sock'),
    Path('/var/lib/docker'),
    Path('/var/lib/containerd'),
    Path('/run/docker.sock'),
    Path('/run/containerd'),
    Path('/private'),
    Path('/Library'),
    Path('/System'),
    Path('/Applications'),
    Path('/Volumes'),
    # Sensitive subtrees under the operator's $HOME. Subdirs of
    # these (e.g. ``~/.ssh/authorized_keys``) are blocked too.
    Path.home() / '.ssh',
    Path.home() / '.aws',
    Path.home() / '.gnupg',
    Path.home() / '.gcp',
    Path.home() / '.kube',
    Path.home() / '.docker',
    Path.home() / '.config' / 'gcloud',
    Path.home() / '.config' / 'kato',
    # macOS keychain / app-support secrets directories.
    #
    # Broad by intent: bypass mode runs an autonomous coding agent
    # with no per-tool prompts. Mounting any of these as a workspace
    # would expose Apple ID auth tokens (IdentityServices), iMessage
    # chat history (Messages, Group Containers), Mail, Safari
    # cookies / bookmarks / history, calendar database, contacts
    # (AddressBook), call history, recently-opened-file lists, and
    # the broad Containers / Group Containers trees used by every
    # sandboxed macOS app for its private data. Operators who keep
    # workspaces under any of these subtrees should move them.
    #
    # On non-macOS hosts (Linux / WSL2) these paths simply don't
    # exist; ``_validate_workspace_path``'s exists() check would
    # catch them anyway, so the entries are harmless on Linux.
    Path.home() / 'Library' / 'Keychains',
    Path.home() / 'Library' / 'Cookies',
    Path.home() / 'Library' / 'Mail',
    Path.home() / 'Library' / 'Messages',
    Path.home() / 'Library' / 'Safari',
    Path.home() / 'Library' / 'Calendars',
    Path.home() / 'Library' / 'IdentityServices',
    Path.home() / 'Library' / 'Group Containers',
    Path.home() / 'Library' / 'Containers',
    Path.home() / 'Library' / 'Application Support' / 'Google' / 'Chrome',
    Path.home() / 'Library' / 'Application Support' / 'Firefox',
    Path.home() / 'Library' / 'Application Support' / 'com.apple.sharedfilelist',
    Path.home() / 'Library' / 'Application Support' / 'AddressBook',
    Path.home() / 'Library' / 'Application Support' / 'Knowledge',
    Path.home() / 'Library' / 'Application Support' / 'CallHistoryDB',
})
_FORBIDDEN_MOUNT_SOURCES_EXACT = frozenset({
    Path('/'),
    Path('/home'),
    Path('/Users'),
    Path.home(),
    # ``~/.kato`` itself is refused (it holds the audit log + lock,
    # plus per-task workspace clones at ``~/.kato/workspaces/`` by
    # default). Mounting the whole dir would let Claude see the audit
    # log and any sibling task's workspace. Descendants are allowed —
    # the legitimate per-task workspace path is
    # ``~/.kato/workspaces/<task_id>/<repo>/``.
    Path.home() / '.kato',
})


# ============================================================================
# Security invariants — single source of truth, kept in sync with
# BYPASS_PROTECTIONS.md by tests/test_bypass_protections_doc_consistency.py
# ============================================================================
#
# Each constant below is the canonical declaration of a security-relevant
# property of the sandbox. The companion test asserts SET-EQUALITY against
# anchored sections in ``BYPASS_PROTECTIONS.md`` and (where mechanical
# verification is possible) against the actual ``wrap_command`` argv.
#
# To add, remove, or rename anything in any of these sets you MUST also
# update the matching anchor block in ``BYPASS_PROTECTIONS.md`` — and you
# should think very hard about whether you're changing what the threat
# model says the sandbox guarantees. The drift guard exists to make that
# decision impossible to skip silently.

# Required Docker run flags. Every entry MUST appear in ``wrap_command``
# argv (verified semantically by the drift-guard test). Form:
# ``--key=value`` for kv flags, ``--key`` for boolean flags. The test's
# matcher accepts either single-token (``--ipc=none``) or two-token
# (``--ipc none``) form in argv.
_REQUIRED_DOCKER_FLAGS = frozenset({
    '--network=kato-sandbox-net',
    '--ipc=none',
    '--cgroupns=private',
    '--pid=container',
    '--uts=private',
    '--cap-drop=ALL',
    '--cap-add=NET_ADMIN',
    '--cap-add=NET_RAW',
    '--cap-add=SETUID',
    '--cap-add=SETGID',
    '--security-opt=no-new-privileges',
    '--security-opt=apparmor=docker-default',
    '--read-only',
})

# Forbidden Docker run flags. NONE of these may appear in ``wrap_command``
# argv (verified semantically). Each one would silently downgrade the
# threat model in a specific way; the per-flag rationale lives in the
# "Why these specific surfaces" section of BYPASS_PROTECTIONS.md.
_FORBIDDEN_DOCKER_FLAGS = frozenset({
    '--privileged',
    '--network=host',
    '--pid=host',
    '--ipc=host',
    '--uts=host',
    '--userns=host',
    '--cgroupns=host',
    '--cap-add=ALL',
    '--cap-add=SYS_ADMIN',
    '--cap-add=SYS_PTRACE',
    '--cap-add=SYS_MODULE',
    '--cap-add=SYS_BOOT',
    '--security-opt=seccomp=unconfined',
    '--security-opt=apparmor=unconfined',
    '--security-opt=systempaths=unconfined',
    '--security-opt=label=disable',
})

# Auth-volume invariants — named tags for properties that the spawn /
# login flows guarantee. Mechanical verification of each property lives
# in entrypoint.sh, wrap_command, login_command, and the Makefile. The
# drift guard ensures the named SET stays in sync with the doc.
_AUTH_VOLUME_INVARIANTS = frozenset({
    'spawn-source-readonly',
    'spawn-target-tmpfs',
    'spawn-credentials-allowlist',
    'spawn-bidirectional-manifest-check',
    'spawn-sha256-manifest-verify',
    'login-direct-readwrite',
    'login-only-volume-writer',
    'login-stamps-manifest',
})

# Firewall guarantees — named tags for properties of init-firewall.sh +
# the wrap_command sysctls/dns flags. Same pattern: mechanical
# enforcement is elsewhere; drift guard keeps the NAMED set in sync.
_FIREWALL_GUARANTEES = frozenset({
    'default-drop-policy',
    'allowlist-only-anthropic-tcp-443',
    'dns-only-cloudflare',
    'dns-rate-limit-60-per-minute',
    'rfc1918-explicit-deny',
    'cloud-metadata-explicit-deny',
    'icmp-blocked',
    'ipv6-disabled',
    'fail-closed-on-anthropic-unreachable',
    'refuses-private-ip-in-allowlist',
})

# Threat-model classification terms used in BYPASS_PROTECTIONS.md
# tables. Adding a new term (e.g. "Bounded-with-monitoring") must
# happen in BOTH places — the drift guard catches drift either way.
_CLASSIFICATION_TERMS = frozenset({
    'Mitigated',
    'Bounded',
    'Accepted',
    'Accepted-with-mitigation',
    'Not-applicable',
})


class SandboxError(RuntimeError):
    """Raised when the sandbox cannot be prepared or launched."""


# ----- preflight -----

def docker_available() -> bool:
    """True when ``docker`` is on PATH and the daemon answers ``info``."""
    if shutil.which('docker') is None:
        return False
    try:
        result = subprocess.run(
            ['docker', 'info', '--format', '{{.ServerVersion}}'],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def gvisor_runtime_available() -> bool:
    """True when ``runsc`` (gVisor) is configured as a Docker runtime.

    gVisor adds syscall-level isolation on top of namespaces and
    capabilities — a second kernel, in userspace, between the
    container and the host. When available we automatically use it
    via ``--runtime=runsc`` for the strongest isolation kato can offer.
    """
    try:
        result = subprocess.run(
            ['docker', 'info', '--format', '{{json .Runtimes}}'],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    try:
        runtimes = json.loads(result.stdout.strip() or '{}')
    except json.JSONDecodeError:
        return False
    return isinstance(runtimes, dict) and 'runsc' in runtimes


def docker_running_rootless() -> bool:
    """True when the Docker daemon is running in rootless mode.

    Rootless mode confines a container escape to the operator's
    user account rather than full root on the host. We don't refuse
    to start without it (it's a daemon-side configuration), but we
    surface a one-line recommendation at boot when bypass is on and
    the daemon is rooted.
    """
    try:
        result = subprocess.run(
            ['docker', 'info', '--format', '{{.SecurityOptions}}'],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return 'rootless' in result.stdout.lower()


def check_gvisor_or_exit(*, env: dict | None = None) -> None:
    """Refuse to start unless gVisor is configured, or operator overrides.

    gVisor (``runsc``) puts a userspace kernel between the container
    and the host, so most Linux-kernel CVEs cannot be used to escape
    the sandbox. With bypass mode on, that's a meaningful additional
    layer — Claude can run any command, and the container's only
    remaining isolation from the host is the kernel itself.

    Strict by default: if gVisor isn't available kato refuses to
    start. The override ``KATO_SANDBOX_ALLOW_NO_GVISOR=true`` exists
    for environments where gVisor can't be installed (most notably
    Docker Desktop on macOS / Windows, where the underlying VM is
    locked down).
    """
    if gvisor_runtime_available():
        return
    if _env_flag_true(env, ALLOW_NO_GVISOR_ENV_KEY):
        return
    bar = '=' * 78
    sys.stderr.write(
        '\n'.join((
            '',
            bar,
            'Kato cannot start: gVisor (runsc) is required for bypass mode.',
            '',
            'When KATO_CLAUDE_BYPASS_PERMISSIONS=true, kato runs Claude inside',
            'a hardened sandbox. Without gVisor, the only thing isolating the',
            'container from your host is the Linux kernel itself — a single',
            'kernel CVE could be used to escape. gVisor adds a userspace',
            'kernel between them, which is much harder to break.',
            '',
            'Pick one:',
            '  1. Install gVisor and register it as a Docker runtime:',
            '       https://gvisor.dev/docs/user_guide/install/',
            '       (then `docker info` should list "runsc" under Runtimes)',
            '  2. If you cannot install gVisor (e.g. Docker Desktop on macOS',
            '     or Windows where the underlying VM is locked down), you can',
            '     accept the residual kernel-CVE risk by setting:',
            f'       export {ALLOW_NO_GVISOR_ENV_KEY}=true',
            '     The other 8 sandbox layers (cap-drop, read-only rootfs,',
            '     egress firewall, etc.) still apply. See BYPASS_PROTECTIONS.md.',
            '  3. Or unset KATO_CLAUDE_BYPASS_PERMISSIONS to run Claude on',
            '     the host with permission prompts in the planning UI.',
            bar,
            '',
        )),
    )
    sys.stderr.flush()
    sys.exit(1)


def check_docker_or_exit() -> None:
    """Print a clear CLI message and ``sys.exit(1)`` if Docker is unavailable.

    Called from ``kato.main`` immediately after the bypass flag is
    consulted. The intent is: if the operator turned on
    ``KATO_CLAUDE_BYPASS_PERMISSIONS`` they accepted that Claude needs
    a hardened sandbox, and that sandbox needs Docker. We refuse to
    fall back to host execution silently — too easy to miss.
    """
    if docker_available():
        return
    bar = '=' * 78
    sys.stderr.write(
        '\n'.join((
            '',
            bar,
            'Kato cannot start: sandbox required but Docker is not available.',
            '',
            'You set KATO_CLAUDE_BYPASS_PERMISSIONS=true. In this mode kato runs',
            'Claude inside a hardened Docker sandbox so '
            '--permission-mode bypassPermissions',
            "can't reach beyond the per-task workspace folder. The sandbox needs",
            "Docker, and ``docker info`` doesn't currently work on this machine.",
            '',
            'Pick one:',
            '  1. Install Docker Desktop (or your distro\'s docker package) and',
            '     start it, then re-run `make compose-up`. Verify with:',
            '         docker info',
            '  2. Or unset the flag to run Claude on the host with permission',
            '     prompts in the planning UI:',
            '         unset KATO_CLAUDE_BYPASS_PERMISSIONS',
            bar,
            '',
        )),
    )
    sys.stderr.flush()
    sys.exit(1)


# ----- image build -----

def image_exists(image_tag: str = SANDBOX_IMAGE_TAG) -> bool:
    try:
        result = subprocess.run(
            ['docker', 'image', 'inspect', image_tag],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def image_built_by_kato(image_tag: str = SANDBOX_IMAGE_TAG) -> bool:
    """True when the cached image carries our identity label.

    Defends against a same-named image of unknown provenance sitting
    in the local Docker cache. ``ensure_image`` rebuilds when this
    returns False — the rebuild stamps the label as part of its
    Dockerfile, so subsequent runs see it.
    """
    try:
        result = subprocess.run(
            [
                'docker', 'image', 'inspect',
                '--format', '{{ index .Config.Labels "' + _IMAGE_IDENTITY_LABEL + '" }}',
                image_tag,
            ],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip() == _IMAGE_IDENTITY_VALUE


_BASE_IMAGE_ENV_KEY = 'KATO_SANDBOX_BASE_IMAGE'
_ALLOW_FLOATING_BASE_IMAGE_ENV_KEY = 'KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE'
_CLAUDE_CLI_VERSION_ENV_KEY = 'KATO_SANDBOX_CLAUDE_CLI_VERSION'
_ALLOW_FLOATING_CLAUDE_CLI_ENV_KEY = 'KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI'


def _validate_base_image_pin_or_refuse(
    *,
    env: dict | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Refuse to build unless the base image is digest-pinned (strict-by-default).

    Closes the build-time supply-chain channel #17 by changing the
    default. Operators have two paths:

      1. **Recommended** — set ``KATO_SANDBOX_BASE_IMAGE`` to a
         digest-pinned reference like
         ``node:22-bookworm-slim@sha256:<digest>``. The build will use
         that exact immutable digest; a hostile registry / DNS hijack
         at build time cannot substitute the base image.
      2. **Opt-out** — set ``KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE=true``
         to acknowledge the residual and allow the moving
         ``node:22-bookworm-slim`` tag. Operator accepts that a
         hostile network during the next build could poison the
         resulting image.

    A value set on ``KATO_SANDBOX_BASE_IMAGE`` without ``@sha256:``
    in it is also refused — half-pinning (``node:22-bookworm-slim``
    without a digest) is no protection at all and would give the
    operator a false sense of security.
    """
    source = env if env is not None else os.environ
    base = str(source.get(_BASE_IMAGE_ENV_KEY, '') or '').strip()
    allow_floating = str(
        source.get(_ALLOW_FLOATING_BASE_IMAGE_ENV_KEY, '') or ''
    ).strip().lower() in {'1', 'true', 'yes', 'on'}

    if base:
        if '@sha256:' not in base:
            raise SandboxError(
                f'{_BASE_IMAGE_ENV_KEY}={base!r} is set but does not '
                f'include a digest pin (expected '
                f'``node:22-bookworm-slim@sha256:<digest>``). A '
                f'tag-only value provides no supply-chain protection — '
                f'kato refuses to build with a half-pinned base image. '
                f'Either add the digest, or set '
                f'{_ALLOW_FLOATING_BASE_IMAGE_ENV_KEY}=true to '
                f'explicitly accept the floating-tag residual.'
            )
        if logger is not None:
            logger.info(
                'sandbox: building with digest-pinned base image %s '
                '(%s)', base, _BASE_IMAGE_ENV_KEY,
            )
        return

    if allow_floating:
        if logger is not None:
            logger.warning(
                'sandbox: building with FLOATING base image tag '
                '(%s=true). A compromised registry or hostile network '
                'at build time could substitute the base image. '
                'Recommend %s=node:22-bookworm-slim@sha256:<digest>.',
                _ALLOW_FLOATING_BASE_IMAGE_ENV_KEY,
                _BASE_IMAGE_ENV_KEY,
            )
        return

    # Strict default — refuse the build.
    raise SandboxError(
        'kato refuses to build the sandbox image without a digest-pinned '
        f'base image. The previous default (floating ``node:22-bookworm-slim`` '
        f'tag) left the build-time supply chain unbounded — a hostile '
        f'registry / DNS hijack / corporate proxy at build time could '
        f'substitute the base image and every subsequent spawn would '
        f'run poisoned binaries. Pick one:\n'
        f'  1. Recommended: export {_BASE_IMAGE_ENV_KEY}=node:22-bookworm-slim@sha256:<digest>\n'
        f'     (find the current digest with: docker manifest inspect node:22-bookworm-slim | jq -r .config.digest)\n'
        f'  2. Opt-out: export {_ALLOW_FLOATING_BASE_IMAGE_ENV_KEY}=true\n'
        f'     (operator accepts the residual; build proceeds with the floating tag)\n'
        f'See BYPASS_PROTECTIONS.md "Build-time supply chain" for detail.'
    )


def _validate_claude_cli_version_pin_or_refuse(
    *,
    env: dict | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Refuse build unless the Claude CLI version is pinned (strict-by-default).

    Closes the npm-side slice of build-time supply chain (residual
    #17) by changing the default. Without a pin, the Dockerfile
    runs ``npm install -g @anthropic-ai/claude-code`` which resolves
    ``latest`` against the npm registry — a malicious tag pushed
    between operator builds would land in the resulting image.

    Operator paths:

      1. **Recommended** — set ``KATO_SANDBOX_CLAUDE_CLI_VERSION``
         to a specific version like ``2.1.5``. The build pins
         ``@anthropic-ai/claude-code@<that version>`` instead of
         ``latest``.
      2. **Opt-out** — set ``KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI=true``
         to acknowledge the residual and allow ``latest``.

    Parallel to ``_validate_base_image_pin_or_refuse``: same shape,
    same opt-out pattern, same operator-friendly error message
    naming both fix paths.
    """
    source = env if env is not None else os.environ
    pinned = str(source.get(_CLAUDE_CLI_VERSION_ENV_KEY, '') or '').strip()
    allow_floating = str(
        source.get(_ALLOW_FLOATING_CLAUDE_CLI_ENV_KEY, '') or ''
    ).strip().lower() in {'1', 'true', 'yes', 'on'}

    if pinned:
        if logger is not None:
            logger.info(
                'sandbox: building with pinned Claude CLI version %s (%s)',
                pinned, _CLAUDE_CLI_VERSION_ENV_KEY,
            )
        return

    if allow_floating:
        if logger is not None:
            logger.warning(
                'sandbox: building with FLOATING Claude CLI version '
                '(%s=true). A malicious npm release pushed between '
                'operator builds could land in the resulting image. '
                'Recommend %s=<specific-version>.',
                _ALLOW_FLOATING_CLAUDE_CLI_ENV_KEY,
                _CLAUDE_CLI_VERSION_ENV_KEY,
            )
        return

    raise SandboxError(
        'kato refuses to build the sandbox image without a pinned '
        f'Claude CLI version. The previous default (``npm install -g '
        f'@anthropic-ai/claude-code@latest``) left the npm-side of the '
        f'build-time supply chain unbounded. Pick one:\n'
        f'  1. Recommended: export {_CLAUDE_CLI_VERSION_ENV_KEY}=<version>\n'
        f'     (e.g. 2.1.5; find current with: npm view @anthropic-ai/claude-code version)\n'
        f'  2. Opt-out: export {_ALLOW_FLOATING_CLAUDE_CLI_ENV_KEY}=true\n'
        f'     (operator accepts the residual; build proceeds with @latest)\n'
        f'See BYPASS_PROTECTIONS.md "Build-time supply chain" for detail.'
    )


def build_image(
    *,
    image_tag: str = SANDBOX_IMAGE_TAG,
    env: dict | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Build ``image_tag`` from the Dockerfile next to this module.

    Streams docker's stdout to the logger so the operator sees the
    ``apt-get`` / ``npm install`` progress on first build (~1 minute
    on a warm npm cache, longer cold). Raises ``SandboxError`` with
    the captured output on failure so the caller can surface a
    clear "build failed" message.

    Refuses the build (raises ``SandboxError``) unless BOTH supply-chain
    pins are satisfied (or explicitly opted out via the matching
    ``ALLOW_FLOATING_*`` env vars):

      * ``KATO_SANDBOX_BASE_IMAGE`` digest-pinned —
        see ``_validate_base_image_pin_or_refuse``.
      * ``KATO_SANDBOX_CLAUDE_CLI_VERSION`` pinned —
        see ``_validate_claude_cli_version_pin_or_refuse``.

    Both validators run before any docker invocation so a refusal
    fails fast without touching the registry.
    """
    _validate_base_image_pin_or_refuse(env=env, logger=logger)
    _validate_claude_cli_version_pin_or_refuse(env=env, logger=logger)
    if logger is not None:
        logger.info(
            'building Claude sandbox image %s — first run, may take ~1 min',
            image_tag,
        )
    cmd = ['docker', 'build', '-t', image_tag]
    # Read pin overrides from the SAME env source the validators used.
    # Previously this read ``os.environ`` directly while validators
    # honored the ``env`` parameter — a CI/test caller that passes a
    # pinned env dict could pass validation and then silently fall
    # back to floating tags during the actual build (supply-chain
    # pin bypass).
    env_source = env if env is not None else os.environ
    # Operator-side supply-chain pin: if KATO_SANDBOX_BASE_IMAGE is set
    # (typically to ``node:22-bookworm-slim@sha256:<digest>``), pass it
    # as the BASE_IMAGE build-arg so the Dockerfile pulls that exact
    # immutable digest instead of the mutable ``node:22-bookworm-slim``
    # tag. Recommended for any deployment that cares about base-image
    # tampering or reproducibility.
    base_override = text_from_mapping(env_source, 'KATO_SANDBOX_BASE_IMAGE')
    if base_override:
        cmd.extend(['--build-arg', f'BASE_IMAGE={base_override}'])
        if logger is not None:
            logger.info(
                'sandbox: pinning base image to %s (KATO_SANDBOX_BASE_IMAGE)',
                base_override,
            )
    # Operator-side npm-side supply-chain pin. If
    # KATO_SANDBOX_CLAUDE_CLI_VERSION is set (e.g. ``2.1.5``), pass
    # it as the CLAUDE_CLI_VERSION build-arg so the Dockerfile installs
    # ``@anthropic-ai/claude-code@<that version>`` instead of ``latest``.
    # Closes the build-time channel where a malicious ``latest`` could
    # be pushed to npm between operator builds. Default ``latest``
    # preserves existing behavior — operators opt into pinning when
    # their threat model requires it.
    cli_override = text_from_mapping(env_source, 'KATO_SANDBOX_CLAUDE_CLI_VERSION')
    if cli_override:
        cmd.extend(['--build-arg', f'CLAUDE_CLI_VERSION={cli_override}'])
        if logger is not None:
            logger.info(
                'sandbox: pinning Claude CLI version to %s '
                '(KATO_SANDBOX_CLAUDE_CLI_VERSION)',
                cli_override,
            )
    cmd.append(str(_SANDBOX_DIR))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(
            f'sandbox image build timed out after 10 minutes: {exc}',
        ) from exc
    except OSError as exc:
        raise SandboxError(
            f'failed to invoke docker build: {exc}',
        ) from exc
    if result.returncode != 0:
        raise SandboxError(
            'sandbox image build failed:\n'
            f'STDOUT:\n{result.stdout}\n'
            f'STDERR:\n{result.stderr}',
        )
    if logger is not None:
        logger.info('sandbox image %s ready', image_tag)


def ensure_image(
    *,
    image_tag: str = SANDBOX_IMAGE_TAG,
    logger: logging.Logger | None = None,
) -> None:
    """Idempotent: build the image if missing or not built by kato.

    The identity-label check forces a rebuild when a same-tagged image
    of unknown provenance is sitting in the cache (e.g. operator
    pulled something or built it from a different source). The
    rebuild restamps the label so subsequent calls short-circuit.

    Also ensures the isolated bridge network exists so parallel
    sandboxes can't reach each other.
    """
    if image_exists(image_tag) and image_built_by_kato(image_tag):
        ensure_network(logger=logger)
        return
    if image_exists(image_tag) and not image_built_by_kato(image_tag) and logger is not None:
        logger.warning(
            'sandbox image %s exists but lacks the kato identity label; '
            'rebuilding from %s to ensure the configured hardening applies',
            image_tag, _SANDBOX_DIR,
        )
    build_image(image_tag=image_tag, logger=logger)
    ensure_network(logger=logger)


def ensure_network(*, logger: logging.Logger | None = None) -> None:
    """Idempotently create the isolated sandbox bridge network.

    The custom bridge sets ``com.docker.network.bridge.enable_icc=false``
    so two parallel sandbox containers (e.g. kato spawning Claude for
    two tasks at once) cannot communicate with each other — each is
    its own island that can only reach api.anthropic.com.

    Fail-closed: if the isolated network can neither be inspected nor
    created, raise ``SandboxError`` rather than silently falling back
    to the default ``docker0`` bridge (which has ``enable_icc=true``,
    breaking the inter-container isolation guarantee).
    """
    try:
        result = subprocess.run(
            ['docker', 'network', 'inspect', _SANDBOX_NETWORK_NAME],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError(
            f'cannot inspect docker networks ({exc}) — refusing to '
            'launch sandbox without confirmed network isolation',
        ) from exc
    if result.returncode == 0:
        return
    create_cmd = [
        'docker', 'network', 'create',
        '--driver', 'bridge',
        '--opt', 'com.docker.network.bridge.enable_icc=false',
        '--opt', 'com.docker.network.bridge.enable_ip_masquerade=true',
        _SANDBOX_NETWORK_NAME,
    ]
    try:
        result = subprocess.run(
            create_cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError(
            f'failed to create isolated sandbox network '
            f'{_SANDBOX_NETWORK_NAME} ({exc}) — refusing to launch '
            'sandbox without inter-container isolation',
        ) from exc
    if result.returncode != 0:
        stderr = result.stderr.strip() or '(no stderr)'
        if logger is not None:
            logger.error(
                'failed to create sandbox network %s: %s',
                _SANDBOX_NETWORK_NAME, stderr,
            )
        raise SandboxError(
            f'failed to create isolated sandbox network '
            f'{_SANDBOX_NETWORK_NAME}: {stderr} — refusing to launch '
            'sandbox without inter-container isolation',
        )


def _is_relative_to(child: Path, parent: Path) -> bool:
    """``Path.is_relative_to`` shim for Python <3.9 plus OSError-safe.

    Returns True when ``child`` equals ``parent`` or is nested under
    it. Falls back to a string-prefix check when path comparison
    raises (extremely rare — happens on Windows reserved names).
    """
    try:
        return child == parent or child.is_relative_to(parent)
    except (AttributeError, ValueError, OSError):
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False


def _forbidden_match(resolved: Path) -> Path | None:
    """Return the forbidden ancestor of ``resolved``, or None.

    ``_FORBIDDEN_MOUNT_SOURCES_SUBTREE`` matches the path itself and
    any descendant. ``_FORBIDDEN_MOUNT_SOURCES_EXACT`` matches only
    the exact path; descendants are allowed (this is how per-task
    workspaces under ``$HOME`` are permitted).
    """
    for forbidden in _FORBIDDEN_MOUNT_SOURCES_SUBTREE:
        if _is_relative_to(resolved, forbidden):
            return forbidden
    if resolved in _FORBIDDEN_MOUNT_SOURCES_EXACT:
        return resolved
    return None


def _validate_workspace_path(workspace_path: str) -> str:
    """Resolve ``workspace_path`` and refuse anything that would expose host state.

    The bind mount is the only file-level seam between the sandbox and
    the host. A misconfigured workspace path (typo, env var pointing
    at ``$HOME``, an attacker-influenced config) would hand Claude the
    operator's whole machine. We reject:

    - empty / unset paths,
    - common system roots and any descendants (``/etc/foo`` is just as
      bad as ``/etc``),
    - the operator's home directory itself, and any descendant of the
      sensitive subtrees under it (``~/.ssh``, ``~/.aws``,
      ``~/.gnupg``, ``~/.kube``, ``~/.docker``, ``~/.kato``, macOS
      keychain dirs, browser profile dirs),
    - anything that doesn't actually exist on disk (typos),
    - anything that isn't a directory.
    """
    if not workspace_path or not str(workspace_path).strip():
        raise SandboxError(
            'sandbox workspace path is empty — refusing to mount '
            'unspecified path into the container',
        )
    expanded = Path(workspace_path).expanduser()
    unresolved = expanded if expanded.is_absolute() else Path.cwd() / expanded
    resolved = expanded.resolve()
    match = _forbidden_match(unresolved) or _forbidden_match(resolved)
    if match is not None:
        if match == resolved:
            raise SandboxError(
                f'sandbox workspace path {resolved} is a system or home '
                'directory — refusing to bind-mount it. Check '
                'KATO_WORKSPACES_ROOT and the per-task workspace layout.',
            )
        raise SandboxError(
            f'sandbox workspace path {resolved} is under sensitive '
            f'directory {match} — refusing to bind-mount it (would '
            'expose secrets / system state to Claude). Move the '
            'workspace outside this subtree.',
        )
    if not resolved.exists():
        raise SandboxError(
            f'sandbox workspace path {resolved} does not exist — '
            'refusing to bind-mount a non-existent path',
        )
    if not resolved.is_dir():
        raise SandboxError(
            f'sandbox workspace path {resolved} is not a directory — '
            'refusing to bind-mount it',
        )
    # Defense-in-depth: scan the top of the workspace for a Docker
    # socket. If someone has a docker-in-docker / podman-style
    # ``docker.sock`` symlink inside the workspace, mounting it lets
    # Claude pivot to the host Docker daemon and spawn an
    # unconstrained container — full host compromise. Top-level only
    # so the scan stays cheap on huge repos.
    try:
        for entry in resolved.iterdir():
            if entry.name in ('docker.sock', 'containerd.sock'):
                raise SandboxError(
                    f'sandbox workspace {resolved} contains a Docker/'
                    f'containerd socket ({entry.name}) — refusing to '
                    'mount, this would let the sandbox talk to the '
                    'host Docker daemon and escape',
                )
    except (OSError, PermissionError):
        # Best-effort: if the workspace is unreadable, we'll fail
        # later for a different reason. Don't block on transient FS.
        pass
    return str(resolved)


# ----- spawn wrap -----

def wrap_command(
    inner_command: list[str],
    *,
    workspace_path: str,
    image_tag: str = SANDBOX_IMAGE_TAG,
    container_name: str | None = None,
    task_id: str | None = None,
) -> list[str]:
    """Wrap ``inner_command`` (the Claude CLI argv) in a ``docker run`` argv.

    The returned argv is fed straight to ``subprocess.Popen``. Inside
    the container:

    - ``--cap-drop ALL`` then a narrow ``--cap-add NET_ADMIN/NET_RAW``
      so the entrypoint can run iptables. The Claude process itself
      runs after capabilities are dropped via ``setpriv`` in the
      entrypoint, so it has no privileges of any kind.
    - ``--security-opt no-new-privileges`` blocks setuid escalation.
    - ``--read-only`` makes the container FS immutable; only the
      bind-mounted workspace and the per-task tmpfs at
      ``/home/claude/.claude`` are writable.
    - ``--network bridge`` so the iptables policy applies (host-network
      mode would bypass it).
    - ``--memory`` / ``--pids-limit`` / ``--cpus`` keep a runaway
      turn from starving the host.
    - The workspace is bind-mounted at ``/workspace`` (the WORKDIR).
    - The operator's Claude credentials live in a persistent named
      volume mounted **read-only** at ``/auth-src``. The entrypoint
      copies a strict allowlist of credential files (``.credentials.json``)
      out of /auth-src into the **per-task tmpfs** at
      ``/home/claude/.claude``. This means a poisoned ``settings.json``,
      hook script, custom slash command, MCP config, or subagent
      definition written by a previous task is **never** carried into
      this task — we copy creds only, and the tmpfs is destroyed on
      container exit so this task can persist nothing either.
    """
    workspace = _validate_workspace_path(workspace_path)
    argv: list[str] = [
        'docker', 'run',
        '--rm',
        '-i',
        '--init',                              # tini reaps zombies inside container
        '--name', container_name or make_container_name(task_id or ''),
    ]
    # Forensic labels — surface in ``docker ps --format '{{.Labels}}'``
    # and ``docker inspect`` so an investigator can correlate a running
    # or just-exited container back to the task it served, the
    # workspace it had access to, and the auth volume it pulled creds
    # from. These are not security boundaries; they are evidence.
    argv.extend([
        '--label', 'org.kato.sandbox=true',
        '--label', f'org.kato.task-id={(task_id or "unknown")[:64]}',
        '--label', f'org.kato.workspace={workspace[:200]}',
        '--label', f'org.kato.auth-volume={_AUTH_VOLUME_NAME}',
    ])
    # gVisor (runsc) when available — adds a userspace kernel between
    # the container and the host, neutralising most kernel-CVE escape
    # paths. Free hardening when the operator has it installed; we
    # silently use the default (runc) otherwise.
    if gvisor_runtime_available():
        argv.extend(['--runtime', 'runsc'])
    argv.extend([
        '--network', _SANDBOX_NETWORK_NAME,    # custom bridge with --icc=false
        '--ipc=none',                          # no shared memory / sysv IPC channel
        '--cgroupns=private',                  # private cgroup namespace (host cgroup tree is invisible)
        '--pid=container',                     # explicit: own PID namespace, not host's
        '--uts=private',                       # explicit: own hostname namespace
        '--cap-drop', 'ALL',
        '--cap-add', 'NET_ADMIN',              # needed only by init-firewall
        '--cap-add', 'NET_RAW',                # needed only by init-firewall
        # Needed only for the ``setpriv`` step in entrypoint.sh that
        # drops root → claude (uid 1000). Without these, setresuid
        # fails with EPERM even from root. The entrypoint's
        # ``--bounding-set=-all`` wipes them before Claude exec, so
        # the running Claude process never holds them.
        '--cap-add', 'SETUID',
        '--cap-add', 'SETGID',
        '--security-opt', 'no-new-privileges',
        # AppArmor: explicitly pin to docker-default. On hosts where
        # AppArmor is loaded (Ubuntu, Debian) this gives an additional
        # MAC layer that constrains things capabilities don't (e.g.
        # mount points, /sys writes, ptrace beyond same-uid). On
        # hosts without AppArmor (macOS / many distros) Docker
        # silently ignores this flag — no-op, but documents intent.
        '--security-opt', 'apparmor=docker-default',
        '--read-only',                         # rootfs immutable
        # Tmpfs ceilings: bounded against runaway disk fill but
        # generous enough that legitimate tooling (pip wheel
        # extraction, npm tarballs, language-server caches, tar/gzip
        # scratch space) doesn't hit ENOSPC during normal Claude
        # work. Also nosuid+nodev so a crafted setuid binary or
        # device node smuggled into a tmpfs cannot be activated.
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m',
        '--tmpfs', '/run:rw,nosuid,nodev,size=4m',
        '--tmpfs', '/var/tmp:rw,nosuid,nodev,size=32m',
        '--shm-size=16m',                      # bound /dev/shm (Claude doesn't use SysV shm)
        '--memory', _MEMORY_LIMIT,
        '--memory-swap', _MEMORY_LIMIT,        # disable swap (= memory) so OOM is hard
        '--pids-limit', _PIDS_LIMIT,
        '--cpus', _CPUS_LIMIT,
        '--ulimit', 'nofile=1024:1024',        # bounded fd count
        '--ulimit', 'nproc=128:128',           # bounded process count
        '--ulimit', 'core=0:0',                # disable core dumps (prevents memory→disk leak on crash)
        # Cap any single file the container writes at 1 GiB. Stops
        # a runaway log / dump from filling the operator's disk via
        # the workspace bind-mount or the .claude tmpfs.
        '--ulimit', 'fsize=1073741824:1073741824',
        # Disable POSIX message queues entirely — Claude doesn't use
        # them and they're a kernel-side data structure with their
        # own attack surface.
        '--ulimit', 'msgqueue=0:0',
        # Bound pending signals + held file locks. Tiny kernel
        # resources whose unbounded growth has historically been
        # used in local DoS PoCs.
        '--ulimit', 'sigpending=8192:8192',
        '--ulimit', 'locks=64:64',
        # Disable IPv6 entirely. The egress firewall only configures
        # ip4tables; an IPv6-capable container could route traffic
        # around it. Killing the stack at the kernel level is the
        # cleanest defense.
        '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1',
        '--sysctl', 'net.ipv6.conf.default.disable_ipv6=1',
        '--sysctl', 'net.ipv6.conf.lo.disable_ipv6=1',
        # Pin DNS to public resolvers (matching the firewall allowlist)
        # so a tampered /etc/resolv.conf or hijacked Docker daemon
        # resolver can't redirect lookups to an attacker-controlled
        # server.
        '--dns', '1.1.1.1',
        '--dns', '1.0.0.1',
        '--hostname', 'kato-sandbox',
        '-v', f'{workspace}:{_WORKSPACE_MOUNT}:rw',
        # Auth volume: read-only source mount. Entrypoint copies an
        # allowlisted subset of files into the per-task .claude tmpfs.
        # See entrypoint.sh + ``_AUTH_SOURCE_MOUNT`` for the full
        # rationale. ``ro`` prevents this task writing back to the
        # operator's persistent credential store.
        '-v', f'{_AUTH_VOLUME_NAME}:{_AUTH_SOURCE_MOUNT}:ro',
        # Per-task writable .claude — destroyed on container exit so
        # nothing this task does can persist into the next task.
        # ``nosuid``/``nodev`` block setuid binaries / device nodes
        # being smuggled in. Owner is fixed up in entrypoint.sh
        # (chown to claude:users) before Claude is exec'd.
        '--tmpfs', f'{_CLAUDE_HOME}/.claude:rw,nosuid,nodev,size=64m,mode=0700',
        '-w', _WORKSPACE_MOUNT,
    ])
    for var in _PASS_THROUGH_ENV:
        if var in os.environ:
            # `-e VAR` (no value) means "pass through from the host
            # env" — keeps the secret out of the docker argv that
            # shows up in `ps`.
            argv.extend(['-e', var])
    # JIT image-identity pin: resolve the *current* digest of the tag
    # right now and refer to the image by ``tag@sha256:<digest>`` in
    # the docker run argv. Defends against a TOCTOU where someone with
    # local Docker access retags ``kato/claude-sandbox:latest`` to a
    # different image after ``ensure_image`` returned. If the digest
    # can't be resolved we fail closed rather than fall back to the
    # bare tag — losing the integrity check is not acceptable.
    #
    # Distinguish ``missing`` (rebuild) vs ``transient`` (retry) so
    # the operator's response is clear and they don't reach for an
    # insecure bypass env var.
    try:
        digest = _image_digest_strict(image_tag)
    except _DigestLookupError as exc:
        if exc.kind == 'missing':
            raise SandboxError(
                f'sandbox image {image_tag} is missing from the local '
                f'Docker cache: {exc}. Run ``make sandbox-build`` and '
                'retry. (kato refuses to spawn without a JIT-pinned '
                'image digest.)',
            ) from exc
        raise SandboxError(
            f'cannot resolve sandbox image digest for {image_tag} '
            f'(transient): {exc}. The Docker daemon may be busy or '
            'restarting — retry shortly. If this persists, run '
            '``docker info`` to diagnose. (kato refuses to spawn '
            'without a JIT-pinned image digest; do not work around '
            'this with an env-var bypass — investigate the daemon.)',
        ) from exc
    if digest.startswith('sha256:'):
        argv.append(f'{image_tag}@{digest}')
    else:
        argv.append(f'{image_tag}@sha256:{digest.split(":")[-1]}')
    # Defense-in-depth: refuse to ever pass ``--security-opt
    # seccomp=unconfined`` even if a future maintainer copies a bad
    # config. Run this last so the check sees the final argv.
    _assert_seccomp_not_unconfined(argv)
    argv.extend(inner_command)
    return argv


# ----- pre-spawn workspace secret scan -----

# File names that strongly indicate operator credentials, not normal
# committed source. Bare ``.env`` is suspicious; ``.env.example`` /
# ``.env.sample`` / ``.env.template`` are not (those are intentional
# scaffolding). Private SSH keys (``id_rsa``, ``id_ed25519``,
# ``id_ecdsa``) are always suspicious. ``credentials`` files under
# ``.aws`` / ``gcloud`` are always suspicious. Public keys (``*.pub``)
# are fine.
_SUSPICIOUS_FILE_NAMES = frozenset({
    '.env',
    '.env.local',
    '.env.production',
    '.env.prod',
    '.env.staging',
    '.netrc',
    '.git-credentials',
    'id_rsa',
    'id_ed25519',
    'id_ecdsa',
    'id_dsa',
    'credentials.json',
})

# Path-suffix matches: anything ending in these treats the whole
# subtree as suspicious. Exact-match path components (case-sensitive).
_SUSPICIOUS_PATH_SUFFIXES = (
    '.aws/credentials',
    '.aws/config',
    '.gcp/credentials.json',
    '.config/gcloud/credentials.db',
    '.config/gcloud/application_default_credentials.json',
    '.kube/config',
    '.docker/config.json',
)

# Hard cap so a workspace with thousands of files doesn't make the
# preflight noticeably slower. ``rglob`` is depth-first; once we hit
# the cap we stop scanning and warn that scan was truncated.
_SECRET_SCAN_FILE_CAP = 20_000

# Per-file size cap for the content-pattern scan. Anything bigger is
# almost certainly a binary blob, generated artifact, or vendored
# dependency — none of which are likely places for a hand-pasted
# credential, and reading multi-megabyte files into memory for a
# single grep is wasted work.
_SECRET_SCAN_PER_FILE_BYTES_CAP = 1_048_576  # 1 MiB

# Directories we skip during the content-pattern scan. They contain
# generated artifacts, vendored deps, or VCS internals that legitimately
# carry tokens (npm registry tarballs, git pack files); scanning them
# produces noise without protecting against the actual leak path
# (operator-written secrets in source files).
_CONTENT_SCAN_SKIP_DIRS: frozenset[str] = frozenset({
    '.git', 'node_modules', 'venv', '.venv', '__pycache__',
    'dist', 'build', 'target', '.tox', '.pytest_cache', '.mypy_cache',
})


def scan_workspace_for_secrets(
    workspace_path: str,
    *,
    logger: logging.Logger | None = None,
) -> list[str]:
    """Walk the workspace looking for committed-secret signals.

    Two signals are considered, in order of preference:

      1. **File name match** — the file's name is one of the
         ``_SUSPICIOUS_FILE_NAMES`` / ``_SUSPICIOUS_PATH_SUFFIXES``
         patterns (e.g. ``.env``, ``id_rsa``, ``.aws/credentials``).
         Cheap, broad, false-positive-prone; the operator override
         exists for the false-positive cases.
      2. **File content match** — the file contains a high-confidence
         credential pattern (AWS key id, GitHub token, OpenAI key, …)
         per ``kato.sandbox.credential_patterns``. Closes the case
         where a secret is committed to a file with an innocuous
         name (`config.yaml`, a migration, a README). Skipped for
         binary files, files larger than 1 MiB, and directories
         that are known to carry generated tokens (`.git`,
         `node_modules`, `venv`, `dist`, `build`, …).

    Returns the list of relative paths that match (empty if none).
    Each match is annotated in the returned string: file-name matches
    are bare paths; content matches carry a ``(content: <pattern>)``
    suffix so the operator and the audit log can distinguish them.
    """
    from sandbox_core_lib.sandbox_core_lib.credential_patterns import find_credential_patterns

    try:
        root = Path(workspace_path).resolve()
    except (OSError, RuntimeError):
        return []
    if not root.is_dir():
        return []
    findings: list[str] = []
    scanned = 0
    truncated = False
    try:
        for entry in root.rglob('*'):
            scanned += 1
            if scanned > _SECRET_SCAN_FILE_CAP:
                truncated = True
                break
            if not entry.is_file():
                continue
            relative_str = str(entry.relative_to(root))
            # File-name signal first — cheap, no I/O.
            if entry.name in _SUSPICIOUS_FILE_NAMES:
                findings.append(relative_str)
                continue
            matched_suffix = False
            for suffix in _SUSPICIOUS_PATH_SUFFIXES:
                if relative_str == suffix or relative_str.endswith('/' + suffix):
                    findings.append(relative_str)
                    matched_suffix = True
                    break
            if matched_suffix:
                continue
            # Content signal — skip generated / vendored trees, binary
            # files, and large files. Reads at most 1 MiB per file.
            relative_parts = entry.relative_to(root).parts
            if any(part in _CONTENT_SCAN_SKIP_DIRS for part in relative_parts):
                continue
            try:
                if entry.stat().st_size > _SECRET_SCAN_PER_FILE_BYTES_CAP:
                    continue
            except OSError:
                continue
            try:
                # ``errors='ignore'`` quietly drops bytes that aren't
                # valid UTF-8 — credential patterns are ASCII so this
                # cannot cause a false negative for the patterns we
                # actually look for.
                text = entry.read_text(encoding='utf-8', errors='ignore')
            except (OSError, PermissionError):
                continue
            content_findings = find_credential_patterns(text)
            if content_findings:
                # One annotated line per (file, pattern_name) pair so
                # the operator sees every distinct signal. The redacted
                # preview is intentionally NOT included in the workspace
                # findings list — the file path alone is enough to
                # locate the leak; the pattern name is enough to know
                # what was found.
                seen_patterns: set[str] = set()
                for finding in content_findings:
                    if finding.pattern_name in seen_patterns:
                        continue
                    seen_patterns.add(finding.pattern_name)
                    findings.append(
                        f'{relative_str} (content: {finding.pattern_name})'
                    )
    except (OSError, PermissionError):
        # Best-effort: if we can't traverse a subtree we just log
        # what we found so far and move on.
        pass
    if findings and logger is not None:
        head = ', '.join(findings[:5])
        rest = f' (+{len(findings) - 5} more)' if len(findings) > 5 else ''
        truncated_note = ' (scan truncated at 20,000 files)' if truncated else ''
        logger.warning(
            'sandbox workspace %s contains %d file(s) that look like '
            'operator credentials Claude will be able to read: %s%s%s. '
            'If these are intentional repo fixtures, ignore. If not, '
            'remove or .gitignore them before continuing.',
            root, len(findings), head, rest, truncated_note,
        )
    return findings


def enforce_no_workspace_secrets(
    workspace_path: str,
    *,
    env: dict | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Refuse to spawn the sandbox when the workspace looks like it
    contains committed secrets.

    Reasoning: kato cloned this workspace from a remote, so anything
    here is something *the team committed and pushed*. A `.env`,
    `id_rsa`, or `.aws/credentials` in a remote-tracked repo is
    almost always an operator mistake — surfaced as a hard refusal
    so the team fixes it instead of shipping the next 1000 PRs with
    the leak still in tree.

    The override ``KATO_SANDBOX_ALLOW_WORKSPACE_SECRETS=true`` exists
    for legitimate cases (security-research repos, intentional test
    fixtures whose names happen to match) — operator's explicit call.
    """
    findings = scan_workspace_for_secrets(workspace_path, logger=logger)
    if not findings:
        return
    if _env_flag_true(env, ALLOW_WORKSPACE_SECRETS_ENV_KEY):
        if logger is not None:
            logger.warning(
                'proceeding with %d workspace secret-shaped file(s) — '
                '%s=true override is set; operator accepted',
                len(findings), ALLOW_WORKSPACE_SECRETS_ENV_KEY,
            )
        return
    head = ', '.join(findings[:10])
    rest = f' (+{len(findings) - 10} more)' if len(findings) > 10 else ''
    raise SandboxError(
        f'workspace at {workspace_path} contains {len(findings)} file(s) '
        f'that look like committed secrets — kato refuses to launch the '
        f'sandbox so the leak is fixed at source rather than ignored: '
        f'{head}{rest}. Either remove the files and add them to '
        f'.gitignore, or set {ALLOW_WORKSPACE_SECRETS_ENV_KEY}=true to '
        f'override (only do this if these are intentional repo fixtures).'
    )


# ----- audit log + container naming -----

def make_container_name(task_id: str = '') -> str:
    """Deterministic-ish container name for ``docker ps`` / audit grep.

    Embeds the task id (or ``unknown``) plus a short uuid suffix so
    parallel spawns don't collide and the operator can find their
    task's container at a glance with ``docker ps | grep UNA-1495``.
    """
    safe_task = ''.join(
        ch if ch.isalnum() or ch in '-_' else '_'
        for ch in (str(task_id or 'unknown') or 'unknown')
    )[:48]
    return f'kato-sandbox-{safe_task}-{uuid.uuid4().hex[:8]}'


_AUDIT_GENESIS_HASH = '0' * 64

# Spawn-rate guard. A buggy task scan loop or a malicious orchestrator
# can otherwise spam ``docker run`` until the host is wedged. The
# limit is generous for legitimate parallelism (a ~3-task pipeline
# spinning up retries) but catches a runaway. Counts entries in the
# audit log within ``_SPAWN_RATE_WINDOW_SEC``; refuses if at/over
# ``_SPAWN_RATE_LIMIT``.
_SPAWN_RATE_WINDOW_SEC = 60
_SPAWN_RATE_LIMIT = 30


def _last_audit_chain_hash(target: Path) -> str:
    """Return ``sha256(last_line_text)`` of the audit log, or genesis.

    The chain hash is built over the raw bytes of each prior line
    *including* its own ``prev_hash`` field, so any single-entry
    edit invalidates every subsequent entry's chain link. Operators
    can verify the chain offline with ``sha256sum`` per line.

    NOTE: callers that need the read+write to be atomic across
    parallel spawns must wrap this call (and the subsequent write)
    in ``_exclusive_file_lock(target)``. ``record_spawn`` does that.
    """
    if not target.exists():
        return _AUDIT_GENESIS_HASH
    try:
        with target.open('rb') as fh:
            try:
                fh.seek(-4096, os.SEEK_END)
            except OSError:
                fh.seek(0)
            tail = fh.read()
    except OSError:
        return _AUDIT_GENESIS_HASH
    lines = [ln for ln in tail.splitlines() if ln.strip()]
    if not lines:
        return _AUDIT_GENESIS_HASH
    return hashlib.sha256(lines[-1]).hexdigest()


def _count_recent_spawns(target: Path, *, now: datetime | None = None) -> int:
    """Count audit-log entries within ``_SPAWN_RATE_WINDOW_SEC``.

    Helper — does NOT take the audit lock. Callers that need the
    count to be consistent with a subsequent write must hold
    ``_exclusive_file_lock(target)`` themselves. ``record_spawn``
    does this; the standalone ``check_spawn_rate`` below also takes
    the lock for its read-only callers.
    """
    if not target.exists():
        return 0
    cutoff = (now or datetime.now(timezone.utc)).timestamp() - _SPAWN_RATE_WINDOW_SEC
    count = 0
    try:
        with target.open('rb') as fh:
            try:
                fh.seek(-65536, os.SEEK_END)
            except OSError:
                fh.seek(0)
            tail = fh.read().decode('utf-8', errors='replace')
    except OSError:
        return 0
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = entry.get('timestamp', '')
        try:
            entry_time = datetime.fromisoformat(ts).timestamp()
        except (ValueError, TypeError):
            continue
        if entry_time >= cutoff:
            count += 1
    return count


def check_spawn_rate(
    audit_log_path: Path | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Lock-protected variant of ``_count_recent_spawns`` for external callers.

    Raises ``SandboxError`` if the count is at or above the limit. The
    authoritative atomic check is inside ``record_spawn`` (so parallel
    spawns can't both see ``N-1`` and both proceed); this function
    exists for callers (UI / tooling) that want a peek without writing.
    """
    target = audit_log_path or _DEFAULT_AUDIT_LOG_PATH
    with _exclusive_file_lock(target):
        count = _count_recent_spawns(target, now=now)
    if count >= _SPAWN_RATE_LIMIT:
        raise SandboxError(
            f'sandbox spawn rate exceeded: {count} spawns in the last '
            f'{_SPAWN_RATE_WINDOW_SEC}s (limit {_SPAWN_RATE_LIMIT}). '
            'Refusing to launch — investigate the caller.',
        )
    return count


# Operator override: when set, a failure to append to the audit log
# *blocks the spawn* instead of just warning. Default-off so a stuck
# disk doesn't take kato down on a normal box; safety-conscious
# operators can opt into "no audit, no spawn".
AUDIT_REQUIRED_ENV_KEY = 'KATO_SANDBOX_AUDIT_REQUIRED'


def record_spawn(
    *,
    task_id: str,
    container_name: str,
    workspace_path: str,
    image_tag: str = SANDBOX_IMAGE_TAG,
    audit_log_path: Path | None = None,
    logger: logging.Logger | None = None,
    env: dict | None = None,
) -> None:
    """Append one JSON line per sandboxed spawn to the audit log.

    Each line embeds ``prev_hash`` = ``sha256`` of the previous line's
    raw bytes, so any single-entry edit invalidates the chain from
    that point forward. Verifiable offline with ``sha256sum`` — no
    secret needed for tamper-evidence.

    Best-effort by default: a write failure logs to stderr + warning
    but does not abort the spawn (a stuck disk shouldn't take kato
    down). Operators can flip this to fail-closed by setting
    ``KATO_SANDBOX_AUDIT_REQUIRED=true``, in which case any audit
    write failure raises ``SandboxError`` and the spawn is refused.
    """
    target = audit_log_path or _DEFAULT_AUDIT_LOG_PATH
    # Hold the audit lock for the entire critical section: count
    # recent spawns → check rate limit → read prev_hash → write
    # entry → fsync. Without this, two parallel spawns can each see
    # ``N-1`` recent entries (admitting one over the limit) AND each
    # compute their ``prev_hash`` against the same predecessor
    # (leaving one chain link invalid). Per-file lock via
    # ``<path>.lock``.
    try:
        with _exclusive_file_lock(target):
            recent = _count_recent_spawns(target)
            if recent >= _SPAWN_RATE_LIMIT:
                raise SandboxError(
                    f'sandbox spawn rate exceeded: {recent} spawns in '
                    f'the last {_SPAWN_RATE_WINDOW_SEC}s (limit '
                    f'{_SPAWN_RATE_LIMIT}). Refusing to launch — '
                    'investigate the caller.',
                )
            prev_hash = _last_audit_chain_hash(target)
            entry = {
                'timestamp': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'event': 'spawn',
                'task_id': str(task_id or ''),
                'container_name': container_name,
                'image_tag': image_tag,
                'image_digest': _image_digest(image_tag) or '',
                'workspace_path': workspace_path,
                'prev_hash': prev_hash,
            }
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(target.parent, 0o700)
            except OSError:
                pass
            fd = os.open(
                str(target),
                os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                0o600,
            )
            try:
                line = (json.dumps(entry, ensure_ascii=False) + '\n').encode('utf-8')
                os.write(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                dir_fd = os.open(str(target.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
    except SandboxError:
        raise
    except OSError as exc:
        if _env_flag_true(env, AUDIT_REQUIRED_ENV_KEY):
            raise SandboxError(
                f'failed to write sandbox audit log entry to {target}: '
                f'{exc} — refusing to spawn ({AUDIT_REQUIRED_ENV_KEY}=true)'
            ) from exc
        msg = (
            f'[kato-sandbox] WARNING: failed to write sandbox audit '
            f'log entry to {target}: {exc} — spawn proceeded but is '
            f'NOT recorded in the audit trail. Set '
            f'{AUDIT_REQUIRED_ENV_KEY}=true to fail-close on this.'
        )
        sys.stderr.write(msg + '\n')
        sys.stderr.flush()
        if logger is not None:
            logger.warning(msg)

    # External audit-log shipping (OG2). Best-effort by default;
    # operators who want fail-closed shipping set
    # ``KATO_SANDBOX_AUDIT_SHIP_REQUIRED=true``. Runs AFTER the local
    # write so the local log is the authoritative copy and a sink
    # failure can never lose the entry. Closes the tail-truncation
    # residual: an external sink is the operator's reference for
    # "did the local file lose entries" verification.
    from sandbox_core_lib.sandbox_core_lib.audit_log_shipping import (
        AuditShipError, ship_audit_entry,
    )
    try:
        ship_audit_entry(entry, env=env, logger=logger)
    except AuditShipError as exc:
        # Only re-raised when ``KATO_SANDBOX_AUDIT_SHIP_REQUIRED=true`` —
        # ``ship_audit_entry`` already swallows otherwise.
        raise SandboxError(
            f'audit-log shipping failed: {exc} — refusing to spawn '
            f'(KATO_SANDBOX_AUDIT_SHIP_REQUIRED=true)'
        ) from exc


class _DigestLookupError(RuntimeError):
    """Internal: distinguishes 'no such image' from 'daemon transient'.

    ``kind`` is one of ``'missing'`` (rebuild fixes it) or
    ``'transient'`` (retry fixes it). Callers that need a clean
    operator diagnostic — e.g. ``wrap_command`` — can branch on
    this; older callers that just want a string treat any failure
    as empty.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _image_digest(image_tag: str) -> str:
    """Best-effort: return the local image digest, empty string on failure."""
    try:
        return _image_digest_strict(image_tag)
    except _DigestLookupError:
        return ''


def _image_digest_strict(image_tag: str) -> str:
    """Like ``_image_digest`` but raises ``_DigestLookupError`` on failure.

    Distinguishes the two operationally distinct failure modes:

    * ``missing``    — daemon answered, image not present. Fix: rebuild.
    * ``transient``  — daemon couldn't be reached or timed out. Fix: retry.

    Used by ``wrap_command`` so the operator sees a diagnostic that
    points at the actual remedy instead of a generic "digest
    unresolvable" that invites them to add an insecure bypass env var.
    """
    try:
        result = subprocess.run(
            [
                'docker', 'image', 'inspect',
                '--format', '{{ index .Id }}',
                image_tag,
            ],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        raise _DigestLookupError(
            'transient',
            f'docker image inspect timed out for {image_tag} ({exc}); '
            'daemon may be busy — retry, then check ``docker info``',
        ) from exc
    except OSError as exc:
        raise _DigestLookupError(
            'transient',
            f'cannot invoke docker for {image_tag} ({exc}); '
            'daemon may be down — start docker and retry',
        ) from exc
    if result.returncode != 0:
        stderr = (result.stderr or '').strip().lower()
        if 'no such image' in stderr or 'not found' in stderr:
            raise _DigestLookupError(
                'missing',
                f'image {image_tag} not present in local cache; '
                'run ``make sandbox-build`` to (re)build it',
            )
        raise _DigestLookupError(
            'transient',
            f'docker image inspect for {image_tag} returned '
            f'rc={result.returncode}: {stderr or "(no stderr)"}',
        )
    digest = result.stdout.strip()
    if not digest:
        raise _DigestLookupError(
            'transient',
            f'docker returned an empty digest for {image_tag}',
        )
    return digest


def _assert_seccomp_not_unconfined(argv: list[str]) -> None:
    """Refuse the spawn if any flag in ``argv`` disables seccomp.

    Docker's default seccomp profile is a meaningful additional syscall
    blockade on top of cap-drop ALL + bounding-set wipe (e.g. it blocks
    ``unshare(CLONE_NEWUSER)`` for non-privileged containers, which
    historically hosted a stream of kernel CVEs). Any future change
    that adds ``--security-opt seccomp=unconfined`` silently downgrades
    the security model — fail closed instead.
    """
    for i, tok in enumerate(argv):
        flat = tok
        if i + 1 < len(argv) and tok == '--security-opt':
            flat = argv[i + 1]
        if 'seccomp=unconfined' in flat:
            raise SandboxError(
                'sandbox argv contains seccomp=unconfined — refusing '
                'to spawn. The default seccomp profile is required.',
            )


def login_command(image_tag: str = SANDBOX_IMAGE_TAG) -> list[str]:
    """One-time interactive ``claude /login`` invocation for the sandbox.

    Run this from a normal terminal (``-it``, not piped) to seed the
    persistent auth volume with the operator's credentials. After
    this, kato-spawned sandbox containers reuse the same volume —
    but only via a **read-only** source mount; the credentials are
    copied (allowlisted basenames only) into a per-task tmpfs at
    spawn time, so this login flow is the *only* path that writes
    the persistent volume.

    Uses the same hardening as ``wrap_command`` minus the workspace
    mount (login doesn't touch task files). The auth volume is
    mounted **read-write** here (and only here) so the operator's
    typed credentials persist across containers.
    """
    return [
        'docker', 'run',
        '--rm',
        '-it',
        '--init',
        '--label', 'org.kato.sandbox=true',
        '--label', 'org.kato.task-id=login',
        '--label', f'org.kato.auth-volume={_AUTH_VOLUME_NAME}',
        '--network', _SANDBOX_NETWORK_NAME,
        '--ipc=none',
        '--cgroupns=private',
        '--pid=container',
        '--uts=private',
        '--cap-drop', 'ALL',
        '--cap-add', 'NET_ADMIN',
        '--cap-add', 'NET_RAW',
        '--cap-add', 'SETUID',
        '--cap-add', 'SETGID',
        '--security-opt', 'no-new-privileges',
        '--security-opt', 'apparmor=docker-default',
        '--read-only',
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m',
        '--tmpfs', '/run:rw,nosuid,nodev,size=8m',
        '--shm-size=32m',
        '--memory', '512m',
        '--memory-swap', '512m',
        '--pids-limit', '128',
        '--ulimit', 'nofile=1024:1024',
        '--ulimit', 'nproc=64:64',
        '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1',
        '--sysctl', 'net.ipv6.conf.default.disable_ipv6=1',
        '--sysctl', 'net.ipv6.conf.lo.disable_ipv6=1',
        '--dns', '1.1.1.1',
        '--dns', '1.0.0.1',
        '--hostname', 'kato-sandbox-login',
        # Login mode: auth volume RW directly at .claude (no
        # /auth-src mount, no tmpfs). Entrypoint detects the absence
        # of /auth-src and skips the copy-in step.
        '-v', f'{_AUTH_VOLUME_NAME}:{_CLAUDE_HOME}/.claude:rw',
        image_tag,
        'claude', '/login',
    ]


def stamp_auth_volume_manifest(
    image_tag: str = SANDBOX_IMAGE_TAG,
    *,
    logger: logging.Logger | None = None,
) -> None:
    """Refresh the integrity manifest stored inside the auth volume.

    Call this immediately after a successful ``claude /login``: it
    spins up a one-shot root container, writes
    ``manifest.sha256`` containing ``sha256(.credentials.json)``
    (and any other allowlisted credential file present), then exits.

    Subsequent **spawn-mode** containers verify this manifest in
    ``entrypoint.sh`` and refuse to start if a credential file's
    hash doesn't match — i.e. someone tampered with the volume out
    of band (manual ``docker volume`` edit, sibling container with
    the same volume mounted RW, etc.). Login mode skips the check
    because it is the legitimate path that mutates the volume.

    Idempotent and best-effort: a manifest-write failure is logged
    at warning level but never aborts. Operators can re-run
    ``make sandbox-login`` to refresh the manifest at any time.
    """
    cmd = [
        'docker', 'run',
        '--rm',
        '--init',
        '--network', 'none',           # manifest writer needs no egress
        '--ipc=none',
        '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges',
        '--read-only',
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=8m',
        '--memory', '128m',
        '--memory-swap', '128m',
        '--pids-limit', '32',
        '-v', f'{_AUTH_VOLUME_NAME}:/auth:rw',
        '--entrypoint', '/bin/bash',
        image_tag,
        '-c',
        # Two-line script: list allowlisted basenames present, then
        # write a fresh manifest. The shell is exec'd as root inside
        # the container (we explicitly DROP all caps and forbid
        # privilege escalation, so root here can do approximately
        # nothing except write to /auth, which is the point).
        'cd /auth && '
        'rm -f manifest.sha256 && '
        'for f in .credentials.json credentials.json; do '
        '  [ -f "$f" ] && sha256sum "$f" >> manifest.sha256; '
        'done; '
        '[ -f manifest.sha256 ] && chmod 600 manifest.sha256 || true',
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if logger is not None:
            logger.warning(
                'failed to stamp auth volume manifest (%s); '
                'subsequent spawns will skip integrity check',
                exc,
            )
        return
    if result.returncode != 0 and logger is not None:
        logger.warning(
            'auth volume manifest write returned non-zero: %s',
            result.stderr.strip() or '(no stderr)',
        )
