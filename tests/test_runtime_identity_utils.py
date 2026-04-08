from pathlib import Path
import tempfile
import unittest

from kato.helpers.runtime_identity_utils import runtime_source_fingerprint


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
