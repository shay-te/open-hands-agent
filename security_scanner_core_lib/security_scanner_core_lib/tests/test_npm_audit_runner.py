"""Full coverage for runners/npm_audit_runner.py.

All subprocess calls are mocked — no npm binary required.
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
from security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner import (
    _find_npm_projects,
    _findings_from_legacy,
    _findings_from_v7,
    run,
)
from security_scanner_core_lib.security_scanner_core_lib.security_finding import Severity


# ---------------------------------------------------------------------------
# _find_npm_projects
# ---------------------------------------------------------------------------


class FindNpmProjectsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def _make(self, relpath: str) -> None:
        p = self.workspace / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{}')

    def test_no_package_json_yields_nothing(self) -> None:
        self.assertEqual(list(_find_npm_projects(self.workspace)), [])

    def test_package_json_without_lockfile_yields_nothing(self) -> None:
        self._make('package.json')
        self.assertEqual(list(_find_npm_projects(self.workspace)), [])

    def test_package_json_with_package_lock_yields_dir(self) -> None:
        self._make('package.json')
        self._make('package-lock.json')
        results = list(_find_npm_projects(self.workspace))
        self.assertEqual(results, [self.workspace])

    def test_package_json_with_yarn_lock_yields_dir(self) -> None:
        self._make('package.json')
        self._make('yarn.lock')
        results = list(_find_npm_projects(self.workspace))
        self.assertEqual(results, [self.workspace])

    def test_skips_node_modules(self) -> None:
        self._make('node_modules/sub/package.json')
        self._make('node_modules/sub/package-lock.json')
        results = list(_find_npm_projects(self.workspace))
        self.assertEqual(results, [])

    def test_nested_project_is_found(self) -> None:
        self._make('packages/api/package.json')
        self._make('packages/api/package-lock.json')
        results = list(_find_npm_projects(self.workspace))
        self.assertEqual(results, [self.workspace / 'packages' / 'api'])


# ---------------------------------------------------------------------------
# _findings_from_v7
# ---------------------------------------------------------------------------


class FindingsFromV7Tests(unittest.TestCase):
    def test_empty_vulns_returns_empty(self) -> None:
        self.assertEqual(_findings_from_v7({}, 'package.json'), [])

    def test_basic_vulnerability(self) -> None:
        vulns = {
            'lodash': {
                'severity': 'high',
                'via': [{'source': 'https://npmjs.com/advisories/1234',
                          'title': 'Prototype Pollution',
                          'range': '<4.17.21'}],
            }
        }
        results = _findings_from_v7(vulns, 'package.json')
        self.assertEqual(len(results), 1)
        f = results[0]
        self.assertEqual(f.tool, 'npm-audit')
        self.assertEqual(f.severity, Severity.HIGH)
        self.assertIn('lodash', f.message)
        self.assertIn('Prototype Pollution', f.message)

    def test_severity_mapping(self) -> None:
        cases = [
            ('critical', Severity.CRITICAL),
            ('high', Severity.HIGH),
            ('moderate', Severity.MEDIUM),
            ('low', Severity.LOW),
            ('info', Severity.LOW),
        ]
        for npm_sev, expected in cases:
            vulns = {
                'pkg': {
                    'severity': npm_sev,
                    'via': [{'source': '123', 'title': 'x'}],
                }
            }
            results = _findings_from_v7(vulns, 'pkg.json')
            self.assertEqual(results[0].severity, expected,
                             f'{npm_sev!r} should map to {expected}')

    def test_via_without_dict_is_skipped(self) -> None:
        vulns = {
            'pkg': {
                'severity': 'high',
                'via': ['lodash'],  # strings, not dicts
            }
        }
        results = _findings_from_v7(vulns, 'package.json')
        self.assertEqual(results, [])

    def test_dedups_same_package_same_advisory(self) -> None:
        advisory = {'source': '999', 'title': 'Bug'}
        vulns = {
            'pkg': {
                'severity': 'high',
                'via': [advisory, advisory],  # duplicate
            }
        }
        results = _findings_from_v7(vulns, 'package.json')
        self.assertEqual(len(results), 1)

    def test_metadata_contains_package_and_severity(self) -> None:
        vulns = {
            'axios': {
                'severity': 'critical',
                'via': [{'source': '456', 'title': 'SSRF'}],
            }
        }
        results = _findings_from_v7(vulns, 'package.json')
        meta = dict(results[0].metadata)
        self.assertEqual(meta['package'], 'axios')
        self.assertEqual(meta['npm_severity'], 'critical')


# ---------------------------------------------------------------------------
# _findings_from_legacy
# ---------------------------------------------------------------------------


class FindingsFromLegacyTests(unittest.TestCase):
    def test_empty_advisories_returns_empty(self) -> None:
        self.assertEqual(_findings_from_legacy({}, 'package.json'), [])

    def test_basic_advisory(self) -> None:
        advisories = {
            '1234': {
                'severity': 'high',
                'module_name': 'express',
                'title': 'Open Redirect',
            }
        }
        results = _findings_from_legacy(advisories, 'package.json')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].severity, Severity.HIGH)
        self.assertIn('express', results[0].message)

    def test_severity_mapping(self) -> None:
        for npm_sev, expected in [('critical', Severity.CRITICAL),
                                   ('moderate', Severity.MEDIUM),
                                   ('low', Severity.LOW)]:
            advisories = {
                '1': {'severity': npm_sev, 'module_name': 'p', 'title': 't'}
            }
            results = _findings_from_legacy(advisories, 'package.json')
            self.assertEqual(results[0].severity, expected)

    def test_metadata_contains_package(self) -> None:
        advisories = {
            '99': {'severity': 'high', 'module_name': 'lodash', 'title': 'PP'}
        }
        results = _findings_from_legacy(advisories, 'package.json')
        meta = dict(results[0].metadata)
        self.assertEqual(meta['package'], 'lodash')


# ---------------------------------------------------------------------------
# run() — integration
# ---------------------------------------------------------------------------


def _mock_subprocess_result(returncode: int, stdout: str, stderr: str = '') -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


class NpmAuditRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        (self.workspace / 'package.json').write_text('{}')
        (self.workspace / 'package-lock.json').write_text('{}')

    def test_npm_not_installed_raises_runner_unavailable(self) -> None:
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.shutil.which',
                   return_value=None):
            with self.assertRaises(RunnerUnavailableError):
                run(str(self.workspace))

    def test_npm_binary_disappears_raises_runner_unavailable(self) -> None:
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.shutil.which',
                   return_value='/usr/bin/npm'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.subprocess.run',
                   side_effect=FileNotFoundError('not found')):
            with self.assertRaises(RunnerUnavailableError):
                run(str(self.workspace))

    def test_non_directory_workspace_returns_empty(self) -> None:
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.shutil.which',
                   return_value='/usr/bin/npm'):
            result = run('/nonexistent/path')
        self.assertEqual(result, [])

    def test_clean_audit_returns_empty(self) -> None:
        payload = json.dumps({'vulnerabilities': {}, 'metadata': {}})
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.shutil.which',
                   return_value='/usr/bin/npm'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.subprocess.run',
                   return_value=_mock_subprocess_result(0, payload)):
            result = run(str(self.workspace))
        self.assertEqual(result, [])

    def test_v7_vulnerabilities_parsed(self) -> None:
        payload = json.dumps({
            'vulnerabilities': {
                'lodash': {
                    'severity': 'critical',
                    'via': [{'source': '777', 'title': 'PP', 'range': '<4.17'}],
                }
            }
        })
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.shutil.which',
                   return_value='/usr/bin/npm'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.subprocess.run',
                   return_value=_mock_subprocess_result(1, payload)):
            result = run(str(self.workspace))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].severity, Severity.CRITICAL)

    def test_legacy_advisories_parsed(self) -> None:
        payload = json.dumps({
            'advisories': {
                '1111': {
                    'severity': 'moderate',
                    'module_name': 'express',
                    'title': 'Open Redirect',
                }
            }
        })
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.shutil.which',
                   return_value='/usr/bin/npm'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.subprocess.run',
                   return_value=_mock_subprocess_result(1, payload)):
            result = run(str(self.workspace))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].severity, Severity.MEDIUM)

    def test_empty_stdout_skips_with_warning(self) -> None:
        logger = MagicMock()
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.shutil.which',
                   return_value='/usr/bin/npm'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.subprocess.run',
                   return_value=_mock_subprocess_result(1, '')):
            result = run(str(self.workspace), logger=logger)
        self.assertEqual(result, [])
        logger.warning.assert_called()

    def test_invalid_json_skips_with_warning(self) -> None:
        logger = MagicMock()
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.shutil.which',
                   return_value='/usr/bin/npm'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.subprocess.run',
                   return_value=_mock_subprocess_result(1, 'NOT JSON')):
            result = run(str(self.workspace), logger=logger)
        self.assertEqual(result, [])
        logger.warning.assert_called()

    def test_empty_stdout_without_logger_silently_skipped(self) -> None:
        # Branch 101->107: ``logger is None`` skips ``logger.warning``
        # on empty stdout and falls through to ``continue``.
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.shutil.which',
                   return_value='/usr/bin/npm'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.subprocess.run',
                   return_value=_mock_subprocess_result(1, '')):
            result = run(str(self.workspace))
        self.assertEqual(result, [])

    def test_invalid_json_without_logger_silently_skipped(self) -> None:
        # Branch 111->116: ``logger is None`` skips ``logger.warning``
        # on JSONDecodeError and falls through to ``continue``.
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.shutil.which',
                   return_value='/usr/bin/npm'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.subprocess.run',
                   return_value=_mock_subprocess_result(1, 'NOT JSON')):
            result = run(str(self.workspace))
        self.assertEqual(result, [])

    def test_payload_without_vulnerabilities_or_advisories_skipped(self) -> None:
        # Branch 127->85: payload has neither a ``vulnerabilities`` dict
        # nor an ``advisories`` dict — neither extend branch fires and
        # we loop back to the next project_dir. Locks tolerance for an
        # unknown npm-audit JSON shape (no findings, no crash).
        payload = json.dumps({'metadata': {'foo': 'bar'}})
        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.shutil.which',
                   return_value='/usr/bin/npm'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.subprocess.run',
                   return_value=_mock_subprocess_result(1, payload)):
            result = run(str(self.workspace))
        self.assertEqual(result, [])

    def test_timeout_seconds_passed_to_subprocess(self) -> None:
        captured = {}

        def fake_run(cmd, **kwargs):
            captured['timeout'] = kwargs.get('timeout')
            return _mock_subprocess_result(0, json.dumps({'vulnerabilities': {}}))

        with patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.shutil.which',
                   return_value='/usr/bin/npm'), \
             patch('security_scanner_core_lib.security_scanner_core_lib.runners.npm_audit_runner.subprocess.run',
                   side_effect=fake_run):
            run(str(self.workspace), timeout_seconds=45)
        self.assertEqual(captured['timeout'], 45)

    def test_findings_from_v7_skips_via_without_advisory_id(self) -> None:
        # Line 144: ``via`` entry has neither ``source`` nor ``url`` → skip.
        findings = _findings_from_v7({
            'lodash': {
                'severity': 'high',
                'via': [
                    {'title': 'no ids here'},   # missing source/url → skip
                    {'source': 'GHSA-1', 'title': 'real one'},
                ],
            },
        }, manifest_path='/repo/package.json')
        self.assertEqual(len(findings), 1)


if __name__ == '__main__':
    unittest.main()
