import types
import threading
import unittest

from core_lib.core_lib import CoreLib

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.openhands_agent_core_lib import OpenHandsAgentCoreLib


thread_lock = threading.Lock()


class OblInstance:
    instance = None
    config = None


def assert_client_headers_and_timeout(
    test_case: unittest.TestCase,
    client: object,
    token: str,
    timeout: int,
) -> None:
    test_case.assertEqual(client.headers, {'Authorization': f'Bearer {token}'})
    test_case.assertEqual(client.timeout, timeout)


def build_test_cfg() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        core_lib=types.SimpleNamespace(
            data=types.SimpleNamespace(
                sqlalchemy=types.SimpleNamespace(
                    db_name='openhands_agent',
                    migration_dir='migrations',
                    log_queries=False,
                    url=types.SimpleNamespace(
                        dialect='sqlite',
                        driver='pysqlite',
                        username='',
                        password='',
                        host='',
                        port='',
                        database=':memory:',
                    ),
                )
            )
        ),
        openhands_agent=types.SimpleNamespace(
            youtrack=types.SimpleNamespace(
                name='youtrack-config',
                base_url='https://youtrack.example',
                token='yt-token',
                project='PROJ',
                assignee='me',
                issue_states=['Todo', 'Open'],
            ),
            openhands=types.SimpleNamespace(
                name='openhands-config',
                base_url='https://openhands.example',
                api_key='oh-token',
            ),
            bitbucket=types.SimpleNamespace(
                name='bitbucket-config',
                base_url='https://bitbucket.example',
                token='bb-token',
                workspace='workspace',
                repo_slug='repo',
                destination_branch='main',
            ),
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
) -> Task:
    return Task(
        id=task_id,
        summary=summary,
        description=description,
        branch_name=branch_name,
    )


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
