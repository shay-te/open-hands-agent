"""Tests for batched review-comment processing.

Pin down four surfaces:

1. ``review_comments_batch_text`` renders the numbered prompt entries
   with file/line localization where present.
2. ``ClaudeCliClient._build_review_comments_batch_prompt`` produces a
   coherent batched prompt that names each comment, lists each
   localization, and asks the agent to address them in one
   change-set.
3. ``ReviewCommentService.process_review_comment_batch`` calls the
   agent's plural ``fix_review_comments`` once, pushes once, and
   replies/resolves/marks-processed per comment.
4. ``_dispatch_review_comments`` groups comments by
   ``(repository_id, pull_request_id)`` so each PR is one batch.

The whole feature exists to collapse N agent spawns into 1 per PR;
these tests verify the batching contract end to end without spinning
up Claude.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from kato_core_lib.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from kato_core_lib.helpers.agent_prompt_utils import review_comments_batch_text
from kato_core_lib.jobs.process_assigned_tasks import (
    _group_review_comments_by_pull_request,
)


def _build_comment(
    *, comment_id: str = '1', body: str = 'fix this',
    file_path: str = '', line_number: int | str = '',
    line_type: str = '', commit_sha: str = '',
    pull_request_id: str = '7', repository_id: str = 'client',
    author: str = 'reviewer',
) -> ReviewComment:
    comment = ReviewComment(
        pull_request_id=pull_request_id,
        comment_id=comment_id,
        author=author,
        body=body,
        file_path=file_path,
        line_number=line_number,
        line_type=line_type,
        commit_sha=commit_sha,
    )
    setattr(comment, PullRequestFields.REPOSITORY_ID, repository_id)
    return comment


class BatchTextRenderingTests(unittest.TestCase):
    """``review_comments_batch_text`` produces the prompt entry list."""

    def test_empty_batch_renders_empty_string(self) -> None:
        self.assertEqual(review_comments_batch_text([]), '')

    def test_single_comment_with_localization(self) -> None:
        text = review_comments_batch_text([
            _build_comment(
                comment_id='1', body='fix typo',
                file_path='src/auth.py', line_number=42, line_type='added',
            ),
        ])
        self.assertIn('1.', text)
        self.assertIn('File: src/auth.py:42 (added)', text)
        self.assertIn('Comment by reviewer: fix typo', text)

    def test_two_comments_are_numbered_separately(self) -> None:
        text = review_comments_batch_text([
            _build_comment(comment_id='1', body='first'),
            _build_comment(comment_id='2', body='second'),
        ])
        self.assertIn('1.', text)
        self.assertIn('2.', text)
        # Order preserved.
        self.assertLess(text.index('first'), text.index('second'))

    def test_comment_without_localization_says_pr_level(self) -> None:
        text = review_comments_batch_text([
            _build_comment(comment_id='1', body='general thoughts'),
        ])
        self.assertIn('PR-level comment', text)
        self.assertIn('general thoughts', text)


class BatchedPromptBuilderTests(unittest.TestCase):
    """``ClaudeCliClient._build_review_comments_batch_prompt`` shape."""

    def test_prompt_includes_branch_name_and_repo(self) -> None:
        comments = [
            _build_comment(
                comment_id='1', body='fix typo',
                file_path='src/auth.py', line_number=42, line_type='added',
                repository_id='client',
            ),
            _build_comment(
                comment_id='2', body='extract constant',
                file_path='src/cache.py', line_number=88, line_type='added',
                repository_id='client',
            ),
        ]
        prompt = ClaudeCliClient._build_review_comments_batch_prompt(
            comments, 'feature/proj-7',
        )
        self.assertIn('feature/proj-7', prompt)
        self.assertIn('repository client', prompt)

    def test_prompt_lists_each_comment_with_localization(self) -> None:
        comments = [
            _build_comment(
                comment_id='1', body='fix typo',
                file_path='src/auth.py', line_number=42, line_type='added',
            ),
            _build_comment(
                comment_id='2', body='extract constant',
                file_path='src/cache.py', line_number=88, line_type='added',
            ),
        ]
        prompt = ClaudeCliClient._build_review_comments_batch_prompt(
            comments, 'feature/proj-7',
        )
        self.assertIn('src/auth.py:42', prompt)
        self.assertIn('src/cache.py:88', prompt)
        # Each body appears wrapped (untrusted-content marker
        # contains the body text inside the wrap).
        self.assertIn('fix typo', prompt)
        self.assertIn('extract constant', prompt)

    def test_prompt_asks_for_single_change_set(self) -> None:
        prompt = ClaudeCliClient._build_review_comments_batch_prompt(
            [
                _build_comment(comment_id='1', body='a'),
                _build_comment(comment_id='2', body='b'),
            ],
            'feature/proj-7',
        )
        self.assertIn('single coherent change-set', prompt)


class GroupingTests(unittest.TestCase):
    """Job dispatcher's ``(repo, pr)`` grouping."""

    def test_single_pr_produces_single_batch(self) -> None:
        comments = [
            _build_comment(comment_id='1', repository_id='client', pull_request_id='7'),
            _build_comment(comment_id='2', repository_id='client', pull_request_id='7'),
        ]
        groups = _group_review_comments_by_pull_request(comments)
        self.assertEqual(len(groups), 1)
        self.assertEqual([c.comment_id for c in groups[0]], ['1', '2'])

    def test_two_prs_produce_two_batches(self) -> None:
        comments = [
            _build_comment(comment_id='1', repository_id='client', pull_request_id='7'),
            _build_comment(comment_id='2', repository_id='client', pull_request_id='9'),
            _build_comment(comment_id='3', repository_id='client', pull_request_id='7'),
        ]
        groups = _group_review_comments_by_pull_request(comments)
        self.assertEqual(len(groups), 2)
        # First-occurrence ordering: PR 7 appears first.
        self.assertEqual([c.comment_id for c in groups[0]], ['1', '3'])
        self.assertEqual([c.comment_id for c in groups[1]], ['2'])

    def test_two_repositories_with_same_pr_id_stay_separate(self) -> None:
        # Same numeric PR id across two repos → two batches.
        comments = [
            _build_comment(comment_id='1', repository_id='client', pull_request_id='7'),
            _build_comment(comment_id='2', repository_id='backend', pull_request_id='7'),
        ]
        groups = _group_review_comments_by_pull_request(comments)
        self.assertEqual(len(groups), 2)


class ServiceBatchFlowTests(unittest.TestCase):
    """``ReviewCommentService.process_review_comment_batch`` end-to-end."""

    def _build_service(
        self, *, fix_success: bool = True,
    ) -> tuple[object, MagicMock, MagicMock]:
        from kato_core_lib.data_layers.service.review_comment_service import (
            ReviewCommentService,
        )

        # Fake repository service: enough surface for the batch
        # method to do its work (workspace clone / branch prepare /
        # publish / reply / resolve).
        repo = SimpleNamespace(
            id='client',
            local_path='/tmp/client',
            display_name='Client',
            branch_name='feature/proj-7',
        )
        repository_service = MagicMock()
        repository_service.get_repository.return_value = repo
        repository_service.prepare_task_branches.return_value = [repo]
        repository_service.publish_review_fix.return_value = None
        repository_service.reply_to_review_comment.return_value = None
        repository_service.resolve_review_comment.return_value = None
        repository_service.list_pull_request_comments.return_value = []
        repository_service.find_review_pull_requests.return_value = []
        repository_service.find_existing_pull_request.return_value = None

        implementation_service = MagicMock()
        implementation_service.fix_review_comments.return_value = {
            ImplementationFields.SUCCESS: fix_success,
            ImplementationFields.AGENT_SESSION_ID: 'sess-1',
        }
        implementation_service.fix_review_comment.return_value = {
            ImplementationFields.SUCCESS: fix_success,
            ImplementationFields.AGENT_SESSION_ID: 'sess-1',
        }

        task_service = MagicMock()
        # Provide a fake task whose pull_requests array maps PR 7 to
        # repo 'client' so ``_review_fix_context`` resolves cleanly.
        task = SimpleNamespace(
            id='PROJ-1',
            summary='do the thing',
            pull_requests=[{
                PullRequestFields.ID: '7',
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.SOURCE_BRANCH: 'feature/proj-7',
                PullRequestFields.TITLE: 'PROJ-1: do',
            }],
            branch_name='feature/proj-7',
        )
        task_service.get_review_tasks.return_value = [task]
        task_service.find_task_by_pull_request.return_value = task

        state_registry = MagicMock()
        state_registry.is_review_comment_processed.return_value = False

        service = ReviewCommentService(
            task_service=task_service,
            implementation_service=implementation_service,
            repository_service=repository_service,
            state_registry=state_registry,
        )
        return service, implementation_service, repository_service

    def test_batch_calls_fix_review_comments_once(self) -> None:
        service, impl, _repo = self._build_service()
        comments = [
            _build_comment(comment_id='1', body='a'),
            _build_comment(comment_id='2', body='b'),
        ]
        service.process_review_comment_batch(comments)
        self.assertEqual(impl.fix_review_comments.call_count, 1)
        self.assertEqual(impl.fix_review_comment.call_count, 0)
        # First positional arg of fix_review_comments is the comments list.
        called_comments = impl.fix_review_comments.call_args.args[0]
        self.assertEqual([c.comment_id for c in called_comments], ['1', '2'])

    def test_batch_pushes_once_replies_and_resolves_each_comment(self) -> None:
        service, _impl, repo = self._build_service()
        comments = [
            _build_comment(comment_id='1', body='a'),
            _build_comment(comment_id='2', body='b'),
            _build_comment(comment_id='3', body='c'),
        ]
        service.process_review_comment_batch(comments)
        # One push for the whole batch.
        self.assertEqual(repo.publish_review_fix.call_count, 1)
        # One reply + one resolve per comment.
        self.assertEqual(repo.reply_to_review_comment.call_count, 3)
        self.assertEqual(repo.resolve_review_comment.call_count, 3)

    def test_batch_returns_one_result_per_comment(self) -> None:
        service, _impl, _repo = self._build_service()
        comments = [
            _build_comment(comment_id='1', body='a'),
            _build_comment(comment_id='2', body='b'),
        ]
        results = service.process_review_comment_batch(comments)
        self.assertEqual(len(results), 2)
        # Each result is shaped by ``review_fix_result(comment, ctx)``;
        # both entries share the PR id since the batch is one PR.
        for result in results:
            self.assertEqual(result[ReviewCommentFields.PULL_REQUEST_ID], '7')

    def test_batch_failure_does_not_publish(self) -> None:
        service, _impl, repo = self._build_service(fix_success=False)
        comments = [
            _build_comment(comment_id='1', body='a'),
            _build_comment(comment_id='2', body='b'),
        ]
        with self.assertRaises(RuntimeError):
            service.process_review_comment_batch(comments)
        # No push, no reply, no resolve when the agent fix fails.
        self.assertEqual(repo.publish_review_fix.call_count, 0)
        self.assertEqual(repo.reply_to_review_comment.call_count, 0)
        self.assertEqual(repo.resolve_review_comment.call_count, 0)

    def test_batch_with_mismatched_pr_raises_value_error(self) -> None:
        service, _impl, _repo = self._build_service()
        comments = [
            _build_comment(comment_id='1', pull_request_id='7'),
            _build_comment(comment_id='2', pull_request_id='9'),
        ]
        with self.assertRaisesRegex(ValueError, 'same .repository_id, pull_request_id.'):
            service.process_review_comment_batch(comments)


class ImplementationServiceFanoutTests(unittest.TestCase):
    """Old client without ``fix_review_comments`` falls back to per-comment."""

    def test_old_client_fans_out_via_singular_method(self) -> None:
        from kato_core_lib.data_layers.service.implementation_service import (
            ImplementationService,
        )

        # Old-style client: only exposes the singular method.
        client = SimpleNamespace(
            fix_review_comment=MagicMock(return_value={
                ImplementationFields.SUCCESS: True,
                ImplementationFields.AGENT_SESSION_ID: 's',
            }),
        )
        service = ImplementationService(client)
        result = service.fix_review_comments(
            [_build_comment(comment_id='1'), _build_comment(comment_id='2')],
            'feature/proj-7',
        )
        self.assertTrue(result[ImplementationFields.SUCCESS])
        # Two singular calls — old behavior preserved (no batching
        # efficiency, but correctness intact).
        self.assertEqual(client.fix_review_comment.call_count, 2)


if __name__ == '__main__':
    unittest.main()
