"""Full coverage for runners/detect_secrets_runner.py.

All detect-secrets library calls are mocked — no detect-secrets install required.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from security_scanner_core_lib.security_scanner_core_lib.runners._helpers import (
    RunnerUnavailableError,
)
from security_scanner_core_lib.security_scanner_core_lib.runners.detect_secrets_runner import (
    _severity_for,
    run,
)
from security_scanner_core_lib.security_scanner_core_lib.security_finding import Severity


# ---------------------------------------------------------------------------
# _severity_for — plugin name → severity mapping
# ---------------------------------------------------------------------------


class SeverityForTests(unittest.TestCase):
    def test_known_critical_plugins(self) -> None:
        critical = [
            'AWS Access Key', 'GitHub Token', 'Private Key',
            'Stripe Access Key', 'GCP API Key', 'GitLab Token',
            'Slack Token', 'OpenAI Token',
        ]
        for plugin in critical:
            self.assertEqual(_severity_for(plugin), Severity.CRITICAL,
                             f'{plugin!r} should be CRITICAL')

    def test_known_high_plugins(self) -> None:
        self.assertEqual(_severity_for('Hex High Entropy String'), Severity.HIGH)
        self.assertEqual(_severity_for('Base64 High Entropy String'), Severity.HIGH)
        self.assertEqual(_severity_for('JSON Web Token'), Severity.HIGH)
        self.assertEqual(_severity_for('Basic Auth Credentials'), Severity.HIGH)

    def test_known_medium_plugin(self) -> None:
        self.assertEqual(_severity_for('Secret Keyword'), Severity.MEDIUM)

    def test_unknown_plugin_defaults_to_high(self) -> None:
        self.assertEqual(_severity_for('Some Future Plugin'), Severity.HIGH)
        self.assertEqual(_severity_for(''), Severity.HIGH)


# ---------------------------------------------------------------------------
# run() — detect-secrets not installed
# ---------------------------------------------------------------------------


class DetectSecretsNotInstalledTests(unittest.TestCase):
    def test_raises_runner_unavailable_on_import_error(self) -> None:
        with patch.dict('sys.modules', {
            'detect_secrets': None,
            'detect_secrets.settings': None,
        }):
            with self.assertRaises((RunnerUnavailableError, ImportError)):
                run('/workspace')


# ---------------------------------------------------------------------------
# run() — workspace and scan behaviour
# ---------------------------------------------------------------------------


def _make_mock_secret(secret_type: str, line_number: int, filename: str) -> MagicMock:
    s = MagicMock()
    s.type = secret_type
    s.line_number = line_number
    return s


class DetectSecretsRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def _write(self, name: str, content: str = '') -> Path:
        p = self.workspace / name
        p.write_text(content)
        return p

    def _run_with_mock_collection(self, secrets_iterable):
        """Run the runner with a mock SecretsCollection."""
        mock_collection_instance = MagicMock()
        mock_collection_instance.__iter__ = MagicMock(
            return_value=iter(secrets_iterable)
        )
        mock_collection_class = MagicMock(return_value=mock_collection_instance)
        mock_settings_ctx = MagicMock()
        mock_settings_ctx.__enter__ = MagicMock(return_value=None)
        mock_settings_ctx.__exit__ = MagicMock(return_value=False)
        mock_default_settings = MagicMock(return_value=mock_settings_ctx)

        mock_ds = MagicMock()
        mock_ds.SecretsCollection = mock_collection_class
        mock_ds_settings = MagicMock()
        mock_ds_settings.default_settings = mock_default_settings

        with patch.dict('sys.modules', {
            'detect_secrets': mock_ds,
            'detect_secrets.settings': mock_ds_settings,
        }):
            return run(str(self.workspace))

    def test_non_directory_workspace_returns_empty(self) -> None:
        mock_collection_instance = MagicMock()
        mock_collection_instance.__iter__ = MagicMock(return_value=iter([]))
        mock_collection_class = MagicMock(return_value=mock_collection_instance)
        mock_settings_ctx = MagicMock()
        mock_settings_ctx.__enter__ = MagicMock(return_value=None)
        mock_settings_ctx.__exit__ = MagicMock(return_value=False)
        mock_ds = MagicMock()
        mock_ds.SecretsCollection = mock_collection_class
        mock_ds_settings = MagicMock()
        mock_ds_settings.default_settings = MagicMock(return_value=mock_settings_ctx)

        with patch.dict('sys.modules', {
            'detect_secrets': mock_ds,
            'detect_secrets.settings': mock_ds_settings,
        }):
            result = run('/nonexistent/path')
        self.assertEqual(result, [])

    def test_no_secrets_returns_empty_list(self) -> None:
        self._write('app.py', 'x = 1')
        result = self._run_with_mock_collection([])
        self.assertEqual(result, [])

    def test_finding_has_correct_fields(self) -> None:
        self._write('config.py', 'KEY = "AKIAxxxxxxxx"')
        secret = _make_mock_secret('AWS Access Key', 1, str(self.workspace / 'config.py'))
        result = self._run_with_mock_collection(
            [(str(self.workspace / 'config.py'), secret)]
        )
        self.assertEqual(len(result), 1)
        f = result[0]
        self.assertEqual(f.tool, 'detect-secrets')
        self.assertEqual(f.severity, Severity.CRITICAL)
        self.assertEqual(f.rule_id, 'AWS Access Key')
        self.assertEqual(f.line, 1)

    def test_finding_message_contains_rotation_advice(self) -> None:
        secret = _make_mock_secret('GitHub Token', 5, str(self.workspace / 'f.py'))
        result = self._run_with_mock_collection(
            [(str(self.workspace / 'f.py'), secret)]
        )
        self.assertIn('rotate', result[0].message.lower())

    def test_multiple_findings_returned(self) -> None:
        s1 = _make_mock_secret('AWS Access Key', 1, str(self.workspace / 'a.py'))
        s2 = _make_mock_secret('GitHub Token', 2, str(self.workspace / 'b.py'))
        result = self._run_with_mock_collection([
            (str(self.workspace / 'a.py'), s1),
            (str(self.workspace / 'b.py'), s2),
        ])
        self.assertEqual(len(result), 2)

    def test_secret_type_in_metadata(self) -> None:
        secret = _make_mock_secret('Private Key', 3, str(self.workspace / 'k.pem'))
        result = self._run_with_mock_collection(
            [(str(self.workspace / 'k.pem'), secret)]
        )
        meta = dict(result[0].metadata)
        self.assertEqual(meta.get('secret_type'), 'Private Key')

    def test_unknown_secret_type_gets_high_severity(self) -> None:
        secret = _make_mock_secret('Some Future Plugin', 7, str(self.workspace / 'f.py'))
        result = self._run_with_mock_collection(
            [(str(self.workspace / 'f.py'), secret)]
        )
        self.assertEqual(result[0].severity, Severity.HIGH)


class DetectSecretsDefensiveBranchTests(unittest.TestCase):
    """Lines 107-113: ``scan_file`` raising for binary files → log + continue.
    Lines 144-146: ``_files_to_scan`` recurses into subdirectories.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def test_scan_file_exception_logs_and_continues(self) -> None:
        # Files exist that will trigger scan_file → make scan_file raise on
        # one of them. The runner must log debug and skip, not crash.
        (self.workspace / 'good.py').write_text('hi')
        (self.workspace / 'bad.bin').write_bytes(b'\x00\x01\x02')

        mock_collection_instance = MagicMock()
        # Make scan_file raise on the .bin file only.
        def selective_scan(path_str):
            if path_str.endswith('.bin'):
                raise RuntimeError('binary scan choked')
        mock_collection_instance.scan_file.side_effect = selective_scan
        mock_collection_instance.__iter__ = MagicMock(return_value=iter([]))
        mock_collection_class = MagicMock(return_value=mock_collection_instance)
        mock_settings_ctx = MagicMock()
        mock_settings_ctx.__enter__ = MagicMock(return_value=None)
        mock_settings_ctx.__exit__ = MagicMock(return_value=False)
        mock_default_settings = MagicMock(return_value=mock_settings_ctx)
        mock_ds = MagicMock(SecretsCollection=mock_collection_class)
        mock_ds_settings = MagicMock(default_settings=mock_default_settings)
        mock_logger = MagicMock()
        with patch.dict('sys.modules', {
            'detect_secrets': mock_ds,
            'detect_secrets.settings': mock_ds_settings,
        }):
            from security_scanner_core_lib.security_scanner_core_lib.runners.detect_secrets_runner import (
                run as run_detect_secrets,
            )
            result = run_detect_secrets(str(self.workspace), logger=mock_logger)
        # No findings, no crash, debug log emitted for the bad file.
        self.assertEqual(result, [])
        mock_logger.debug.assert_called()

    def test_files_to_scan_recurses_into_subdirectories(self) -> None:
        # Lines 144-146: directory branch recurses (yields files in subdirs).
        from security_scanner_core_lib.security_scanner_core_lib.runners.detect_secrets_runner import (
            _files_to_scan,
        )
        (self.workspace / 'top.py').write_text('hi')
        sub = self.workspace / 'src'
        sub.mkdir()
        (sub / 'inner.py').write_text('world')
        # Also create an excluded dir.
        excluded = self.workspace / '.git'
        excluded.mkdir()
        (excluded / 'config').write_text('skip me')

        files = sorted(p.name for p in _files_to_scan(self.workspace))
        self.assertIn('top.py', files)
        self.assertIn('inner.py', files)
        # .git directory is excluded.
        self.assertNotIn('config', files)

    def test_scan_file_exception_without_logger_continues_silently(self) -> None:
        # Branch 108->113: ``logger is None`` skips ``logger.debug`` and
        # falls through to ``continue``. Locks the no-logger tolerance
        # path on a binary-file scan failure.
        (self.workspace / 'bad.bin').write_bytes(b'\x00\x01\x02')
        mock_collection_instance = MagicMock()
        mock_collection_instance.scan_file.side_effect = RuntimeError('boom')
        mock_collection_instance.__iter__ = MagicMock(return_value=iter([]))
        mock_collection_class = MagicMock(return_value=mock_collection_instance)
        mock_settings_ctx = MagicMock()
        mock_settings_ctx.__enter__ = MagicMock(return_value=None)
        mock_settings_ctx.__exit__ = MagicMock(return_value=False)
        mock_default_settings = MagicMock(return_value=mock_settings_ctx)
        mock_ds = MagicMock(SecretsCollection=mock_collection_class)
        mock_ds_settings = MagicMock(default_settings=mock_default_settings)
        with patch.dict('sys.modules', {
            'detect_secrets': mock_ds,
            'detect_secrets.settings': mock_ds_settings,
        }):
            from security_scanner_core_lib.security_scanner_core_lib.runners.detect_secrets_runner import (
                run as run_detect_secrets,
            )
            # No logger passed — exercises the False branch.
            result = run_detect_secrets(str(self.workspace))
        self.assertEqual(result, [])

    def test_files_to_scan_skips_broken_symlinks(self) -> None:
        # Branch 147->142: a child that is neither dir nor file (broken
        # symlink) must be skipped so the walker keeps going. Locks the
        # tolerance path against stray dangling links inside a repo.
        import os
        from security_scanner_core_lib.security_scanner_core_lib.runners.detect_secrets_runner import (
            _files_to_scan,
        )
        (self.workspace / 'real.py').write_text('hi')
        broken = self.workspace / 'broken_link'
        os.symlink(str(self.workspace / 'does_not_exist'), str(broken))
        files = sorted(p.name for p in _files_to_scan(self.workspace))
        self.assertIn('real.py', files)
        self.assertNotIn('broken_link', files)


if __name__ == '__main__':
    unittest.main()
