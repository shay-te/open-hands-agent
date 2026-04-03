import types
import unittest
from unittest.mock import Mock, patch

from openhands_agent.client.ticket_client_base import TicketClientBase
from openhands_agent.data_layers.data.fields import PullRequestFields, StatusFields
from openhands_agent.data_layers.service.task_preflight_service import (
    TaskPreflightService,
)
from openhands_agent.helpers.task_context_utils import PreparedTaskContext
from utils import build_task


class TaskPreflightServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = build_task(
            summary='Update the client and backend flow',
            description='Implement the client and backend change',
        )
        self.repository = types.SimpleNamespace(
            id='client',
            local_path='/workspace/client',
            destination_branch='main',
        )
        self.repositories = [self.repository]
        self.prepared_task = PreparedTaskContext(
            branch_name='feature/proj-1/client',
            repositories=self.repositories,
            repository_branches={'client': 'feature/proj-1/client'},
        )
        self.task_model_access_validator = Mock()
        self.task_service = Mock()
        self.repository_service = Mock()
        self.repository_service.resolve_task_repositories.return_value = self.repositories
        self.repository_service.prepare_task_repositories.side_effect = lambda repositories: repositories
        self.repository_service.prepare_task_branches.side_effect = (
            lambda repositories, repository_branches: repositories
        )
        self.repository_service.build_branch_name.return_value = 'feature/proj-1/client'
        self.task_branch_push_validator = Mock()
        self.task_branch_publishability_validator = Mock()
        self.task_branch_push_validator.validate.return_value = None
        self.task_branch_publishability_validator.validate.return_value = None
        self.service = TaskPreflightService(
            task_model_access_validator=self.task_model_access_validator,
            task_service=self.task_service,
            repository_service=self.repository_service,
            task_branch_push_validator=self.task_branch_push_validator,
            task_branch_publishability_validator=self.task_branch_publishability_validator,
        )

    def test_prepare_task_execution_context_returns_prepared_context_on_happy_path(self) -> None:
        result = self.service.prepare_task_execution_context(self.task)

        self.assertIsInstance(result, PreparedTaskContext)
        self.assertEqual(result.branch_name, 'feature/proj-1/client')
        self.assertEqual(result.repositories, self.repositories)
        self.assertEqual(result.repository_branches, {'client': 'feature/proj-1/client'})
        self.task_model_access_validator.validate.assert_called_once_with(self.task)
        self.repository_service.resolve_task_repositories.assert_called_once_with(self.task)
        self.repository_service.prepare_task_repositories.assert_called_once_with(self.repositories)
        self.repository_service.build_branch_name.assert_called_once_with(
            self.task,
            self.repository,
        )
        self.repository_service.prepare_task_branches.assert_called_once_with(
            self.repositories,
            {'client': 'feature/proj-1/client'},
        )
        self.task_branch_push_validator.validate.assert_called_once_with(
            self.repositories,
            {'client': 'feature/proj-1/client'},
        )

    def test_prepare_task_execution_context_reports_model_access_failure(self) -> None:
        self.task_model_access_validator.validate.side_effect = RuntimeError('model offline')
        failure_handler = Mock()

        result = self.service.prepare_task_execution_context(
            self.task,
            task_failure_handler=failure_handler,
        )

        self.assertIsNone(result)
        failure_handler.assert_called_once()
        self.assertIs(failure_handler.call_args.args[0], self.task)
        self.assertIsInstance(failure_handler.call_args.args[1], RuntimeError)
        self.assertIsNone(failure_handler.call_args.args[2])
        self.repository_service.resolve_task_repositories.assert_not_called()

    def test_prepare_task_execution_context_routes_repository_resolution_failure(self) -> None:
        self.repository_service.resolve_task_repositories.side_effect = ValueError(
            'no configured repository matched task PROJ-1'
        )
        failure_handler = Mock()

        result = self.service.prepare_task_execution_context(
            self.task,
            repository_resolution_failure_handler=failure_handler,
        )

        self.assertIsNone(result)
        failure_handler.assert_called_once()
        self.assertIs(failure_handler.call_args.args[0], self.task)
        self.assertIsInstance(failure_handler.call_args.args[1], ValueError)
        self.assertIsNone(failure_handler.call_args.args[2])
        self.repository_service.prepare_task_repositories.assert_not_called()

    def test_prepare_task_execution_context_skips_thin_task_definition_with_handler(self) -> None:
        thin_task = build_task(summary='tiny', description='No description provided.')
        failure_handler = Mock()

        result = self.service.prepare_task_execution_context(
            thin_task,
            task_definition_failure_handler=failure_handler,
        )

        self.assertIsNone(result)
        failure_handler.assert_called_once_with(thin_task)
        self.repository_service.resolve_task_repositories.assert_called_once_with(thin_task)
        self.repository_service.prepare_task_repositories.assert_called_once_with(self.repositories)
        self.repository_service.prepare_task_branches.assert_not_called()
        self.task_branch_push_validator.validate.assert_not_called()

    def test_prepare_task_execution_context_skips_when_completion_comment_is_active(self) -> None:
        with patch.object(
            self.service,
            '_active_execution_blocking_comment',
            return_value='OpenHands completed task PROJ-1.',
        ), patch.object(self.service, '_prepare_task_start') as mock_prepare_task_start:
            result = self.service.prepare_task_execution_context(self.task)

        self.assertEqual(result[StatusFields.STATUS], StatusFields.SKIPPED)
        self.assertEqual(result['id'], self.task.id)
        mock_prepare_task_start.assert_not_called()
        self.repository_service.resolve_task_repositories.assert_not_called()

    def test_prepare_task_execution_context_retries_when_prior_blocking_comment_clears(self) -> None:
        with patch.object(
            self.service,
            '_active_execution_blocking_comment',
            return_value=TicketClientBase.PRE_START_BLOCKING_PREFIXES[0],
        ), patch.object(
            self.service,
            '_prepare_task_start',
            return_value=self.prepared_task,
        ) as mock_prepare_task_start:
            result = self.service.prepare_task_execution_context(self.task)

        self.assertIs(result, self.prepared_task)
        mock_prepare_task_start.assert_called_once_with(self.task)

    def test_prepare_task_execution_context_skips_when_prior_blocking_comment_persists(self) -> None:
        with patch.object(
            self.service,
            '_active_execution_blocking_comment',
            return_value=TicketClientBase.PRE_START_BLOCKING_PREFIXES[0],
        ), patch.object(
            self.service,
            '_prepare_task_start',
            return_value=None,
        ) as mock_prepare_task_start:
            result = self.service.prepare_task_execution_context(self.task)

        self.assertEqual(result[StatusFields.STATUS], StatusFields.SKIPPED)
        self.assertEqual(result['id'], self.task.id)
        mock_prepare_task_start.assert_called_once_with(self.task)

    def test_validate_task_branch_push_access_returns_false_without_failure_handler(self) -> None:
        self.task_branch_push_validator.validate.side_effect = RuntimeError('missing push')

        result = self.service.validate_task_branch_push_access(self.task, self.prepared_task)

        self.assertFalse(result)

    def test_validate_task_branch_publishability_invokes_failure_handler_when_blocked(self) -> None:
        self.task_branch_publishability_validator.validate.side_effect = RuntimeError('no changes')
        failure_handler = Mock()

        result = self.service.validate_task_branch_publishability(
            self.task,
            self.prepared_task,
            failure_handler=failure_handler,
        )

        self.assertFalse(result)
        failure_handler.assert_called_once()
        self.assertIs(failure_handler.call_args.args[0], self.task)
        self.assertIsInstance(failure_handler.call_args.args[1], RuntimeError)
        self.assertIs(failure_handler.call_args.args[2], self.prepared_task)
