from __future__ import annotations

import types
import unittest
from unittest.mock import patch

import bootstrap  # noqa: F401

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.data_access.implementation_data_access import (
    ImplementationDataAccess,
)
from openhands_agent.data_layers.data_access.pull_request_data_access import (
    PullRequestDataAccess,
)
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess


class TaskDataAccessTests(unittest.TestCase):
    def test_uses_base_url_only_for_client_and_passes_runtime_values(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://youtrack.example",
            token="yt-token",
            project="PROJ",
            assignee="me",
            issue_state="Open",
        )

        with patch(
            "openhands_agent.data_layers.data_access.task_data_access.YouTrackClient"
        ) as mock_client_cls:
            data_access = TaskDataAccess(config)
            data_access.get_assigned_tasks()
            data_access.add_pull_request_comment("PROJ-1", "https://bitbucket/pr/1")

        mock_client_cls.assert_called_once_with("https://youtrack.example")
        client = mock_client_cls.return_value
        client.get_assigned_tasks.assert_called_once_with(
            token="yt-token",
            project="PROJ",
            assignee="me",
            state="Open",
        )
        client.add_pull_request_comment.assert_called_once_with(
            "yt-token",
            "PROJ-1",
            "https://bitbucket/pr/1",
        )


class PullRequestDataAccessTests(unittest.TestCase):
    def test_passes_bitbucket_settings_to_client_call(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://bitbucket.example",
            token="bb-token",
            workspace="workspace",
            repo_slug="repo",
            destination_branch="main",
        )

        with patch(
            "openhands_agent.data_layers.data_access.pull_request_data_access.BitbucketClient"
        ) as mock_client_cls:
            data_access = PullRequestDataAccess(config)
            data_access.create_pull_request(
                title="PROJ-1: Fix bug",
                source_branch="feature/proj-1",
                description="Ready for review",
            )

        mock_client_cls.assert_called_once_with("https://bitbucket.example")
        mock_client_cls.return_value.create_pull_request.assert_called_once_with(
            title="PROJ-1: Fix bug",
            source_branch="feature/proj-1",
            token="bb-token",
            workspace="workspace",
            repo_slug="repo",
            destination_branch="main",
            description="Ready for review",
        )


class ImplementationDataAccessTests(unittest.TestCase):
    def test_passes_api_key_to_openhands_client_calls(self) -> None:
        config = types.SimpleNamespace(
            base_url="https://openhands.example",
            api_key="oh-token",
        )
        task = Task(
            id="PROJ-1",
            summary="Fix bug",
            description="Details",
            branch_name="feature/proj-1",
        )
        comment = ReviewComment(
            pull_request_id="17",
            comment_id="99",
            author="reviewer",
            body="Please rename this variable.",
        )

        with patch(
            "openhands_agent.data_layers.data_access.implementation_data_access.OpenHandsClient"
        ) as mock_client_cls:
            data_access = ImplementationDataAccess(config)
            data_access.implement_task(task)
            data_access.fix_review_comment(comment, "feature/proj-1")

        mock_client_cls.assert_called_once_with("https://openhands.example")
        client = mock_client_cls.return_value
        client.implement_task.assert_called_once_with("oh-token", task)
        client.fix_review_comment.assert_called_once_with(
            "oh-token",
            comment,
            "feature/proj-1",
        )
