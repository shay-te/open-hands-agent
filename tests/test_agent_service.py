from __future__ import annotations

import types
import unittest
from unittest.mock import Mock

import bootstrap  # noqa: F401

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.service.agent_service import AgentService
class AgentServiceTests(unittest.TestCase):
    def test_process_assigned_tasks_creates_pull_requests_for_successful_tasks(self) -> None:
        task_one = Task(
            id="PROJ-1",
            summary="Fix bug",
            description="Details",
            branch_name="feature/proj-1",
        )
        task_two = Task(
            id="PROJ-2",
            summary="Skip bug",
            description="Details",
            branch_name="feature/proj-2",
        )

        task_da = types.SimpleNamespace()
        impl_da = types.SimpleNamespace()
        pr_da = types.SimpleNamespace()

        task_da.get_assigned_tasks = lambda: [task_one, task_two]
        task_da.add_pull_request_comment = Mock()
        impl_da.implement_task = Mock(
            side_effect=[
                {
                    "success": True,
                    "branch_name": "feature/proj-1",
                    "summary": "Implemented PROJ-1",
                },
                {
                    "success": False,
                    "branch_name": "feature/proj-2",
                    "summary": "Failed PROJ-2",
                },
            ]
        )
        pr_da.create_pull_request = Mock(
            return_value={
                "id": "17",
                "title": "PROJ-1: Fix bug",
                "url": "https://bitbucket/pr/17",
            }
        )

        service = AgentService(task_da, impl_da, pr_da)
        results = service.process_assigned_tasks()

        self.assertEqual(
            results,
            [
                {
                    "id": "17",
                    "title": "PROJ-1: Fix bug",
                    "url": "https://bitbucket/pr/17",
                }
            ],
        )
        pr_da.create_pull_request.assert_called_once_with(
            title="PROJ-1: Fix bug",
            source_branch="feature/proj-1",
            description="Implemented PROJ-1",
        )
        task_da.add_pull_request_comment.assert_called_once_with(
            "PROJ-1",
            "https://bitbucket/pr/17",
        )
        self.assertEqual(service.pull_request_branch_map, {"17": "feature/proj-1"})

    def test_handle_pull_request_comment_updates_known_branch(self) -> None:
        impl_da = types.SimpleNamespace(fix_review_comment=Mock(return_value={"success": True}))
        service = AgentService(types.SimpleNamespace(), impl_da, types.SimpleNamespace())
        service.pull_request_branch_map["17"] = "feature/proj-1"

        result = service.handle_pull_request_comment(
            {
                "pull_request_id": "17",
                "comment_id": "99",
                "author": "reviewer",
                "body": "Please rename this variable.",
            }
        )

        self.assertEqual(
            result,
            {
                "status": "updated",
                "pull_request_id": "17",
                "branch_name": "feature/proj-1",
            },
        )
        impl_da.fix_review_comment.assert_called_once()
        comment_arg = impl_da.fix_review_comment.call_args.args[0]
        self.assertIsInstance(comment_arg, ReviewComment)

    def test_handle_pull_request_comment_rejects_invalid_payload(self) -> None:
        service = AgentService(types.SimpleNamespace(), types.SimpleNamespace(), types.SimpleNamespace())

        with self.assertRaises(ValueError):
            service.handle_pull_request_comment({"pull_request_id": "17"})

    def test_handle_pull_request_comment_rejects_unknown_pull_request(self) -> None:
        service = AgentService(types.SimpleNamespace(), types.SimpleNamespace(), types.SimpleNamespace())

        with self.assertRaisesRegex(ValueError, "unknown pull request id"):
            service.handle_pull_request_comment(
                {
                    "pull_request_id": "17",
                    "comment_id": "99",
                    "author": "reviewer",
                    "body": "Please rename this variable.",
                }
            )

    def test_handle_pull_request_comment_raises_when_fix_fails(self) -> None:
        impl_da = types.SimpleNamespace(fix_review_comment=Mock(return_value={"success": False}))
        service = AgentService(types.SimpleNamespace(), impl_da, types.SimpleNamespace())
        service.pull_request_branch_map["17"] = "feature/proj-1"

        with self.assertRaisesRegex(RuntimeError, "failed to address comment 99"):
            service.handle_pull_request_comment(
                {
                    "pull_request_id": "17",
                    "comment_id": "99",
                    "author": "reviewer",
                    "body": "Please rename this variable.",
                }
            )
