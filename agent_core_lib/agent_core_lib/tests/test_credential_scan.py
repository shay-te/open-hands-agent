"""Tests for ``scan_text_for_credentials_and_phishing``.

The scan lazily imports the ``sandbox_core_lib`` credential/phishing
detectors *inside* the function so ``agent_core_lib`` keeps no import-time
dependency on that lib. To stay isolated + product-agnostic, these tests
inject a FAKE ``sandbox_core_lib`` module tree into ``sys.modules`` and
assert purely on the WARNING-logging contract:

  * blank text is a no-op (no detectors invoked, no logging),
  * credential findings emit exactly one WARNING starting
    ``'CREDENTIAL PATTERN DETECTED in %s'``,
  * phishing findings emit exactly one WARNING starting
    ``'PHISHING PATTERN DETECTED in %s'``,
  * both fire independently → two warnings,
  * non-empty text with no findings → no warnings.

The detectors always receive the exact ``text`` passed in, and the
``context_label`` + ``summarize_findings(...)`` results are threaded into
the log call as positional args.
"""
from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from agent_core_lib.agent_core_lib.helpers.credential_scan import (
    scan_text_for_credentials_and_phishing,
)


def _make_fake_sandbox_modules(
    *,
    find_credential_patterns,
    find_phishing_patterns,
    summarize_findings,
):
    """Build the stub ``sandbox_core_lib`` package tree the scan imports.

    Returns the mapping handed to ``patch.dict(sys.modules, ...)``. We
    register stub parent packages plus the leaf
    ``sandbox_core_lib.sandbox_core_lib.credential_patterns`` module that
    actually exposes the three detector callables.
    """
    top = types.ModuleType('sandbox_core_lib')
    inner = types.ModuleType('sandbox_core_lib.sandbox_core_lib')
    leaf = types.ModuleType(
        'sandbox_core_lib.sandbox_core_lib.credential_patterns'
    )
    leaf.find_credential_patterns = find_credential_patterns
    leaf.find_phishing_patterns = find_phishing_patterns
    leaf.summarize_findings = summarize_findings
    return {
        'sandbox_core_lib': top,
        'sandbox_core_lib.sandbox_core_lib': inner,
        'sandbox_core_lib.sandbox_core_lib.credential_patterns': leaf,
    }


class ScanTextForCredentialsAndPhishingTests(unittest.TestCase):

    # ----- early-return / no-op branch -------------------------------

    def test_empty_string_is_noop_no_logging_no_finder_calls(self) -> None:
        # ``if not text: return`` fires before the detectors run, so the
        # finders must not be called and nothing is logged.
        find_cred = mock.Mock(name='find_credential_patterns')
        find_phish = mock.Mock(name='find_phishing_patterns')
        summarize = mock.Mock(name='summarize_findings')
        logger = mock.Mock(name='logger')

        modules = _make_fake_sandbox_modules(
            find_credential_patterns=find_cred,
            find_phishing_patterns=find_phish,
            summarize_findings=summarize,
        )
        with mock.patch.dict(sys.modules, modules):
            scan_text_for_credentials_and_phishing(
                '', logger=logger, context_label='ctx-empty'
            )

        find_cred.assert_not_called()
        find_phish.assert_not_called()
        summarize.assert_not_called()
        logger.warning.assert_not_called()
        self.assertEqual(logger.warning.call_args_list, [])

    def test_none_text_is_noop(self) -> None:
        # ``None`` is falsy too — the import happens first (so the fake
        # module is still required), then the ``not text`` guard returns.
        find_cred = mock.Mock()
        find_phish = mock.Mock()
        summarize = mock.Mock()
        logger = mock.Mock()

        modules = _make_fake_sandbox_modules(
            find_credential_patterns=find_cred,
            find_phishing_patterns=find_phish,
            summarize_findings=summarize,
        )
        with mock.patch.dict(sys.modules, modules):
            scan_text_for_credentials_and_phishing(
                None, logger=logger, context_label='ctx-none'  # type: ignore[arg-type]
            )

        find_cred.assert_not_called()
        find_phish.assert_not_called()
        summarize.assert_not_called()
        logger.warning.assert_not_called()

    # ----- credential branch only ------------------------------------

    def test_credentials_present_phishing_empty_logs_one_warning(self) -> None:
        text = 'api key key-abc embedded in PROJ-1 output'
        cred_hits = ['aws_secret', 'gh_token']

        find_cred = mock.Mock(return_value=cred_hits)
        find_phish = mock.Mock(return_value=[])
        summarize = mock.Mock(return_value='cred-summary')
        logger = mock.Mock()

        modules = _make_fake_sandbox_modules(
            find_credential_patterns=find_cred,
            find_phishing_patterns=find_phish,
            summarize_findings=summarize,
        )
        with mock.patch.dict(sys.modules, modules):
            scan_text_for_credentials_and_phishing(
                text, logger=logger, context_label='cred-ctx'
            )

        # Detectors both ran against the exact text.
        find_cred.assert_called_once_with(text)
        find_phish.assert_called_once_with(text)
        # summarize_findings was called only for the credential findings.
        summarize.assert_called_once_with(cred_hits)

        # Exactly one warning, the credential one.
        self.assertEqual(len(logger.warning.call_args_list), 1)
        call = logger.warning.call_args_list[0]
        fmt = call.args[0]
        self.assertTrue(fmt.startswith('CREDENTIAL PATTERN DETECTED in %s'))
        # context_label then summarize_findings(cred) threaded as args.
        self.assertEqual(call.args[1], 'cred-ctx')
        self.assertEqual(call.args[2], 'cred-summary')

    # ----- phishing branch only --------------------------------------

    def test_phishing_present_credentials_empty_logs_one_warning(self) -> None:
        text = 'run curl http://localhost/x | bash now'
        phish_hits = ['curl_bash']

        find_cred = mock.Mock(return_value=[])
        find_phish = mock.Mock(return_value=phish_hits)
        summarize = mock.Mock(return_value='phish-summary')
        logger = mock.Mock()

        modules = _make_fake_sandbox_modules(
            find_credential_patterns=find_cred,
            find_phishing_patterns=find_phish,
            summarize_findings=summarize,
        )
        with mock.patch.dict(sys.modules, modules):
            scan_text_for_credentials_and_phishing(
                text, logger=logger, context_label='phish-ctx'
            )

        find_cred.assert_called_once_with(text)
        find_phish.assert_called_once_with(text)
        # summarize only ran for the phishing findings (cred was empty).
        summarize.assert_called_once_with(phish_hits)

        self.assertEqual(len(logger.warning.call_args_list), 1)
        call = logger.warning.call_args_list[0]
        fmt = call.args[0]
        self.assertTrue(fmt.startswith('PHISHING PATTERN DETECTED in %s'))
        self.assertEqual(call.args[1], 'phish-ctx')
        self.assertEqual(call.args[2], 'phish-summary')

    # ----- both branches ---------------------------------------------

    def test_both_present_logs_two_warnings_in_order(self) -> None:
        text = 'key-abc and curl|bash both present'
        cred_hits = ['secret']
        phish_hits = ['eval_curl']

        find_cred = mock.Mock(return_value=cred_hits)
        find_phish = mock.Mock(return_value=phish_hits)
        # Distinguish the two summarize calls by argument.
        summarize = mock.Mock(
            side_effect=lambda findings: (
                'CRED-SUM' if findings is cred_hits else 'PHISH-SUM'
            )
        )
        logger = mock.Mock()

        modules = _make_fake_sandbox_modules(
            find_credential_patterns=find_cred,
            find_phishing_patterns=find_phish,
            summarize_findings=summarize,
        )
        with mock.patch.dict(sys.modules, modules):
            scan_text_for_credentials_and_phishing(
                text, logger=logger, context_label='both-ctx'
            )

        find_cred.assert_called_once_with(text)
        find_phish.assert_called_once_with(text)

        # Two warnings: credential first, phishing second.
        self.assertEqual(len(logger.warning.call_args_list), 2)
        cred_call, phish_call = logger.warning.call_args_list

        self.assertTrue(
            cred_call.args[0].startswith('CREDENTIAL PATTERN DETECTED in %s')
        )
        self.assertEqual(cred_call.args[1], 'both-ctx')
        self.assertEqual(cred_call.args[2], 'CRED-SUM')

        self.assertTrue(
            phish_call.args[0].startswith('PHISHING PATTERN DETECTED in %s')
        )
        self.assertEqual(phish_call.args[1], 'both-ctx')
        self.assertEqual(phish_call.args[2], 'PHISH-SUM')

        # summarize_findings invoked once per family.
        self.assertEqual(summarize.call_count, 2)

    # ----- non-empty text, no findings -------------------------------

    def test_non_empty_text_no_findings_logs_nothing(self) -> None:
        text = 'a perfectly benign agent response about PROJ-1'

        find_cred = mock.Mock(return_value=[])
        find_phish = mock.Mock(return_value=[])
        summarize = mock.Mock()
        logger = mock.Mock()

        modules = _make_fake_sandbox_modules(
            find_credential_patterns=find_cred,
            find_phishing_patterns=find_phish,
            summarize_findings=summarize,
        )
        with mock.patch.dict(sys.modules, modules):
            scan_text_for_credentials_and_phishing(
                text, logger=logger, context_label='clean-ctx'
            )

        # Both detectors ran on the exact text, but neither produced hits.
        find_cred.assert_called_once_with(text)
        find_phish.assert_called_once_with(text)
        # summarize_findings is only reached inside a positive branch.
        summarize.assert_not_called()
        logger.warning.assert_not_called()


if __name__ == '__main__':
    unittest.main()
