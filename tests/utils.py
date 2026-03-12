import types
import threading
import unittest
from unittest.mock import Mock

from core_lib.core_lib import CoreLib

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib


thread_lock = threading.Lock()


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


def build_test_cfg() -> types.SimpleNamespace:
    repositories = [
        types.SimpleNamespace(
            id='client',
            display_name='Client',
            local_path='.',
            provider_base_url='https://bitbucket.example',
            token='bb-token',
            owner='workspace',
            repo_slug='repo',
            destination_branch='',
            aliases=['client', 'frontend'],
        ),
        types.SimpleNamespace(
            id='backend',
            display_name='Backend',
            local_path='.',
            provider_base_url='https://github.example/api/v3',
            token='gh-token',
            owner='workspace',
            repo_slug='backend',
            destination_branch='main',
            aliases=['backend', 'api'],
        ),
    ]
    return types.SimpleNamespace(
        core_lib=types.SimpleNamespace(
            app=types.SimpleNamespace(
                name='openhands-agent',
            ),
            data=types.SimpleNamespace(
                sqlalchemy=types.SimpleNamespace(
                    log_queries=False,
                    create_db=True,
                    session=types.SimpleNamespace(
                        pool_recycle=3600,
                        pool_pre_ping=False,
                    ),
                    url=types.SimpleNamespace(
                        protocol='sqlite',
                        username='',
                        password='',
                        host='',
                        port='',
                        path='',
                        file=':memory:',
                    ),
                ),
            ),
            alembic=types.SimpleNamespace(
                version_table='alembic_version',
                script_location='data_layers/data/db/migrations',
                render_as_batch=True,
            ),
            email_core_lib=types.SimpleNamespace(
                client=types.SimpleNamespace(
                    _target_='email_core_lib.client.send_in_blue_client.SendInBlueClient',
                    api_key='send-in-blue-key',
                    slack_email_error_url='',
                )
            )
        ),
        openhands_agent=types.SimpleNamespace(
            retry=types.SimpleNamespace(
                max_retries=5,
            ),
            failure_email=types.SimpleNamespace(
                enabled=True,
                template_id='42',
                body_template='failure_email.txt',
                recipients=['ops@example.com', 'dev@example.com'],
                sender=types.SimpleNamespace(
                    name='OpenHands Agent',
                    email='noreply@example.com',
                ),
            ),
            completion_email=types.SimpleNamespace(
                enabled=True,
                template_id='77',
                body_template='completion_email.txt',
                recipients=['reviewers@example.com', 'teamlead@example.com'],
                sender=types.SimpleNamespace(
                    name='OpenHands Agent',
                    email='noreply@example.com',
                ),
            ),
            youtrack=types.SimpleNamespace(
                name='youtrack-config',
                base_url='https://youtrack.example',
                token='yt-token',
                project='PROJ',
                assignee='me',
                review_state_field='State',
                review_state='In Review',
                issue_states=['Todo', 'Open'],
            ),
            openhands=types.SimpleNamespace(
                name='openhands-config',
                base_url='https://openhands.example',
                api_key='oh-token',
            ),
            repository=types.SimpleNamespace(
                name='repository-config',
                base_url='https://bitbucket.example',
                token='bb-token',
                owner='workspace',
                repo_slug='repo',
                destination_branch='main',
            ),
            repositories=repositories,
        )
    )


def load_config():
    if not OblInstance.config:
        OblInstance.config = build_test_cfg()
    return OblInstance.config


def sync_create_start_core_lib() -> OpenHandsAgentCoreLib:
    with thread_lock:
        if not OblInstance.instance:
            [CoreLib.cache_registry.unregister(key) for key in CoreLib.cache_registry.registered()]
            [CoreLib.observer_registry.unregister(key) for key in CoreLib.observer_registry.registered()]
            from unittest.mock import patch

            with patch(
                'openhands_agent.openhands_agent_core_lib.AgentService.validate_connections'
            ):
                OblInstance.instance = OpenHandsAgentCoreLib(load_config())
            OblInstance.instance.start_core_lib()

        for key in CoreLib.cache_registry.registered():
            cache = CoreLib.cache_registry.get(key)
            flush_all = getattr(cache, 'flush_all', None)
            if callable(flush_all):
                flush_all()

        return OblInstance.instance


def build_review_comment_payload() -> dict[str, str]:
    return {
        'pull_request_id': '17',
        'comment_id': '99',
        'author': 'reviewer',
        'body': 'Please rename this variable.',
    }


def build_task(
    task_id: str = 'PROJ-1',
    summary: str = 'Fix bug',
    description: str = 'Details',
    branch_name: str = 'feature/proj-1',
    repositories: list | None = None,
    repository_branches: dict | None = None,
) -> Task:
    task = Task(
        id=task_id,
        summary=summary,
        description=description,
        branch_name=branch_name,
    )
    if repositories is not None:
        task.repositories = repositories
    if repository_branches is not None:
        task.repository_branches = repository_branches
    return task


def build_review_comment(
    pull_request_id: str = '17',
    comment_id: str = '99',
    author: str = 'reviewer',
    body: str = 'Please rename this variable.',
) -> ReviewComment:
    return ReviewComment(
        pull_request_id=pull_request_id,
        comment_id=comment_id,
        author=author,
        body=body,
    )


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


def implement_task_with_defaults(client, task: Task | None = None):
    return client.implement_task(task or build_task())


def test_task_with_defaults(client, task: Task | None = None):
    return client.test_task(task or build_task())


def fix_review_comment_with_defaults(
    client,
    comment: ReviewComment | None = None,
    branch_name: str = 'feature/proj-1',
):
    return client.fix_review_comment(comment or build_review_comment(), branch_name)


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
