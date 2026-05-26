"""Defensive-branch coverage for ``TaskPreflightService``.

Pins the rare-fail paths: REP refusal without handler, security scanner
crashes, workspace provisioner partial returns, branch validators on
the no-handler branch, etc. Each test names the lines it covers.
"""

from __future__ import annotations

import types
import unittest
from unittest.mock import MagicMock, Mock, patch

from kato_core_lib.data_layers.data.fields import TaskCommentFields
from kato_core_lib.data_layers.service.task_preflight_service import (
    TaskPreflightService,
)
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
from tests.utils import build_task


def _make_service(**overrides):
    """Build a TaskPreflightService with sane mocked defaults."""
    repo = types.SimpleNamespace(
        id='client', local_path='/workspace/client', destination_branch='main',
    )
    repository_service = Mock()
    repository_service.resolve_task_repositories.return_value = [repo]
    repository_service.prepare_task_repositories.side_effect = lambda rs: rs
    repository_service.prepare_task_branches.side_effect = lambda rs, _: rs
    repository_service.build_branch_name.return_value = 'feat/client'

    push_validator = Mock()
    push_validator.validate.return_value = None
    publish_validator = Mock()
    publish_validator.validate.return_value = None

    defaults = dict(
        task_model_access_validator=Mock(),
        task_service=Mock(),
        repository_service=repository_service,
        task_branch_push_validator=push_validator,
        task_branch_publishability_validator=publish_validator,
    )
    defaults.update(overrides)
    return TaskPreflightService(**defaults), repo


class TaskPreflightDefensiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = build_task(summary='do work', description='detail')

    def test_validate_branch_publishability_returns_false_without_handler(
        self,
    ) -> None:
        # Lines 618-624: ``failure_handler is None`` → log + False.
        service, _ = _make_service()
        publish_validator = service._task_branch_publishability_validator
        publish_validator.validate.side_effect = RuntimeError('publish blocked')
        prepared = PreparedTaskContext(
            branch_name='feat/x',
            repositories=[],
            repository_branches={},
        )
        self.assertFalse(
            service.validate_task_branch_publishability(self.task, prepared),
        )

    def test_validate_branch_push_logs_when_no_handler(self) -> None:
        # Lines 589-595 (line 591-595 region — already tested 206 case).
        service, _ = _make_service()
        push_validator = service._task_branch_push_validator
        push_validator.validate.side_effect = RuntimeError('push blocked')
        prepared = PreparedTaskContext(
            branch_name='feat/x', repositories=[], repository_branches={},
        )
        self.assertFalse(
            service.validate_task_branch_push_access(self.task, prepared),
        )

    def test_handle_pre_start_task_definition_failure_logs_without_handler(
        self,
    ) -> None:
        # Lines 719-723: no handler → log_mission_step.
        service, _ = _make_service()
        service.logger = MagicMock()
        # Drive the no-handler branch.
        service._handle_pre_start_task_definition_failure(
            self.task, failure_handler=None,
        )
        service.logger.info.assert_called()

    def test_handle_repository_detection_failure_adds_comment(self) -> None:
        # Lines 726-727: log + add_task_comment.
        service, _ = _make_service()
        service.logger = MagicMock()
        service._handle_repository_detection_failure(
            self.task, RuntimeError('not found'),
        )
        service._task_service.add_comment.assert_called_once()

    def test_handle_task_definition_failure_adds_comment(self) -> None:
        # Lines 735-736: log + add_task_comment with TOO_THIN comment.
        service, _ = _make_service()
        service.logger = MagicMock()
        service._handle_task_definition_failure(self.task)
        service._task_service.add_comment.assert_called_once()

    def test_add_task_comment_swallows_exception_and_returns_false(self) -> None:
        # Lines 767-774: ``except Exception: log + return False``.
        service, _ = _make_service()
        service.logger = MagicMock()
        service._task_service.add_comment.side_effect = RuntimeError('fail')
        result = service._add_task_comment(
            'PROJ-1', 'msg',
            failure_log_message='comment failed for %s',
        )
        self.assertFalse(result)
        service.logger.exception.assert_called_once()

    def test_provision_workspace_clones_raises_when_empty_provisioned(
        self,
    ) -> None:
        # Lines 290-296: provisioner returns nothing → RuntimeError.
        empty_provisioner = MagicMock(return_value=[])
        service, repo = _make_service(workspace_provisioner=empty_provisioner)
        with self.assertRaisesRegex(RuntimeError, 'workspace provisioner returned no clones'):
            service._provision_workspace_clones(self.task, [repo])

    def test_provision_workspace_clones_raises_on_partial(self) -> None:
        # Lines 298-303: provisioner returns FEWER clones than expected.
        partial = MagicMock(return_value=[
            types.SimpleNamespace(id='client', local_path='/x'),
        ])  # only 1 of 2 expected
        service, _ = _make_service(workspace_provisioner=partial)
        repo_a = types.SimpleNamespace(id='client', local_path='/a')
        repo_b = types.SimpleNamespace(id='backend', local_path='/b')
        with self.assertRaisesRegex(RuntimeError, 'partial workspace'):
            service._provision_workspace_clones(self.task, [repo_a, repo_b])

    def test_provision_workspace_noop_when_no_provisioner(self) -> None:
        # Line 287: no provisioner OR empty repos → return unchanged.
        service, repo = _make_service()
        self.assertEqual(
            service._provision_workspace_clones(self.task, [repo]),
            [repo],
        )

    def test_enforce_rep_refuses_unapproved_without_handler(self) -> None:
        # Lines 348-353: no failure_handler → ``logger.error`` + return False.
        approval = MagicMock()
        approval.unapproved_repository_ids.return_value = ['client']
        service, repo = _make_service(repository_approval_service=approval)
        service.logger = MagicMock()
        ok = service._enforce_restricted_execution_protocol(self.task, [repo])
        self.assertFalse(ok)
        service.logger.error.assert_called_once()

    def test_enforce_rep_posture_violation_without_handler(self) -> None:
        # Lines 388-396: posture violation without handler → log + False.
        approval = MagicMock()
        approval.unapproved_repository_ids.return_value = []
        approval.restricted_mode_repository_ids.return_value = ['client']
        posture_supplier = MagicMock(return_value=MagicMock())
        with patch(
            'kato_core_lib.data_layers.service.repository_approval_service.'
            'restricted_mode_posture_violations',
            return_value=['docker-off'],
        ):
            service, repo = _make_service(
                repository_approval_service=approval,
                runtime_posture_supplier=posture_supplier,
            )
            service.logger = MagicMock()
            ok = service._enforce_restricted_execution_protocol(
                self.task, [repo],
            )
        self.assertFalse(ok)
        service.logger.error.assert_called_once()

    def test_enforce_rep_returns_true_when_no_approval_service(self) -> None:
        # Line 334: approval service is None → True.
        service, repo = _make_service(repository_approval_service=None)
        self.assertTrue(
            service._enforce_restricted_execution_protocol(self.task, [repo]),
        )

    def test_enforce_rep_returns_true_for_empty_repositories(self) -> None:
        # Line 336: ``if not repositories: return True``.
        approval = MagicMock()
        service, _ = _make_service(repository_approval_service=approval)
        self.assertTrue(
            service._enforce_restricted_execution_protocol(self.task, []),
        )

    def test_enforce_rep_returns_true_when_posture_supplier_missing(
        self,
    ) -> None:
        # Line 359: no posture supplier → skip posture gate.
        approval = MagicMock()
        approval.unapproved_repository_ids.return_value = []
        service, repo = _make_service(
            repository_approval_service=approval,
            runtime_posture_supplier=None,
        )
        self.assertTrue(
            service._enforce_restricted_execution_protocol(self.task, [repo]),
        )

    def test_enforce_rep_returns_true_when_no_restricted_repos(self) -> None:
        # Line 364: restricted_mode_repository_ids returns [] → True.
        approval = MagicMock()
        approval.unapproved_repository_ids.return_value = []
        approval.restricted_mode_repository_ids.return_value = []
        posture_supplier = MagicMock()
        service, repo = _make_service(
            repository_approval_service=approval,
            runtime_posture_supplier=posture_supplier,
        )
        self.assertTrue(
            service._enforce_restricted_execution_protocol(self.task, [repo]),
        )

    def test_enforce_rep_fail_open_on_posture_supplier_exception(self) -> None:
        # Lines 367-375: posture supplier crashes → fail-open (True).
        approval = MagicMock()
        approval.unapproved_repository_ids.return_value = []
        approval.restricted_mode_repository_ids.return_value = ['client']
        posture_supplier = MagicMock(side_effect=RuntimeError('posture read failed'))
        service, repo = _make_service(
            repository_approval_service=approval,
            runtime_posture_supplier=posture_supplier,
        )
        service.logger = MagicMock()
        # Fail-open by design — the approval gate above already refused
        # unapproved repos, so this just allows degraded posture-check.
        self.assertTrue(
            service._enforce_restricted_execution_protocol(self.task, [repo]),
        )
        service.logger.exception.assert_called_once()

    def test_enforce_rep_returns_true_when_no_violations(self) -> None:
        # Line 378: no posture violations → True.
        approval = MagicMock()
        approval.unapproved_repository_ids.return_value = []
        approval.restricted_mode_repository_ids.return_value = ['client']
        posture_supplier = MagicMock(return_value=MagicMock())
        with patch(
            'kato_core_lib.data_layers.service.repository_approval_service.'
            'restricted_mode_posture_violations',
            return_value=[],
        ):
            service, repo = _make_service(
                repository_approval_service=approval,
                runtime_posture_supplier=posture_supplier,
            )
            self.assertTrue(
                service._enforce_restricted_execution_protocol(self.task, [repo]),
            )

    def test_security_scan_disabled_returns_true(self) -> None:
        # Lines 427-430: scanner not wired → True. ``enabled=False`` → True.
        service, repo = _make_service()
        # No scanner.
        self.assertTrue(service._run_security_scan(self.task, [repo]))
        # Scanner with enabled=False.
        scanner = MagicMock()
        scanner.enabled = False
        service._security_scanner_service = scanner
        self.assertTrue(service._run_security_scan(self.task, [repo]))

    def test_security_scan_returns_true_for_empty_repositories(self) -> None:
        # Line 432: ``if not repositories: return True``.
        scanner = MagicMock()
        scanner.enabled = True
        service, _ = _make_service(security_scanner_service=scanner)
        self.assertTrue(service._run_security_scan(self.task, []))

    def test_security_scan_skips_repos_with_blank_local_path(self) -> None:
        # Line 439: ``if not workspace_path: continue``.
        scanner = MagicMock()
        scanner.enabled = True
        service, _ = _make_service(security_scanner_service=scanner)
        repo = types.SimpleNamespace(id='client', local_path='')
        # No findings since no repos had a path → True.
        self.assertTrue(service._run_security_scan(self.task, [repo]))
        scanner.scan_workspace.assert_not_called()

    def test_security_scan_swallows_scanner_crash(self) -> None:
        # Lines 442-449: scanner raises → log + continue (other repos
        # can still be scanned; infrastructure flake should not block).
        scanner = MagicMock()
        scanner.enabled = True
        scanner.scan_workspace.side_effect = RuntimeError('scanner crashed')
        service, _ = _make_service(security_scanner_service=scanner)
        service.logger = MagicMock()
        repo = types.SimpleNamespace(id='client', local_path='/some/path')
        self.assertTrue(service._run_security_scan(self.task, [repo]))
        service.logger.exception.assert_called_once()

    def test_security_scan_returns_true_when_block_threshold_none(self) -> None:
        # Lines 453-454: block_threshold is None (scanner returned a
        # report without a configured threshold) → True.
        scanner = MagicMock()
        scanner.enabled = True
        report = MagicMock()
        report.findings = []
        report.runner_errors = []
        report.block_threshold = None
        scanner.scan_workspace.return_value = report
        service, _ = _make_service(security_scanner_service=scanner)
        repo = types.SimpleNamespace(id='client', local_path='/some/path')
        self.assertTrue(service._run_security_scan(self.task, [repo]))

    def test_security_scan_logs_non_blocking_findings(self) -> None:
        # Lines 464-471: non-blocking findings → log + return True.
        from security_scanner_core_lib.security_scanner_core_lib.security_finding import (
            ScanReport,
        )
        finding = MagicMock()
        threshold = MagicMock()
        finding.severity.is_at_least.return_value = False  # below threshold
        scanner = MagicMock()
        scanner.enabled = True
        report = MagicMock()
        report.findings = [finding]
        report.runner_errors = []
        report.block_threshold = threshold
        scanner.scan_workspace.return_value = report
        service, _ = _make_service(security_scanner_service=scanner)
        service.logger = MagicMock()
        repo = types.SimpleNamespace(id='client', local_path='/some/path')
        self.assertTrue(service._run_security_scan(self.task, [repo]))

    def test_security_scan_blocks_without_handler(self) -> None:
        # Lines 480-490: blocking findings AND no handler → log error +
        # return False.
        finding = MagicMock()
        threshold = MagicMock()
        finding.severity.is_at_least.return_value = True  # blocking
        scanner = MagicMock()
        scanner.enabled = True
        report = MagicMock()
        report.findings = [finding]
        report.runner_errors = []
        report.block_threshold = threshold
        scanner.scan_workspace.return_value = report
        service, _ = _make_service(security_scanner_service=scanner)
        service.logger = MagicMock()
        repo = types.SimpleNamespace(id='client', local_path='/some/path')
        result = service._run_security_scan(self.task, [repo])
        self.assertFalse(result)
        service.logger.error.assert_called_once()

    def test_security_scan_blocks_with_handler(self) -> None:
        # Lines 480-482: blocking findings WITH handler → handler invoked.
        finding = MagicMock()
        threshold = MagicMock()
        finding.severity.is_at_least.return_value = True
        scanner = MagicMock()
        scanner.enabled = True
        report = MagicMock()
        report.findings = [finding]
        report.runner_errors = []
        report.block_threshold = threshold
        scanner.scan_workspace.return_value = report
        service, _ = _make_service(security_scanner_service=scanner)
        handler = MagicMock()
        repo = types.SimpleNamespace(id='client', local_path='/some/path')
        result = service._run_security_scan(
            self.task, [repo], failure_handler=handler,
        )
        self.assertFalse(result)
        handler.assert_called_once()

    def test_prepare_task_start_returns_none_when_rep_refuses(self) -> None:
        # Line 217: ``_enforce_restricted_execution_protocol`` returns False
        # → ``return None``.
        approval = MagicMock()
        approval.unapproved_repository_ids.return_value = ['client']
        service, _ = _make_service(repository_approval_service=approval)
        handler = MagicMock()
        result = service._prepare_task_start(
            self.task,
            repository_resolution_failure_handler=handler,
        )
        self.assertIsNone(result)
        handler.assert_called_once()

    def test_prepare_task_start_returns_none_when_security_blocks(self) -> None:
        # Line 232: security scan returns False → None.
        finding = MagicMock()
        threshold = MagicMock()
        finding.severity.is_at_least.return_value = True
        scanner = MagicMock()
        scanner.enabled = True
        report = MagicMock()
        report.findings = [finding]
        report.runner_errors = []
        report.block_threshold = threshold
        scanner.scan_workspace.return_value = report
        service, _ = _make_service(security_scanner_service=scanner)
        handler = MagicMock()
        result = service._prepare_task_start(
            self.task,
            repository_preparation_failure_handler=handler,
        )
        self.assertIsNone(result)

    def test_retry_preconditions_returns_skip_when_prepare_returns_none(
        self,
    ) -> None:
        # Line 185: ``_prepare_task_start`` returns None during retry
        # check → return the skip result so the blocking comment stays.
        service, _ = _make_service()
        # Make resolution fail to force _prepare_task_start to return None.
        service._repository_service.resolve_task_repositories.side_effect = (
            RuntimeError('cannot resolve')
        )
        # Patch retry detector so this counts as a retry.
        with patch(
            'kato_core_lib.client.ticket_client_base.TicketClientBase.'
            'is_pre_start_blocking_comment',
            return_value=True,
        ):
            result = service._check_retry_preconditions(
                self.task, 'Kato encountered a blocking issue',
            )
        # The skip result is a dict; we just verify it's truthy.
        self.assertIsNotNone(result)
        self.assertIsInstance(result, dict)

    def test_prepare_execution_returns_none_when_model_access_fails_on_retry(
        self,
    ) -> None:
        # Line 106: on the retry path, model-access validation fails → None.
        service, _ = _make_service()
        # Make model-access validator raise.
        service._task_model_access_validator.validate.side_effect = (
            RuntimeError('no model access')
        )
        # Force a blocking comment that's pre-start retryable so the
        # retry path is taken.
        with patch(
            'kato_core_lib.client.ticket_client_base.TicketClientBase.'
            'active_execution_blocking_comment',
            return_value='Kato encountered a blocking issue',
        ), patch(
            'kato_core_lib.client.ticket_client_base.TicketClientBase.'
            'is_pre_start_blocking_comment',
            return_value=True,
        ):
            result = service.prepare_task_execution_context(
                self.task,
                task_failure_handler=MagicMock(),
            )
        self.assertIsNone(result)


    def test_check_retry_preconditions_returns_skip_for_non_retryable(self) -> None:
        # Line 177: defensive recheck — non-retryable blocking comment
        # → skip result. Called directly so we drive the inner guard.
        service, _ = _make_service()
        with patch(
            'kato_core_lib.client.ticket_client_base.TicketClientBase.'
            'is_pre_start_blocking_comment',
            return_value=False,
        ):
            result = service._check_retry_preconditions(
                self.task, 'Kato completed task',
            )
        self.assertIsInstance(result, dict)

    def test_validate_task_model_access_returns_false_without_handler(self) -> None:
        # Branch 147->149: ``task_failure_handler is None`` → skip the
        # handler call, still return False. Pre-existing callers that
        # don't supply a handler must not get a NoneType call.
        service, _ = _make_service()
        service._task_model_access_validator.validate.side_effect = (
            RuntimeError('no model access')
        )
        result = service._validate_task_model_access(
            self.task, task_failure_handler=None,
        )
        self.assertFalse(result)

    def test_add_task_comment_returns_true_without_after_step(self) -> None:
        # Branch 769->771: ``if after_step`` is False → skip the
        # ``_log_task_step`` call and return True directly. The default
        # ``after_step=''`` reaches this path on every non-tracking
        # callsite.
        service, _ = _make_service()
        service._log_task_step = MagicMock()
        ok = service._add_task_comment(
            'PROJ-1', 'a comment',
            after_step='',  # explicit: drives the False branch
            failure_log_message='cannot post on %s',
        )
        self.assertTrue(ok)
        service._task_service.add_comment.assert_called_once_with(
            'PROJ-1', 'a comment',
        )
        # No after_step logging happened.
        service._log_task_step.assert_not_called()


if __name__ == '__main__':
    unittest.main()
