"""``kato`` — the single operator entry point (replaces the Makefile).

Every former ``make <target>`` is now ``kato <subcommand>``::

    kato up                      # start kato locally (.env + run main)
    kato bootstrap               # one-time setup (venv + deps + .env)
    kato configure               # (re)generate .env
    kato doctor [--mode all|agent|openhands]
    kato test                    # run the unittest suite
    kato build-agent-server      # build the agent-server image
    kato sandbox build|login|verify
    kato compose-docker          # the containerized OpenHands compose flow

Subcommands delegate to the same cross-platform Python implementations
the Makefile used (``scripts/*.py`` + ``kato_core_lib`` modules), so
behaviour is identical and — because ``kato`` is a console script —
Windows operators get the one command from ``pip install -e .`` with no
``make`` and no ``python scripts\\...`` to remember.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from kato_core_lib.helpers.dotenv_utils import parse_dotenv_text

# kato_core_lib/cli.py -> repo root is two parents up. Matches
# scripts/_script_utils.REPO_ROOT so delegated scripts resolve the
# same tree whether invoked via ``kato`` or directly.
REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / 'scripts'


def _venv_python() -> str:
    """Prefer the project venv (the Makefile used ``$(VENV_PYTHON)`` for
    package-context targets); fall back to the current interpreter."""
    if os.name == 'nt':
        candidate = REPO_ROOT / '.venv' / 'Scripts' / 'python.exe'
    else:
        candidate = REPO_ROOT / '.venv' / 'bin' / 'python'
    return str(candidate) if candidate.exists() else sys.executable


def _run(cmd: list[str]) -> int:
    return subprocess.call(cmd, cwd=str(REPO_ROOT))


def _script(name: str, *args: str) -> int:
    """Run ``scripts/<name>`` with the current interpreter — these
    scripts intentionally bootstrap their own venv handling."""
    return _run([sys.executable, str(SCRIPTS / name), *args])


def _load_env(path: Path) -> dict[str, str]:
    """Minimal ``KEY=VALUE`` reader for the compose flow (the Makefile
    did ``set -a; . ./.env``). Ignores blanks/comments, strips one
    layer of surrounding quotes. Not a full shell parser — kato's
    ``.env`` is generated, so it stays simple key/value."""
    if not path.exists():
        return {}
    return parse_dotenv_text(path.read_text(encoding='utf-8'))


def cmd_up(_args: argparse.Namespace) -> int:
    return _script('run_local.py')


def cmd_bootstrap(_args: argparse.Namespace) -> int:
    return _script('bootstrap.py')


def cmd_configure(_args: argparse.Namespace) -> int:
    return _run([_venv_python(), str(SCRIPTS / 'generate_env.py'),
                 '--output', '.env'])


def cmd_doctor(args: argparse.Namespace) -> int:
    return _run([_venv_python(), '-m', 'kato_core_lib.validate_env',
                 '--env-file', '.env', '--mode', args.mode])


def cmd_test(_args: argparse.Namespace) -> int:
    return _run([_venv_python(), '-m', 'unittest', 'discover', '-s', 'tests'])


def cmd_build_agent_server(_args: argparse.Namespace) -> int:
    tag = os.environ.get('KATO_AGENT_SERVER_IMAGE_TAG', '1.12.0-python')
    return _run(['docker', 'build', '-t', f'kato-agent-server:{tag}',
                 str(REPO_ROOT / 'docker' / 'agent-server')])


def cmd_sandbox(args: argparse.Namespace) -> int:
    py = _venv_python()
    if args.action == 'build':
        return _run([py, '-c',
                     'from kato_core_lib.sandbox.manager import build_image; '
                     'build_image()'])
    if args.action == 'verify':
        return _run([py, '-m', 'kato_core_lib.sandbox.verify'])
    # login: seed the persistent kato-claude-config volume.
    return _run([py, '-c',
                 'from kato_core_lib.sandbox.manager import ensure_image, '
                 'login_command, stamp_auth_volume_manifest; '
                 'import subprocess, sys; ensure_image(); '
                 'rc = subprocess.call(login_command()); '
                 'stamp_auth_volume_manifest() if rc == 0 else None; '
                 'sys.exit(rc)'])


def cmd_compose_docker(_args: argparse.Namespace) -> int:
    """Port of the Makefile's ``compose-up-docker`` shell pipeline:
    load .env, fingerprint the source, pick compose profiles from the
    backend/testing flags, ``up --build -d``, then ``docker attach``
    the kato container."""
    env = os.environ.copy()
    env.update(_load_env(REPO_ROOT / '.env'))
    fingerprint = subprocess.run(
        [sys.executable, '-m',
         'kato_core_lib.helpers.runtime_identity_utils', '--root', '.'],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
    ).stdout.strip()
    env['KATO_SOURCE_FINGERPRINT'] = fingerprint

    profiles: list[str] = []
    if env.get('KATO_AGENT_BACKEND', 'openhands') != 'claude':
        profiles += ['--profile', 'openhands']
    if (env.get('OPENHANDS_SKIP_TESTING', 'false') != 'true'
            and env.get('OPENHANDS_TESTING_CONTAINER_ENABLED', 'false') == 'true'):
        profiles += ['--profile', 'testing']

    up = subprocess.call(
        ['docker', 'compose', *profiles, 'up', '--build', '-d'],
        cwd=str(REPO_ROOT), env=env,
    )
    if up != 0:
        return up
    result = subprocess.run(
        ['docker', 'compose', *profiles, 'ps', '-q', 'kato'],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, check=False,
    )
    container_id = result.stdout.strip()
    if not container_id:
        print('unable to determine kato container id', file=sys.stderr)
        return 1
    return subprocess.call(['docker', 'attach', container_id],
                           cwd=str(REPO_ROOT), env=env)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='kato',
        description='Kato operator CLI — the single entry point '
                    '(replaces the Makefile).',
    )
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('up', help='start kato locally (.env + run main)').set_defaults(func=cmd_up)
    sub.add_parser('bootstrap', help='one-time setup (venv + deps + .env)').set_defaults(func=cmd_bootstrap)
    sub.add_parser('configure', help='(re)generate .env').set_defaults(func=cmd_configure)

    doctor = sub.add_parser('doctor', help='validate the environment')
    doctor.add_argument('--mode', choices=['all', 'agent', 'openhands'],
                        default='all')
    doctor.set_defaults(func=cmd_doctor)

    sub.add_parser('test', help='run the unittest suite').set_defaults(func=cmd_test)
    sub.add_parser('build-agent-server',
                   help='build the agent-server Docker image').set_defaults(func=cmd_build_agent_server)

    sandbox = sub.add_parser('sandbox', help='hardened Claude sandbox image')
    sandbox.add_argument('action', choices=['build', 'login', 'verify'])
    sandbox.set_defaults(func=cmd_sandbox)

    sub.add_parser('compose-docker',
                   help='containerized OpenHands docker-compose flow').set_defaults(func=cmd_compose_docker)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
