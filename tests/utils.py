from __future__ import annotations

from base64 import b64encode
import threading
import unittest
from unittest.mock import Mock, patch

from core_lib.core_lib import CoreLib
from omegaconf import DictConfig, OmegaConf

from kato.data_layers.data.review_comment import ReviewComment
from kato.data_layers.data.task import Task
from kato.data_layers.data.fields import ReviewCommentFields, TaskCommentFields
thread_lock = threading.Lock()

__test__ = False


class OblInstance:
    instance = None
    config = None


class Timeout(TimeoutError):
    pass


ClientTimeout = Timeout


def assert_client_headers_and_timeout(
    test_case: unittest.TestCase,
    client: object,
    token: str,
    timeout: int,
) -> None:
    test_case.assertEqual(client.headers, {'Authorization': f'Bearer {token}'})
    test_case.assertEqual(client.timeout, timeout)


def assert_client_basic_auth_and_timeout(
    test_case: unittest.TestCase,
    client: object,
    username: str,
    token: str,
    timeout: int,
) -> None:
    encoded = b64encode(f'{username}:{token}'.encode('utf-8')).decode('ascii')
    test_case.assertEqual(client.headers, {'Authorization': f'Basic {encoded}'})
    test_case.assertEqual(client.timeout, timeout)


def build_test_cfg() -> DictConfig:
    return OmegaConf.create(
        {
            'core_lib': {
                'app': {
                    'name': 'kato',
                },
                'data': {},
                'email_core_lib': {
                    'client': {
                        '_target_': 'email_core_lib.client.send_in_blue_client.SendInBlueClient',
                        'api_key': 'send-in-blue-key',
                        'slack_email_error_url': '',
                    },
                },
            },
            'kato': {
                'issue_platform': 'youtrack',
                'retry': {
                    'max_retries': 5,
                },
                'failure_email': {
                    'enabled': True,
                    'template_id': '42',
                    'body_template': 'failure_email.j2',
                    'recipients': ['ops@example.com', 'dev@example.com'],
                    'sender': {
                        'name': 'Kato',
                        'email': 'noreply@example.com',
                    },
                },
                'completion_email': {
                    'enabled': True,
                    'template_id': '77',
                    'body_template': 'completion_email.j2',
                    'recipients': ['reviewers@example.com', 'teamlead@example.com'],
                    'sender': {
                        'name': 'Kato',
                        'email': 'noreply@example.com',
                    },
                },
                'youtrack': {
                    'name': 'youtrack-config',
                    'provider_name': 'youtrack',
                    'base_url': 'https://youtrack.example',
                    'token': 'yt-token',
                    'project': 'PROJ',
                    'assignee': 'me',
                    'progress_state_field': 'State',
                    'progress_state': 'In Progress',
                    'review_state_field': 'State',
                    'review_state': 'To Verify',
                    'issue_states': ['Todo', 'Open'],
                },
                'jira': {
                    'name': 'jira-config',
                    'provider_name': 'jira',
                    'base_url': 'https://jira.example',
                    'token': 'jira-token',
                    'email': 'dev@example.com',
                    'project': 'PROJ',
                    'assignee': 'developer',
                    'progress_state_field': 'status',
                    'progress_state': 'In Progress',
                    'review_state_field': 'status',
                    'review_state': 'In Review',
                    'issue_states': ['To Do', 'Open'],
                },
                'github_issues': {
                    'name': 'github-issues-config',
                    'provider_name': 'github',
                    'base_url': 'https://api.github.com',
                    'token': 'gh-issues-token',
                    'owner': 'workspace',
                    'repo': 'issues-repo',
                    'project': 'issues-repo',
                    'assignee': 'octocat',
                    'progress_state_field': 'labels',
                    'progress_state': 'In Progress',
                    'review_state_field': 'labels',
                    'review_state': 'In Review',
                    'issue_states': ['open'],
                },
                'gitlab_issues': {
                    'name': 'gitlab-issues-config',
                    'provider_name': 'gitlab',
                    'base_url': 'https://gitlab.example/api/v4',
                    'token': 'gitlab-issues-token',
                    'project': 'group/issues-repo',
                    'assignee': 'developer',
                    'progress_state_field': 'labels',
                    'progress_state': 'In Progress',
                    'review_state_field': 'labels',
                    'review_state': 'In Review',
                    'issue_states': ['opened'],
                },
                'bitbucket_issues': {
                    'name': 'bitbucket-issues-config',
                    'provider_name': 'bitbucket',
                    'base_url': 'https://api.bitbucket.org/2.0',
                    'token': 'bb-issues-token',
                    'username': '',
                    'api_email': 'bb-api@example.com',
                    'workspace': 'workspace',
                    'repo_slug': 'issues-repo',
                    'project': 'issues-repo',
                    'assignee': 'reviewer',
                    'progress_state_field': 'state',
                    'progress_state': 'open',
                    'review_state_field': 'state',
                    'review_state': 'resolved',
                    'issue_states': ['new', 'open'],
                },
                'openhands': {
                    'name': 'openhands-config',
                    'base_url': 'https://openhands.example',
                    'api_key': 'oh-token',
                    'llm_model': 'bedrock/qwen.qwen3-coder-480b-a35b-v1:0',
                    'llm_base_url': '',
                    'model_smoke_test_enabled': True,
                    'skip_testing': False,
                    'testing_container_enabled': False,
                    'testing_base_url': 'https://openhands-testing.example',
                    'testing_llm_model': '',
                    'testing_llm_base_url': '',
                },
                'task_scan': {
                    'startup_delay_seconds': 30,
                    'scan_interval_seconds': 60,
                },
                'repository': {
                    'name': 'repository-config',
                    'base_url': 'https://bitbucket.example',
                    'token': 'bb-token',
                    'owner': 'workspace',
                    'repo_slug': 'repo',
                    'destination_branch': 'main',
                },
                'repositories': [
                    {
                        'id': 'client',
                        'display_name': 'Client',
                        'local_path': '.',
                        'provider_base_url': 'https://bitbucket.example',
                        'token': 'bb-token',
                        'api_email': 'bb-api@example.com',
                        'owner': 'workspace',
                        'repo_slug': 'repo',
                        'destination_branch': '',
                        'aliases': ['client', 'frontend'],
                    },
                    {
                        'id': 'backend',
                        'display_name': 'Backend',
                        'local_path': '.',
                        'provider_base_url': 'https://github.example/api/v3',
                        'token': 'gh-token',
                        'owner': 'workspace',
                        'repo_slug': 'backend',
                        'destination_branch': 'main',
                        'aliases': ['backend', 'api'],
                    },
                ],
            },
        }
    )


def load_config() -> DictConfig:
    if not OblInstance.config:
        OblInstance.config = build_test_cfg()
    return OblInstance.config


def sync_create_start_core_lib() -> KatoCoreLib:
    with thread_lock:
        if not OblInstance.instance:
            [CoreLib.cache_registry.unregister(key) for key in CoreLib.cache_registry.registered()]
            [CoreLib.observer_registry.unregister(key) for key in CoreLib.observer_registry.registered()]
            from unittest.mock import patch
            from kato.kato_core_lib import KatoCoreLib

            with patch(
                'kato.kato_core_lib.EmailCoreLib'
            ), patch(
                'kato.kato_core_lib.AgentService.validate_connections'
            ):
                OblInstance.instance = KatoCoreLib(load_config())
            OblInstance.instance.start_core_lib()

        for key in CoreLib.cache_registry.registered():
            cache = CoreLib.cache_registry.get(key)
            flush_all = getattr(cache, 'flush_all', None)
            if callable(flush_all):
                flush_all()

        return OblInstance.instance


def build_review_comment_payload() -> dict[str, str]:
    return {
        ReviewCommentFields.PULL_REQUEST_ID: '17',
        ReviewCommentFields.COMMENT_ID: '99',
        ReviewCommentFields.AUTHOR: 'reviewer',
        ReviewCommentFields.BODY: 'Please rename this variable.',
    }


def build_task(
    task_id: str = 'PROJ-1',
    summary: str = 'Fix bug',
    description: str = 'Details',
    branch_name: str = 'feature/proj-1',
    tags: list[str] | None = None,
    repositories: list | None = None,
    repository_branches: dict | None = None,
    comments: list[dict[str, str]] | None = None,
) -> Task:
    task = Task(
        id=task_id,
        summary=summary,
        description=description,
        branch_name=branch_name,
        tags=tags,
    )
    if repositories is not None:
        task.repositories = repositories
    if repository_branches is not None:
        task.repository_branches = repository_branches
    if comments is not None:
        setattr(task, TaskCommentFields.ALL_COMMENTS, comments)
    return task


def build_review_comment(
    pull_request_id: str = '17',
    comment_id: str = '99',
    author: str = 'reviewer',
    body: str = 'Please rename this variable.',
    resolution_target_id: str = '',
    resolution_target_type: str = '',
    resolvable: bool | None = None,
) -> ReviewComment:
    comment = ReviewComment(
        pull_request_id=pull_request_id,
        comment_id=comment_id,
        author=author,
        body=body,
    )
    if resolution_target_id:
        setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, resolution_target_id)
    if resolution_target_type:
        setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE, resolution_target_type)
    if resolvable is not None:
        setattr(comment, ReviewCommentFields.RESOLVABLE, resolvable)
    return comment


def mock_response(
    *,
    json_data=None,
    status_code: int = 200,
    text='',
    content=b'',
) -> Mock:
    response = Mock(status_code=status_code)
    response.json.return_value = json_data
    response.text = text
    response.content = content
    return response


def create_pull_request_with_defaults(
    client,
    title: str = 'PROJ-1: Fix bug',
    source_branch: str = 'feature/proj-1',
    repo_owner: str = 'workspace',
    repo_slug: str = 'repo',
    destination_branch: str = 'main',
    description: str = '',
):
    return client.create_pull_request(
        title=title,
        source_branch=source_branch,
        repo_owner=repo_owner,
        repo_slug=repo_slug,
        destination_branch=destination_branch,
        description=description,
    )


def implement_task_with_defaults(
    client,
    task: Task | None = None,
    session_id: str = '',
):
    with patch.object(client, '_patch', return_value=mock_response()):
        return client.implement_task(task or build_task(), '')


def test_task_with_defaults(client, task: Task | None = None):
    with patch.object(client, '_patch', return_value=mock_response()):
        return client.test_task(task or build_task())


test_task_with_defaults.__test__ = False


def fix_review_comment_with_defaults(
    client,
    comment: ReviewComment | None = None,
    branch_name: str = 'feature/proj-1',
    session_id: str = '',
    task_id: str = '',
    task_summary: str = '',
):
    with patch.object(client, '_patch', return_value=mock_response()):
        return client.fix_review_comment(
            comment or build_review_comment(),
            branch_name,
            session_id,
            task_id=task_id,
            task_summary=task_summary,
        )


def get_assigned_tasks_with_defaults(
    client,
    project: str = 'PROJ',
    assignee: str = 'me',
    states: list[str] | None = None,
):
    return client.get_assigned_tasks(project, assignee, states or ['Todo', 'Open'])


def add_pull_request_comment_with_defaults(
    client,
    issue_id: str = 'PROJ-1',
    pull_request_url: str = 'https://bitbucket/pr/1',
):
    return client.add_pull_request_comment(issue_id, pull_request_url)


def move_issue_to_state_with_defaults(
    client,
    issue_id: str = 'PROJ-1',
    field_name: str = 'State',
    state_name: str = 'In Review',
):
    return client.move_issue_to_state(issue_id, field_name, state_name)
