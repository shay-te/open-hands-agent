"""Tests for question-vs-fix routing in review-comment processing.

Three surfaces:

1. ``is_question_comment`` heuristic: conservative — only fires for
   wording that's unambiguously a question. Anything that looks
   imperative ("should be a constant", "fix this") stays on the
   fix flow even with a trailing ``?``.
2. ``ClaudeCliClient._build_review_comments_batch_prompt`` produces
   different prompts for ``mode='fix'`` (legacy) vs ``mode='answer'``
   (new). The answer prompt forbids edits / commits / pushes.
3. ``ReviewCommentService.process_review_comment_batch`` routes
   pure-question batches through ``_publish_review_comment_answers``
   (no push, agent's text becomes the reply body), and routes
   anything else through the existing fix flow.
"""

from __future__ import annotations

import types
import unittest
from unittest.mock import MagicMock

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from kato_core_lib.data_layers.data.fields import (
    ImplementationFields,
    PullRequestFields,
    ReviewCommentFields,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from kato_core_lib.helpers.review_comment_utils import (
    is_question_comment,
    is_question_only_batch,
    review_comment_answer_body,
)


def _comment(*, body: str, comment_id: str = '1') -> ReviewComment:
    return ReviewComment(
        pull_request_id='17',
        comment_id=comment_id,
        author='reviewer',
        body=body,
    )


class HeuristicTests(unittest.TestCase):
    """Conservative — defaults to fix on anything ambiguous."""

    def test_clear_how_question_classifies_as_question(self) -> None:
        body = (
            'when we generate a link and share it on whatsapp chat or any '
            'other social media chat app will not execute JS on the website '
            'and we have this entire logic behind JS, how will it work?'
        )
        self.assertTrue(is_question_comment(_comment(body=body)))

    def test_simple_what_question(self) -> None:
        self.assertTrue(is_question_comment(_comment(body='what does this do?')))

    def test_could_you_explain(self) -> None:
        self.assertTrue(
            is_question_comment(_comment(body='could you explain why this works?')),
        )

    def test_no_question_mark_is_not_a_question(self) -> None:
        self.assertFalse(is_question_comment(_comment(body='how does this work')))

    def test_imperative_should_be_disqualifies(self) -> None:
        # "Should this be a constant?" reads as a fix request.
        self.assertFalse(
            is_question_comment(_comment(body='should this be a constant?')),
        )

    def test_imperative_fix_disqualifies(self) -> None:
        self.assertFalse(
            is_question_comment(_comment(body='can you fix this typo?')),
        )

    def test_long_comments_default_to_fix(self) -> None:
        # Long comments usually bury a fix request inside the explanation.
        long_question = 'how ' + ('. '.join(['x' * 30] * 20)) + '?'
        self.assertFalse(is_question_comment(_comment(body=long_question)))

    def test_empty_body_not_a_question(self) -> None:
        self.assertFalse(is_question_comment(_comment(body='')))

    def test_question_only_batch_all_questions(self) -> None:
        self.assertTrue(is_question_only_batch([
            _comment(body='how will this work?'),
            _comment(body='why is this an array?'),
        ]))

    def test_question_only_batch_mixed_returns_false(self) -> None:
        self.assertFalse(is_question_only_batch([
            _comment(body='how will this work?'),
            _comment(body='please rename this variable.'),
        ]))

    def test_empty_batch_is_not_question_only(self) -> None:
        # Defensive: empty list shouldn't trigger answer flow.
        self.assertFalse(is_question_only_batch([]))


class AnswerModePromptTests(unittest.TestCase):
    """Mode='answer' produces the no-edit prompt; mode='fix' is unchanged."""

    def test_batch_prompt_in_answer_mode_says_questions_no_edit(self) -> None:
        comments = [
            _comment(body='how will this work?', comment_id='1'),
            _comment(body='why is this needed?', comment_id='2'),
        ]
        prompt = ClaudeCliClient._build_review_comments_batch_prompt(
            comments, 'feature/proj-7', mode='answer',
        )
        self.assertIn('QUESTIONS', prompt)
        self.assertIn('Do NOT modify any files', prompt)
        self.assertIn('Do not commit', prompt)
        self.assertIn('Do not push', prompt)

    def test_batch_prompt_in_fix_mode_unchanged(self) -> None:
        comments = [
            _comment(body='please rename this', comment_id='1'),
            _comment(body='extract this constant', comment_id='2'),
        ]
        prompt = ClaudeCliClient._build_review_comments_batch_prompt(
            comments, 'feature/proj-7', mode='fix',
        )
        self.assertIn('Address every comment', prompt)
        self.assertNotIn('QUESTIONS', prompt)
        self.assertNotIn('Do NOT modify any files', prompt)

    def test_singular_prompt_in_answer_mode(self) -> None:
        comment = _comment(body='how will this work?')
        prompt = ClaudeCliClient._build_review_prompt(
            comment, 'feature/proj-7', mode='answer',
        )
        self.assertIn('QUESTION', prompt)
        self.assertIn('Do NOT modify any files', prompt)


class AnswerBodyTests(unittest.TestCase):
    """``review_comment_answer_body`` extracts agent text, not template."""

    def test_returns_agent_message_when_present(self) -> None:
        body = review_comment_answer_body({
            'message': 'When the link is shared, the receiver hits the '
                       'rendered HTML which loads the JS bundle.',
        })
        self.assertIn('When the link is shared', body)
        # Crucially, NOT the "Kato addressed / pushed" template.
        self.assertNotIn('Kato addressed', body)
        self.assertNotIn('pushed a follow-up update', body)

    def test_falls_back_to_summary_when_no_message(self) -> None:
        body = review_comment_answer_body({
            'summary': 'Short answer text.',
        })
        # Body is prefixed with the no-push disclaimer; agent text follows.
        self.assertIn('No code was changed', body)
        self.assertIn('Short answer text.', body)

    def test_friendly_fallback_when_agent_produced_nothing(self) -> None:
        body = review_comment_answer_body({})
        self.assertIn('did not produce an answer', body)


class ServiceRoutingTests(unittest.TestCase):
    """Pure-question batches go through the answer-only publish path."""

    def _build_service(self, *, agent_message: str = 'agent answer text'):
        from kato_core_lib.data_layers.service.review_comment_service import (
            ReviewCommentService,
        )
        from kato_core_lib.helpers.review_comment_utils import ReviewFixContext

        repo = types.SimpleNamespace(
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
        # Implementation service returns the agent's text in ``message``;
        # answer-mode publish reads that as the reply body.
        implementation_service.fix_review_comments.return_value = {
            ImplementationFields.SUCCESS: True,
            ImplementationFields.AGENT_SESSION_ID: 'sess-1',
            'message': agent_message,
        }

        task = types.SimpleNamespace(
            id='PROJ-1',
            summary='do the thing',
            pull_requests=[{
                PullRequestFields.ID: '17',
                PullRequestFields.REPOSITORY_ID: 'client',
                PullRequestFields.SOURCE_BRANCH: 'feature/proj-7',
                PullRequestFields.TITLE: 'PROJ-1: do',
            }],
            branch_name='feature/proj-7',
        )
        task_service = MagicMock()
        task_service.get_review_tasks.return_value = [task]
        task_service.find_task_by_pull_request.return_value = task

        # Mock registry: the routing logic only needs ``pull_request_context``
        # to return non-None; the real lookup machinery is tested
        # elsewhere.
        state_registry = MagicMock()
        state_registry.is_review_comment_processed.return_value = False
        state_registry.pull_request_context.return_value = {
            'task_id': 'PROJ-1',
            'task_summary': 'do the thing',
            'session_id': 'sess-1',
            'branch_name': 'feature/proj-7',
            'pull_request_title': 'PROJ-1: do',
            'repository_id': 'client',
        }
        service = ReviewCommentService(
            task_service=task_service,
            implementation_service=implementation_service,
            repository_service=repository_service,
            state_registry=state_registry,
        )
        return service, implementation_service, repository_service

    def _question_comment(self, comment_id: str) -> ReviewComment:
        c = ReviewComment(
            pull_request_id='17',
            comment_id=comment_id,
            author='reviewer',
            body='how will this work?',
        )
        return c

    def _fix_comment(self, comment_id: str) -> ReviewComment:
        c = ReviewComment(
            pull_request_id='17',
            comment_id=comment_id,
            author='reviewer',
            body='please rename this variable.',
        )
        return c

    def test_question_only_batch_calls_agent_in_answer_mode(self) -> None:
        service, impl, _repo = self._build_service()
        service.process_review_comment_batch([
            self._question_comment('1'),
            self._question_comment('2'),
        ])
        # mode='answer' threaded through to the agent client.
        kwargs = impl.fix_review_comments.call_args.kwargs
        self.assertEqual(kwargs.get('mode'), 'answer')

    def test_question_only_batch_skips_publish_review_fix(self) -> None:
        service, _impl, repo = self._build_service()
        service.process_review_comment_batch([
            self._question_comment('1'),
        ])
        # No push for question batches.
        self.assertEqual(repo.publish_review_fix.call_count, 0)
        # But still reply per question.
        self.assertEqual(repo.reply_to_review_comment.call_count, 1)

    def test_question_only_reply_body_is_agent_answer_text(self) -> None:
        service, _impl, repo = self._build_service(
            agent_message='Because the link triggers JS at render time.',
        )
        service.process_review_comment_batch([
            self._question_comment('1'),
        ])
        body = repo.reply_to_review_comment.call_args.args[2]
        # Body must carry the no-push disclaimer so the reviewer can't
        # mistake an answer for a code fix, and must include the agent text.
        self.assertIn('No code was changed', body)
        self.assertIn('Because the link triggers JS at render time.', body)
        # NOT the "Kato addressed / pushed" fix template.
        self.assertNotIn('pushed a follow-up update', body)
        # Thread must NOT be resolved — the human verifies the answer.
        repo.resolve_review_comment.assert_not_called()

    def test_fix_batch_still_pushes(self) -> None:
        service, impl, repo = self._build_service()
        service.process_review_comment_batch([self._fix_comment('1')])
        # mode='fix' (default) — push happens.
        kwargs = impl.fix_review_comments.call_args.kwargs
        self.assertEqual(kwargs.get('mode'), 'fix')
        self.assertEqual(repo.publish_review_fix.call_count, 1)

    def test_mixed_batch_treated_as_fix(self) -> None:
        service, impl, repo = self._build_service()
        service.process_review_comment_batch([
            self._question_comment('1'),
            self._fix_comment('2'),
        ])
        # Mixed batch falls through to fix-mode (conservative default).
        kwargs = impl.fix_review_comments.call_args.kwargs
        self.assertEqual(kwargs.get('mode'), 'fix')
        self.assertEqual(repo.publish_review_fix.call_count, 1)


if __name__ == '__main__':
    unittest.main()
