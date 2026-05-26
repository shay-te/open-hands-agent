"""Full coverage for runners/bandit_runner.py.

All subprocess calls are mocked — no bandit binary required.
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
from security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner import (
    _kato_severity,
    run,
)
from security_scanner_core_lib.security_scanner_core_lib.security_finding import Severity


# ---------------------------------------------------------------------------
# _kato_severity mapping
# ---------------------------------------------------------------------------


class KatoSeverityTests(unittest.TestCase):
    def test_high_confidence_maps_directly(self) -> None:
        self.assertEqual(_kato_severity('HIGH', 'HIGH'), Severity.HIGH)
        self.assertEqual(_kato_severity('HIGH', 'MEDIUM'), Severity.HIGH)
        self.assertEqual(_kato_severity('MEDIUM', 'HIGH'), Severity.MEDIUM)
        self.assertEqual(_kato_severity('LOW', 'HIGH'), Severity.LOW)

    def test_low_confidence_demotes_high_to_medium(self) -> None:
        self.assertEqual(_kato_severity('HIGH', 'LOW'), Severity.MEDIUM)

    def test_low_confidence_demotes_medium_to_low(self) -> None:
        self.assertEqual(_kato_severity('MEDIUM', 'LOW'), Severity.LOW)

    def test_low_confidence_keeps_low_as_low(self) -> None:
        self.assertEqual(_kato_severity('LOW', 'LOW'), Severity.LOW)

    def test_unknown_bandit_severity_falls_back_to_low(self) -> None:
        self.assertEqual(_kato_severity('UNKNOWN', 'HIGH'), Severity.LOW)

    def test_case_insensitive_severity(self) -> None:
        self.assertEqual(_kato_severity('high', 'high'), Severity.HIGH)

    def test_case_insensitive_confidence(self) -> None:
        self.assertEqual(_kato_severity('HIGH', 'low'), Severity.MEDIUM)


# ---------------------------------------------------------------------------
# run() — bandit not installed
# ---------------------------------------------------------------------------


class BanditNotInstalledTests(unittest.TestCase):
    def test_raises_runner_unavailable_when_bandit_missing(self) -> None:
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.shutil.which',
                   return_value=None):
            with self.assertRaises(RunnerUnavailableError) as ctx:
                run('/some/workspace')
            self.assertIn('bandit', str(ctx.exception).lower())

    def test_raises_runner_unavailable_when_binary_disappears(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            with patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.shutil.which',
                       return_value='/usr/bin/bandit'), \
                 patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.subprocess.run',
                       side_effect=FileNotFoundError('not found')):
                with self.assertRaises(RunnerUnavailableError):
                    run(ws)


# ---------------------------------------------------------------------------
# run() — workspace edge cases
# ---------------------------------------------------------------------------


class BanditWorkspaceEdgeCaseTests(unittest.TestCase):
    def test_non_directory_workspace_returns_empty(self) -> None:
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.shutil.which',
                   return_value='/usr/bin/bandit'):
            result = run('/nonexistent/path/that/does/not/exist')
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# run() — output parsing
# ---------------------------------------------------------------------------


def _bandit_result(returncode: int, stdout: str, stderr: str = '') -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


class BanditOutputParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = self._tmp.name

    def _run_with_output(self, returncode: int, payload: dict) -> list:
        stdout = json.dumps(payload)
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.shutil.which',
                   return_value='/usr/bin/bandit'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.subprocess.run',
                   return_value=_bandit_result(returncode, stdout)):
            return run(self.workspace)

    def test_returncode_0_no_results_returns_empty(self) -> None:
        result = self._run_with_output(0, {'results': [], 'metrics': {}})
        self.assertEqual(result, [])

    def test_returncode_1_with_findings_returns_findings(self) -> None:
        payload = {
            'results': [{
                'test_id': 'B601',
                'test_name': 'paramiko_calls',
                'issue_severity': 'HIGH',
                'issue_confidence': 'MEDIUM',
                'issue_text': 'Paramiko call with host key checking disabled.',
                'filename': f'{self.workspace}/app.py',
                'line_number': 42,
            }],
        }
        results = self._run_with_output(1, payload)
        self.assertEqual(len(results), 1)
        f = results[0]
        self.assertEqual(f.tool, 'bandit')
        self.assertEqual(f.severity, Severity.HIGH)
        self.assertEqual(f.rule_id, 'B601')
        self.assertEqual(f.line, 42)

    def test_high_severity_low_confidence_is_demoted(self) -> None:
        payload = {
            'results': [{
                'test_id': 'B101',
                'test_name': 'assert_used',
                'issue_severity': 'HIGH',
                'issue_confidence': 'LOW',
                'issue_text': 'Use of assert detected.',
                'filename': f'{self.workspace}/x.py',
                'line_number': 5,
            }],
        }
        results = self._run_with_output(1, payload)
        self.assertEqual(results[0].severity, Severity.MEDIUM)

    def test_multiple_findings_parsed(self) -> None:
        payload = {
            'results': [
                {'test_id': 'B101', 'test_name': 't1', 'issue_severity': 'HIGH',
                 'issue_confidence': 'HIGH', 'issue_text': 'msg1',
                 'filename': f'{self.workspace}/a.py', 'line_number': 1},
                {'test_id': 'B102', 'test_name': 't2', 'issue_severity': 'MEDIUM',
                 'issue_confidence': 'MEDIUM', 'issue_text': 'msg2',
                 'filename': f'{self.workspace}/b.py', 'line_number': 2},
            ],
        }
        results = self._run_with_output(1, payload)
        self.assertEqual(len(results), 2)

    def test_metadata_contains_bandit_severity_and_confidence(self) -> None:
        payload = {
            'results': [{
                'test_id': 'B201', 'test_name': 'flask_debug_true',
                'issue_severity': 'HIGH', 'issue_confidence': 'HIGH',
                'issue_text': 'Flask debug mode enabled.',
                'filename': f'{self.workspace}/app.py', 'line_number': 10,
            }],
        }
        results = self._run_with_output(1, payload)
        meta = dict(results[0].metadata)
        self.assertIn('bandit_severity', meta)
        self.assertIn('confidence', meta)

    def test_empty_stdout_returns_empty(self) -> None:
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.shutil.which',
                   return_value='/usr/bin/bandit'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.subprocess.run',
                   return_value=_bandit_result(0, '')):
            result = run(self.workspace)
        self.assertEqual(result, [])

    def test_invalid_json_returns_empty(self) -> None:
        logger = MagicMock()
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.shutil.which',
                   return_value='/usr/bin/bandit'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.subprocess.run',
                   return_value=_bandit_result(1, 'NOT JSON AT ALL')):
            result = run(self.workspace, logger=logger)
        self.assertEqual(result, [])
        logger.warning.assert_called()

    def test_returncode_greater_than_1_returns_empty_and_warns(self) -> None:
        logger = MagicMock()
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.shutil.which',
                   return_value='/usr/bin/bandit'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.subprocess.run',
                   return_value=_bandit_result(2, '', 'config error')):
            result = run(self.workspace, logger=logger)
        self.assertEqual(result, [])
        logger.warning.assert_called()

    def test_returncode_greater_than_1_without_logger_returns_empty(self) -> None:
        # Branch 114->120: ``logger is None`` skips ``logger.warning``
        # and falls through to the empty return. Locks the no-logger
        # path so a missing logger doesn't crash on a config-error
        # returncode.
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.shutil.which',
                   return_value='/usr/bin/bandit'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.subprocess.run',
                   return_value=_bandit_result(2, '', 'config error')):
            result = run(self.workspace)
        self.assertEqual(result, [])

    def test_invalid_json_without_logger_returns_empty(self) -> None:
        # Branch 126->128: ``logger is None`` skips ``logger.warning``
        # on JSONDecodeError and falls through to ``return []``. Locks
        # the no-logger path on malformed stdout.
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.shutil.which',
                   return_value='/usr/bin/bandit'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.bandit_runner.subprocess.run',
                   return_value=_bandit_result(1, 'NOT JSON AT ALL')):
            result = run(self.workspace)
        self.assertEqual(result, [])

    def test_finding_without_filename_gets_empty_path(self) -> None:
        payload = {
            'results': [{
                'test_id': 'B001', 'test_name': 'test', 'issue_severity': 'LOW',
                'issue_confidence': 'HIGH', 'issue_text': 'msg',
                'filename': '', 'line_number': 0,
            }],
        }
        results = self._run_with_output(1, payload)
        self.assertEqual(results[0].path, '')


if __name__ == '__main__':
    unittest.main()
