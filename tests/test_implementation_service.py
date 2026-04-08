import types
import unittest
from unittest.mock import Mock

from kato.data_layers.service.implementation_service import (
    ImplementationService,
)
from utils import build_task


class ImplementationServiceTests(unittest.TestCase):
    def test_passes_kato_client_calls(self) -> None:
        client = types.SimpleNamespace(
            implement_task=Mock(),
            fix_review_comment=Mock(),
        )
        service = ImplementationService(client)
        service.logger = Mock()
        task = build_task()
        comment = types.SimpleNamespace(pull_request_id='17', comment_id='99')

        service.implement_task(task, 'conversation-1')
        service.fix_review_comment(comment, 'feature/proj-1', 'conversation-1')

        service.logger.info.assert_any_call('delegating implementation for task %s', 'PROJ-1')
        service.logger.info.assert_any_call(
            'delegating review fix for pull request %s comment %s',
            '17',
            '99',
        )
        client.implement_task.assert_called_once_with(
            task,
            'conversation-1',
            prepared_task=None,
        )
        client.fix_review_comment.assert_called_once_with(
            comment,
            'feature/proj-1',
            'conversation-1',
            task_id='',
            task_summary='',
        )


if __name__ == '__main__':
    unittest.main()
