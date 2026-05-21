"""Shared test utilities for youtrack_core_lib tests."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from youtrack_core_lib.youtrack_core_lib.data.fields import TaskCommentFields
from youtrack_core_lib.youtrack_core_lib.data.task import Task

__test__ = False


class ClientTimeout(TimeoutError):
    """Simulated transient network timeout for tests."""


def mock_response(
    *,
    json_data=None,
    status_code: int = 200,
    text: str = '',
    content: bytes = b'',
) -> Mock:
    response = Mock(status_code=status_code)
    response.json.return_value = json_data
    response.text = text
    response.content = content
    return response


def assert_client_headers_and_timeout(
    test_case: unittest.TestCase,
    client: object,
    token: str,
    timeout: int,
) -> None:
    test_case.assertEqual(client.headers, {'Authorization': f'Bearer {token}'})
    test_case.assertEqual(client.timeout, timeout)


def get_assigned_tasks_with_defaults(
    client,
    project: str = 'PROJ',
    assignee: str = 'me',
    states: list[str] | None = None,
) -> list[Task]:
    return client.get_assigned_tasks(project, assignee, states or ['Todo', 'Open'])


def add_pull_request_comment_with_defaults(
    client,
    issue_id: str = 'PROJ-1',
    pull_request_url: str = 'https://bitbucket/pr/1',
) -> None:
    return client.add_pull_request_comment(issue_id, pull_request_url)


def move_issue_to_state_with_defaults(
    client,
    issue_id: str = 'PROJ-1',
    field_name: str = 'State',
    state_name: str = 'In Review',
) -> None:
    return client.move_issue_to_state(issue_id, field_name, state_name)


def build_task(
    task_id: str = 'PROJ-1',
    summary: str = 'fix it already',
    description: str = 'Details',
    branch_name: str = 'feature/proj-1',
    tags: list[str] | None = None,
    comments: list[dict] | None = None,
) -> Task:
    task = Task(
        id=task_id,
        summary=summary,
        description=description,
        branch_name=branch_name,
        tags=tags,
    )
    if comments is not None:
        setattr(task, TaskCommentFields.ALL_COMMENTS, comments)
    return task
