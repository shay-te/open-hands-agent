import types
import unittest
from unittest.mock import Mock

import bootstrap  # noqa: F401

from openhands_agent.data_layers.data_access.pull_request_data_access import (
    PullRequestDataAccess,
)
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.data_layers.service.agent_service import AgentService
from openhands_agent.data_layers.service.implementation_service import (
    ImplementationService,
)
from openhands_agent.fields import ImplementationFields, PullRequestFields, StatusFields
from utils import build_review_comment_payload, build_task, build_test_cfg


class AgentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build_test_cfg()
        task_client = types.SimpleNamespace(
            get_assigned_tasks=Mock(
                return_value=[
                    build_task(),
                    build_task(
                        task_id='PROJ-2',
                        summary='Skip bug',
                        branch_name='feature/proj-2',
                    ),
                ]
            ),
            add_pull_request_comment=Mock(),
        )
        self.task_data_access = TaskDataAccess(
            self.cfg.openhands_agent.youtrack,
            task_client,
        )
        self.task_client = task_client
        self.openhands_client = types.SimpleNamespace(
            implement_task=Mock(
                side_effect=[
                    {
                        ImplementationFields.SUCCESS: True,
                        "branch_name": "feature/proj-1",
                        "summary": "Implemented PROJ-1",
                    },
                    {
                        ImplementationFields.SUCCESS: False,
                        "branch_name": "feature/proj-2",
                        "summary": "Failed PROJ-2",
                    },
                ]
            ),
            fix_review_comment=Mock(return_value={ImplementationFields.SUCCESS: True}),
        )
        self.implementation_service = ImplementationService(self.openhands_client)
        pull_request_client = types.SimpleNamespace(
            create_pull_request=Mock(
                return_value={
                    PullRequestFields.ID: "17",
                    PullRequestFields.TITLE: "PROJ-1: Fix bug",
                    PullRequestFields.URL: "https://bitbucket/pr/17",
                }
            )
        )
        self.pull_request_client = pull_request_client
        self.pull_request_data_access = PullRequestDataAccess(
            self.cfg.openhands_agent.bitbucket,
            pull_request_client,
        )
        self.service = AgentService(
            self.task_data_access,
            self.implementation_service,
            self.pull_request_data_access,
        )

    def test_process_assigned_tasks_creates_pull_requests_for_successful_tasks(self) -> None:
        results = self.service.process_assigned_tasks()

        self.assertEqual(
            results,
            [
                {
                    PullRequestFields.ID: "17",
                    PullRequestFields.TITLE: "PROJ-1: Fix bug",
                    PullRequestFields.URL: "https://bitbucket/pr/17",
                }
            ],
        )
        self.pull_request_client.create_pull_request.assert_called_once_with(
            title="PROJ-1: Fix bug",
            source_branch="feature/proj-1",
            workspace="workspace",
            repo_slug="repo",
            destination_branch="main",
            description="Implemented PROJ-1",
        )
        self.task_client.add_pull_request_comment.assert_called_once_with(
            "PROJ-1",
            "https://bitbucket/pr/17",
        )
        self.assertEqual(self.service.pull_request_branch_map, {"17": "feature/proj-1"})

    def test_handle_pull_request_comment_updates_known_branch(self) -> None:
        self.service.pull_request_branch_map["17"] = "feature/proj-1"
        payload = build_review_comment_payload()

        result = self.service.handle_pull_request_comment(payload)

        self.assertEqual(
            result,
            {
                StatusFields.STATUS: StatusFields.UPDATED,
                "pull_request_id": "17",
                "branch_name": "feature/proj-1",
            },
        )
        self.openhands_client.fix_review_comment.assert_called_once()
        comment_arg = self.openhands_client.fix_review_comment.call_args.args[0]
        self.assertEqual(comment_arg.pull_request_id, "17")
        self.assertEqual(comment_arg.comment_id, "99")
        self.assertEqual(comment_arg.author, "reviewer")
        self.assertEqual(comment_arg.body, "Please rename this variable.")

    def test_handle_pull_request_comment_rejects_invalid_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, 'invalid review comment payload'):
            self.service.handle_pull_request_comment({"pull_request_id": "17"})

    def test_handle_pull_request_comment_rejects_unknown_pull_request(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown pull request id"):
            self.service.handle_pull_request_comment(build_review_comment_payload())

    def test_handle_pull_request_comment_raises_when_fix_fails(self) -> None:
        self.openhands_client.fix_review_comment.return_value = {ImplementationFields.SUCCESS: False}
        self.service.pull_request_branch_map["17"] = "feature/proj-1"

        with self.assertRaisesRegex(RuntimeError, "failed to address comment 99"):
            self.service.handle_pull_request_comment(build_review_comment_payload())
