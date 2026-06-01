"""The review-comment scan must skip tasks the operator forgot.

Regression: forgetting a task wipes its local workspace/record, but the task can
still be IN REVIEW on the platform with unresolved PR comments. The scan polls
the platform (``get_review_tasks``), so a forgotten task was being resurrected
on the next tick / after a restart. ``forgotten_task_ids`` is now consulted in
``_review_pull_request_contexts``.
"""
import types
import unittest
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.data.fields import PullRequestFields
from kato_core_lib.data_layers.service.review_comment_service import ReviewCommentService


class ForgottenTaskScanSkipTests(unittest.TestCase):
    def _service(self, review_tasks):
        task_service = MagicMock()
        task_service.get_review_tasks.return_value = review_tasks
        return ReviewCommentService(
            task_service=task_service,
            implementation_service=MagicMock(),
            repository_service=MagicMock(),
            state_registry=MagicMock(),
        )

    @staticmethod
    def _ctx_for(task):
        return [{
            PullRequestFields.ID: task.id,
            PullRequestFields.REPOSITORY_ID: 'repo',
        }]

    def test_forgotten_task_is_skipped(self) -> None:
        forgotten = types.SimpleNamespace(id='UNA-2536')
        kept = types.SimpleNamespace(id='UNA-OK')
        service = self._service([forgotten, kept])
        with patch(
            'kato_core_lib.data_layers.service.review_comment_service.forgotten_task_ids',
            return_value={'UNA-2536'},
        ), patch.object(
            service, '_review_task_pull_request_contexts', side_effect=self._ctx_for,
        ) as mock_ctx:
            contexts = service._review_pull_request_contexts()
        # The forgotten task's PR contexts were never even gathered.
        gathered_ids = [call.args[0].id for call in mock_ctx.call_args_list]
        self.assertEqual(gathered_ids, ['UNA-OK'])
        self.assertEqual(
            [c[PullRequestFields.ID] for c in contexts], ['UNA-OK'],
        )

    def test_no_forgotten_keeps_every_task(self) -> None:
        kept = types.SimpleNamespace(id='UNA-OK')
        service = self._service([kept])
        with patch(
            'kato_core_lib.data_layers.service.review_comment_service.forgotten_task_ids',
            return_value=set(),
        ), patch.object(
            service, '_review_task_pull_request_contexts', side_effect=self._ctx_for,
        ):
            contexts = service._review_pull_request_contexts()
        self.assertEqual(
            [c[PullRequestFields.ID] for c in contexts], ['UNA-OK'],
        )


if __name__ == '__main__':
    unittest.main()
