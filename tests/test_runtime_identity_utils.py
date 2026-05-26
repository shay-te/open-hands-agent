from pathlib import Path
import tempfile
import unittest

from kato_core_lib.helpers.runtime_identity_utils import runtime_source_fingerprint


class RuntimeIdentityUtilsTests(unittest.TestCase):
    def test_runtime_source_fingerprint_changes_when_runtime_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / 'AGENTS.md').write_text('alpha\n', encoding='utf-8')
            (root / 'Dockerfile').write_text('FROM python:3.11-slim\n', encoding='utf-8')
            (root / 'Makefile').write_text('test:\n\ttrue\n', encoding='utf-8')
            (root / 'docker-compose.yaml').write_text('services: {}\n', encoding='utf-8')
            (root / '.env.example').write_text('KATO_LOG_LEVEL=warning\n', encoding='utf-8')
            (root / 'kato').mkdir()
            (root / 'kato' / '__init__.py').write_text('', encoding='utf-8')
            (root / 'kato' / 'main.py').write_text('print("v1")\n', encoding='utf-8')
            (root / 'scripts').mkdir()
            (root / 'scripts' / 'run-local.sh').write_text('echo hello\n', encoding='utf-8')

            first = runtime_source_fingerprint(root)
            second = runtime_source_fingerprint(root)
            self.assertEqual(first, second)

            (root / 'kato' / 'main.py').write_text('print("v2")\n', encoding='utf-8')
            self.assertNotEqual(first, runtime_source_fingerprint(root))

    def test_runtime_source_fingerprint_ignores_caches_and_subdirectory_entries(self) -> None:
        # Line 34: ``if path.is_file() and not _is_ignored(...)`` —
        # rglob yields BOTH subdirectories (not a file) and entries
        # under ``__pycache__`` / ``.git`` / etc. (ignored prefixes).
        # Either disqualifier must short-circuit the append so cache
        # churn and intermediate directory inodes don't poison the
        # fingerprint.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / 'kato').mkdir()
            (root / 'kato' / '__init__.py').write_text('', encoding='utf-8')
            (root / 'kato' / 'main.py').write_text('print("v1")\n', encoding='utf-8')
            baseline = runtime_source_fingerprint(root)

            # Adding a subdirectory (not a file) — ``path.is_file()``
            # branch is False, fingerprint unchanged.
            (root / 'kato' / 'inner').mkdir()
            self.assertEqual(baseline, runtime_source_fingerprint(root))

            # Adding files under an ignored ``__pycache__`` — the
            # ``not _is_ignored(...)`` branch is False, fingerprint
            # unchanged.
            (root / 'kato' / '__pycache__').mkdir()
            (root / 'kato' / '__pycache__' / 'main.cpython-311.pyc').write_bytes(b'cached')
            self.assertEqual(baseline, runtime_source_fingerprint(root))
