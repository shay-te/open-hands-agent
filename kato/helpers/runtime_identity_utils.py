from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


_ROOT_FILES = (
    'AGENTS.md',
    'Dockerfile',
    'Makefile',
    'docker-compose.yaml',
    '.env.example',
)
_ROOT_DIRS = ('kato', 'scripts')
_IGNORED_PARTS = {'__pycache__', '.git', '.mypy_cache', '.pytest_cache', '.venv'}


def _is_ignored(path: Path) -> bool:
    return any(part in _IGNORED_PARTS for part in path.parts)


def _source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for relative_path in _ROOT_FILES:
        path = root / relative_path
        if path.is_file():
            files.append(path)
    for relative_dir in _ROOT_DIRS:
        directory = root / relative_dir
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob('*')):
            if path.is_file() and not _is_ignored(path.relative_to(root)):
                files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def runtime_source_fingerprint(root: Path | str | None = None) -> str:
    root_path = Path(root or Path(__file__).resolve().parents[2]).resolve()
    digest = hashlib.sha256()
    for path in _source_files(root_path):
        relative_path = path.relative_to(root_path).as_posix()
        digest.update(relative_path.encode('utf-8'))
        digest.update(b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Print the Kato source fingerprint.',
    )
    parser.add_argument(
        '--root',
        default='.',
        help='Repository root used to calculate the fingerprint.',
    )
    args = parser.parse_args(argv)
    print(runtime_source_fingerprint(args.root))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
