"""Full coverage for runners/safety_runner.py.

All subprocess calls are mocked — no safety binary required.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from security_scanner_core_lib.security_scanner_core_lib.runners._helpers import (
    RunnerUnavailableError,
)
from security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner import (
    _find_requirement_files,
    _severity_from_cvss,
    run,
)
from security_scanner_core_lib.security_scanner_core_lib.security_finding import Severity


# ---------------------------------------------------------------------------
# _severity_from_cvss
# ---------------------------------------------------------------------------


class SeverityFromCvssTests(unittest.TestCase):
    def test_none_returns_medium(self) -> None:
        self.assertEqual(_severity_from_cvss(None), Severity.MEDIUM)

    def test_9_point_0_returns_critical(self) -> None:
        self.assertEqual(_severity_from_cvss(9.0), Severity.CRITICAL)

    def test_above_9_returns_critical(self) -> None:
        self.assertEqual(_severity_from_cvss(10.0), Severity.CRITICAL)
        self.assertEqual(_severity_from_cvss(9.1), Severity.CRITICAL)

    def test_7_point_0_returns_high(self) -> None:
        self.assertEqual(_severity_from_cvss(7.0), Severity.HIGH)

    def test_between_7_and_9_returns_high(self) -> None:
        self.assertEqual(_severity_from_cvss(8.5), Severity.HIGH)
        self.assertEqual(_severity_from_cvss(7.1), Severity.HIGH)

    def test_4_point_0_returns_medium(self) -> None:
        self.assertEqual(_severity_from_cvss(4.0), Severity.MEDIUM)

    def test_between_4_and_7_returns_medium(self) -> None:
        self.assertEqual(_severity_from_cvss(5.0), Severity.MEDIUM)
        self.assertEqual(_severity_from_cvss(6.9), Severity.MEDIUM)

    def test_below_4_returns_low(self) -> None:
        self.assertEqual(_severity_from_cvss(3.9), Severity.LOW)
        self.assertEqual(_severity_from_cvss(1.0), Severity.LOW)

    def test_zero_returns_low(self) -> None:
        self.assertEqual(_severity_from_cvss(0.0), Severity.LOW)


# ---------------------------------------------------------------------------
# _find_requirement_files
# ---------------------------------------------------------------------------


class FindRequirementFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def _make(self, relpath: str) -> Path:
        p = self.workspace / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('')
        return p

    def test_no_files_yields_nothing(self) -> None:
        self.assertEqual(list(_find_requirement_files(self.workspace)), [])

    def test_finds_requirements_txt(self) -> None:
        p = self._make('requirements.txt')
        self.assertIn(p, list(_find_requirement_files(self.workspace)))

    def test_finds_pipfile_lock(self) -> None:
        p = self._make('Pipfile.lock')
        self.assertIn(p, list(_find_requirement_files(self.workspace)))

    def test_finds_poetry_lock(self) -> None:
        p = self._make('poetry.lock')
        self.assertIn(p, list(_find_requirement_files(self.workspace)))

    def test_ignores_non_requirement_files(self) -> None:
        self._make('setup.py')
        self._make('pyproject.toml')
        self._make('package.json')
        self.assertEqual(list(_find_requirement_files(self.workspace)), [])

    def test_skips_excluded_dirs(self) -> None:
        self._make('node_modules/pkg/requirements.txt')
        self._make('.venv/requirements.txt')
        self.assertEqual(list(_find_requirement_files(self.workspace)), [])

    def test_finds_nested_files(self) -> None:
        p = self._make('services/api/requirements.txt')
        results = list(_find_requirement_files(self.workspace))
        self.assertIn(p, results)

    def test_multiple_files_all_returned(self) -> None:
        p1 = self._make('requirements.txt')
        p2 = self._make('services/Pipfile.lock')
        results = list(_find_requirement_files(self.workspace))
        self.assertIn(p1, results)
        self.assertIn(p2, results)
        self.assertEqual(len(results), 2)


# ---------------------------------------------------------------------------
# run() — integration
# ---------------------------------------------------------------------------


def _mock_result(returncode: int, stdout: str, stderr: str = '') -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


class SafetyRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        (self.workspace / 'requirements.txt').write_text('requests==2.0.0\n')

    def test_safety_not_installed_raises_runner_unavailable(self) -> None:
        with patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.shutil.which',
            return_value=None,
        ):
            with self.assertRaises(RunnerUnavailableError):
                run(str(self.workspace))

    def test_binary_disappears_raises_runner_unavailable(self) -> None:
        with patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.shutil.which',
            return_value='/usr/bin/safety',
        ), patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.subprocess.run',
            side_effect=FileNotFoundError('not found'),
        ):
            with self.assertRaises(RunnerUnavailableError):
                run(str(self.workspace))

    def test_non_directory_workspace_returns_empty(self) -> None:
        with patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.shutil.which',
            return_value='/usr/bin/safety',
        ):
            self.assertEqual(run('/nonexistent/path'), [])

    def test_no_requirement_files_returns_empty(self) -> None:
        empty_tmp = tempfile.mkdtemp()
        try:
            with patch(
                'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.shutil.which',
                return_value='/usr/bin/safety',
            ):
                self.assertEqual(run(empty_tmp), [])
        finally:
            import shutil
            shutil.rmtree(empty_tmp)

    def _run_with_payload(self, returncode: int, payload, *, logger=None) -> list:
        stdout = json.dumps(payload)
        with patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.shutil.which',
            return_value='/usr/bin/safety',
        ), patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.subprocess.run',
            return_value=_mock_result(returncode, stdout),
        ):
            return run(str(self.workspace), logger)

    def test_returncode_0_returns_empty(self) -> None:
        result = self._run_with_payload(0, [])
        self.assertEqual(result, [])

    def test_returncode_64_old_format_list(self) -> None:
        payload = [{
            'package_name': 'requests',
            'analyzed_version': '2.0.0',
            'vulnerability_id': 'CVE-2021-1234',
            'CVSS': 7.5,
            'advisory': 'Remote code execution in requests.',
        }]
        result = self._run_with_payload(64, payload)
        self.assertEqual(len(result), 1)
        f = result[0]
        self.assertEqual(f.tool, 'safety')
        self.assertEqual(f.severity, Severity.HIGH)
        self.assertEqual(f.rule_id, 'CVE-2021-1234')
        self.assertIn('requests', f.message)

    def test_returncode_64_new_format_dict(self) -> None:
        payload = {
            'vulnerabilities': [{
                'package_name': 'flask',
                'analyzed_version': '1.0.0',
                'vulnerability_id': 'CVE-2022-5678',
                'CVSS': 9.5,
                'advisory': 'Critical flask vulnerability.',
            }]
        }
        result = self._run_with_payload(64, payload)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].severity, Severity.CRITICAL)

    def test_returncode_other_skips_with_warning(self) -> None:
        logger = MagicMock()
        with patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.shutil.which',
            return_value='/usr/bin/safety',
        ), patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.subprocess.run',
            return_value=_mock_result(1, '', 'network error'),
        ):
            result = run(str(self.workspace), logger)
        self.assertEqual(result, [])
        logger.warning.assert_called()

    def test_empty_stdout_skips_silently(self) -> None:
        with patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.shutil.which',
            return_value='/usr/bin/safety',
        ), patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.subprocess.run',
            return_value=_mock_result(64, ''),
        ):
            result = run(str(self.workspace))
        self.assertEqual(result, [])

    def test_invalid_json_skips_with_warning(self) -> None:
        logger = MagicMock()
        with patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.shutil.which',
            return_value='/usr/bin/safety',
        ), patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.subprocess.run',
            return_value=_mock_result(64, 'NOT JSON'),
        ):
            result = run(str(self.workspace), logger)
        self.assertEqual(result, [])
        logger.warning.assert_called()

    def test_returncode_other_without_logger_silently_skipped(self) -> None:
        # Branch 103->109: ``logger is None`` skips ``logger.warning``
        # on an unexpected returncode and falls through to ``continue``.
        with patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.shutil.which',
            return_value='/usr/bin/safety',
        ), patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.subprocess.run',
            return_value=_mock_result(1, '', 'network error'),
        ):
            result = run(str(self.workspace))
        self.assertEqual(result, [])

    def test_invalid_json_without_logger_silently_skipped(self) -> None:
        # Branch 115->120: ``logger is None`` skips ``logger.warning``
        # on JSONDecodeError and falls through to ``continue``.
        with patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.shutil.which',
            return_value='/usr/bin/safety',
        ), patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.subprocess.run',
            return_value=_mock_result(64, 'NOT JSON'),
        ):
            result = run(str(self.workspace))
        self.assertEqual(result, [])

    def test_critical_cvss_maps_correctly(self) -> None:
        payload = [{'package_name': 'p', 'analyzed_version': '1.0', 'vulnerability_id': 'V1', 'CVSS': 9.0, 'advisory': 'x'}]
        result = self._run_with_payload(64, payload)
        self.assertEqual(result[0].severity, Severity.CRITICAL)

    def test_medium_cvss_maps_correctly(self) -> None:
        payload = [{'package_name': 'p', 'analyzed_version': '1.0', 'vulnerability_id': 'V1', 'CVSS': 5.0, 'advisory': 'x'}]
        result = self._run_with_payload(64, payload)
        self.assertEqual(result[0].severity, Severity.MEDIUM)

    def test_low_cvss_maps_correctly(self) -> None:
        payload = [{'package_name': 'p', 'analyzed_version': '1.0', 'vulnerability_id': 'V1', 'CVSS': 2.5, 'advisory': 'x'}]
        result = self._run_with_payload(64, payload)
        self.assertEqual(result[0].severity, Severity.LOW)

    def test_missing_cvss_defaults_to_medium(self) -> None:
        payload = [{'package_name': 'p', 'analyzed_version': '1.0', 'vulnerability_id': 'V1', 'advisory': 'x'}]
        result = self._run_with_payload(64, payload)
        self.assertEqual(result[0].severity, Severity.MEDIUM)

    def test_non_numeric_cvss_falls_back_to_medium(self) -> None:
        payload = [{'package_name': 'p', 'analyzed_version': '1.0', 'vulnerability_id': 'V1', 'CVSS': 'N/A', 'advisory': 'x'}]
        result = self._run_with_payload(64, payload)
        self.assertEqual(result[0].severity, Severity.MEDIUM)

    def test_alternate_field_names_are_handled(self) -> None:
        payload = [{
            'package': 'boto3',
            'installed_version': '1.0.0',
            'CVE': 'CVE-2020-0001',
            'cvss': 8.0,
            'description': 'Alternate field names advisory.',
        }]
        result = self._run_with_payload(64, payload)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].rule_id, 'CVE-2020-0001')
        self.assertEqual(result[0].severity, Severity.HIGH)

    def test_advisory_truncated_at_280_chars(self) -> None:
        long_advisory = 'A' * 300
        payload = [{'package_name': 'p', 'analyzed_version': '1.0', 'vulnerability_id': 'V1', 'advisory': long_advisory}]
        result = self._run_with_payload(64, payload)
        self.assertIn('…', result[0].message)
        self.assertIn('A' * 10, result[0].message)

    def test_short_advisory_not_truncated(self) -> None:
        payload = [{'package_name': 'p', 'analyzed_version': '1.0', 'vulnerability_id': 'V1', 'advisory': 'Short advisory.'}]
        result = self._run_with_payload(64, payload)
        self.assertNotIn('…', result[0].message)

    def test_metadata_contains_package_version_cvss(self) -> None:
        payload = [{'package_name': 'requests', 'analyzed_version': '2.0.0', 'vulnerability_id': 'V1', 'CVSS': 7.5, 'advisory': 'x'}]
        result = self._run_with_payload(64, payload)
        meta = dict(result[0].metadata)
        self.assertEqual(meta['package'], 'requests')
        self.assertEqual(meta['installed_version'], '2.0.0')
        self.assertEqual(meta['cvss'], '7.5')

    def test_metadata_cvss_empty_when_missing(self) -> None:
        payload = [{'package_name': 'p', 'analyzed_version': '1.0', 'vulnerability_id': 'V1', 'advisory': 'x'}]
        result = self._run_with_payload(64, payload)
        meta = dict(result[0].metadata)
        self.assertEqual(meta['cvss'], '')

    def test_timeout_seconds_passed_to_subprocess(self) -> None:
        captured = {}

        def fake_run(cmd, **kwargs):
            captured['timeout'] = kwargs.get('timeout')
            return _mock_result(0, json.dumps([]))

        with patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.shutil.which',
            return_value='/usr/bin/safety',
        ), patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.subprocess.run',
            side_effect=fake_run,
        ):
            run(str(self.workspace), timeout_seconds=60)
        self.assertEqual(captured['timeout'], 60)

    def test_multiple_requirement_files_all_scanned(self) -> None:
        (self.workspace / 'Pipfile.lock').write_text('')
        call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            return _mock_result(0, json.dumps([]))

        with patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.shutil.which',
            return_value='/usr/bin/safety',
        ), patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.safety_runner.subprocess.run',
            side_effect=fake_run,
        ):
            run(str(self.workspace))
        self.assertEqual(call_count, 2)

    def test_unknown_vulnerability_id_falls_back_to_unknown(self) -> None:
        payload = [{'package_name': 'p', 'analyzed_version': '1.0', 'advisory': 'x'}]
        result = self._run_with_payload(64, payload)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].rule_id, 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
