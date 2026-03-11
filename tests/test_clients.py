from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import bootstrap  # noqa: F401

from openhands_agent.client.bitbucket_client import BitbucketClient
from openhands_agent.client.openhands_client import OpenHandsClient
from openhands_agent.client.youtrack_client import YouTrackClient
from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task


class YouTrackClientTests(unittest.TestCase):
    def test_get_assigned_tasks_builds_query_and_maps_tasks(self) -> None:
        client = YouTrackClient("https://youtrack.example")
        response = Mock()
        response.json.return_value = [
            {"idReadable": "PROJ-1", "summary": "Fix bug", "description": "Details"}
        ]

        with patch.object(client, "_get", return_value=response) as mock_get:
            tasks = client.get_assigned_tasks(
                token="yt-token",
                project="PROJ",
                assignee="me",
                state="Open",
            )

        response.raise_for_status.assert_called_once_with()
        self.assertEqual(
            tasks,
            [
                Task(
                    id="PROJ-1",
                    summary="Fix bug",
                    description="Details",
                    branch_name="feature/proj-1",
                )
            ],
        )
        mock_get.assert_called_once_with(
            "/api/issues",
            headers={"Authorization": "Bearer yt-token"},
            timeout=30,
            params={
                "query": "project: PROJ assignee: me State: {Open}",
                "fields": "idReadable,summary,description",
            },
        )

    def test_add_pull_request_comment_posts_expected_payload(self) -> None:
        client = YouTrackClient("https://youtrack.example")
        response = Mock()

        with patch.object(client, "_post", return_value=response) as mock_post:
            client.add_pull_request_comment("yt-token", "PROJ-1", "https://bitbucket/pr/1")

        response.raise_for_status.assert_called_once_with()
        mock_post.assert_called_once_with(
            "/api/issues/PROJ-1/comments",
            headers={"Authorization": "Bearer yt-token"},
            timeout=30,
            json={"text": "Pull request created: https://bitbucket/pr/1"},
        )


class BitbucketClientTests(unittest.TestCase):
    def test_create_pull_request_normalizes_response(self) -> None:
        client = BitbucketClient("https://bitbucket.example")
        response = Mock()
        response.json.return_value = {
            "id": 7,
            "title": "PROJ-1: Fix bug",
            "links": {"html": {"href": "https://bitbucket/pr/7"}},
        }

        with patch.object(client, "_post", return_value=response) as mock_post:
            pr = client.create_pull_request(
                title="PROJ-1: Fix bug",
                source_branch="feature/proj-1",
                token="bb-token",
                workspace="workspace",
                repo_slug="repo",
                destination_branch="main",
                description="Ready for review",
            )

        response.raise_for_status.assert_called_once_with()
        self.assertEqual(
            pr,
            {
                "id": "7",
                "title": "PROJ-1: Fix bug",
                "url": "https://bitbucket/pr/7",
            },
        )
        mock_post.assert_called_once_with(
            "/repositories/workspace/repo/pullrequests",
            headers={"Authorization": "Bearer bb-token"},
            timeout=30,
            json={
                "title": "PROJ-1: Fix bug",
                "description": "Ready for review",
                "source": {"branch": {"name": "feature/proj-1"}},
                "destination": {"branch": {"name": "main"}},
            },
        )


class OpenHandsClientTests(unittest.TestCase):
    def test_implement_task_posts_prompt(self) -> None:
        client = OpenHandsClient("https://openhands.example")
        response = Mock()
        response.json.return_value = {
            "summary": "Implemented task",
            "commit_message": "Implement PROJ-1",
            "success": True,
        }
        task = Task(
            id="PROJ-1",
            summary="Fix bug",
            description="Details",
            branch_name="feature/proj-1",
        )

        with patch.object(client, "_post", return_value=response) as mock_post:
            result = client.implement_task("oh-token", task)

        response.raise_for_status.assert_called_once_with()
        self.assertEqual(
            result,
            {
                "branch_name": "feature/proj-1",
                "summary": "Implemented task",
                "commit_message": "Implement PROJ-1",
                "success": True,
            },
        )
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args, ("/api/sessions",))
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer oh-token"})
        self.assertEqual(kwargs["timeout"], 300)
        self.assertIn("Implement task PROJ-1: Fix bug", kwargs["json"]["prompt"])

    def test_fix_review_comment_posts_prompt(self) -> None:
        client = OpenHandsClient("https://openhands.example")
        response = Mock()
        response.json.return_value = {
            "summary": "Updated branch",
            "commit_message": "Address review comments",
            "success": True,
        }
        comment = ReviewComment(
            pull_request_id="17",
            comment_id="99",
            author="reviewer",
            body="Please rename this variable.",
        )

        with patch.object(client, "_post", return_value=response) as mock_post:
            result = client.fix_review_comment("oh-token", comment, "feature/proj-1")

        response.raise_for_status.assert_called_once_with()
        self.assertEqual(result["branch_name"], "feature/proj-1")
        self.assertTrue(result["success"])
        mock_post.assert_called_once()
        self.assertIn("Comment by reviewer: Please rename this variable.", mock_post.call_args.kwargs["json"]["prompt"])
