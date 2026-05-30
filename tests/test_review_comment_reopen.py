"""Tests for re-opened review-comment behavior.

Pin down the position-based "already handled" rule in
``ReviewCommentService._unprocessed_review_comments``:

- Reviewer comment that pre-dates kato's last reply on the same
  thread is treated as addressed and skipped on subsequent scans.
- Reviewer comment that comes **after** kato's last reply (a
  follow-up or a freshly-added comment in a re-opened thread) gets
  picked up so kato re-engages.
- Kato's own reply messages never appear in the returned list —
  kato shouldn't process its own narration.

Together these implement the behaviour the operator asked for:
"when the comment is reopened Claude should check it again."
"""

from __future__ import annotations

import types
import unittest
from unittest.mock import Mock

from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from kato_core_lib.data_layers.data.fields import (
    PullRequestFields,
    ReviewCommentFields,
)
from kato_core_lib.data_layers.service.agent_state_registry import AgentStateRegistry
from kato_core_lib.data_layers.service.review_comment_service import (
    ReviewCommentService,
)
from kato_core_lib.helpers.review_comment_utils import (
    KATO_REVIEW_COMMENT_FIXED_PREFIX,
)


def _comment(
    *, comment_id: str, body: str = 'real reviewer comment',
    author: str = 'reviewer', resolution_target_id: str = '',
) -> ReviewComment:
    c = ReviewComment(
        pull_request_id='17',
        comment_id=comment_id,
        author=author,
        body=body,
    )
    if resolution_target_id:
        setattr(c, ReviewCommentFields.RESOLUTION_TARGET_ID, resolution_target_id)
    return c


def _kato_reply(*, comment_id: str, resolution_target_id: str = '') -> ReviewComment:
    # Kato detection is body-prefix based (matches the production
    # ``is_kato_review_comment_reply`` rule).
    return _comment(
        comment_id=comment_id,
        body=f'{KATO_REVIEW_COMMENT_FIXED_PREFIX}5 on pull request 17.',
        author='kato',
        resolution_target_id=resolution_target_id,
    )


class ReopenedThreadTests(unittest.TestCase):
    """Position-based gate in ``_unprocessed_review_comments``."""

    def setUp(self) -> None:
        self.service = ReviewCommentService(
            task_service=types.SimpleNamespace(),
            implementation_service=types.SimpleNamespace(),
            repository_service=types.SimpleNamespace(),
            state_registry=AgentStateRegistry(),
        )

    def _run(self, comments):
        return self.service._unprocessed_review_comments(
            comments,
            repository_id='client',
            pull_request_id='17',
            comment_context=[],
        )

    def test_thread_with_kato_reply_then_reviewer_followup_returns_followup(self) -> None:
        # Reviewer asked, kato addressed, reviewer is back saying
        # "still broken." The follow-up must be picked up.
        comments = [
            _comment(comment_id='100', resolution_target_id='100', body='fix this'),
            _kato_reply(comment_id='101', resolution_target_id='100'),
            _comment(comment_id='102', resolution_target_id='100', body='still not fixed'),
        ]
        result = self._run(comments)
        ids = [c.comment_id for c in result]
        self.assertEqual(ids, ['102'])

    def test_thread_addressed_with_no_followup_returns_nothing(self) -> None:
        # Original comment pre-dates kato's reply — already addressed.
        comments = [
            _comment(comment_id='100', resolution_target_id='100', body='fix this'),
            _kato_reply(comment_id='101', resolution_target_id='100'),
        ]
        result = self._run(comments)
        self.assertEqual(result, [])

    def test_thread_with_no_kato_reply_returns_the_reviewer_comment(self) -> None:
        # Fresh comment, no kato activity yet.
        comments = [
            _comment(comment_id='100', resolution_target_id='100', body='fix this'),
        ]
        result = self._run(comments)
        ids = [c.comment_id for c in result]
        self.assertEqual(ids, ['100'])

    def test_kato_reply_is_never_returned(self) -> None:
        # Even if there's no following reviewer comment, kato's
        # reply itself must not be processed.
        comments = [_kato_reply(comment_id='100', resolution_target_id='200')]
        result = self._run(comments)
        self.assertEqual(result, [])

    def test_two_independent_threads_are_processed_independently(self) -> None:
        # Thread A is fully addressed; Thread B has a reviewer
        # follow-up after kato's reply. Only B comes back.
        comments = [
            _comment(comment_id='10', resolution_target_id='10', body='thread A'),
            _kato_reply(comment_id='11', resolution_target_id='10'),
            _comment(comment_id='20', resolution_target_id='20', body='thread B'),
            _kato_reply(comment_id='21', resolution_target_id='20'),
            _comment(comment_id='22', resolution_target_id='20', body='thread B follow-up'),
        ]
        result = self._run(comments)
        ids = [c.comment_id for c in result]
        self.assertEqual(ids, ['22'])

    def test_multiple_followups_after_kato_reply_dedupe_to_latest(self) -> None:
        # Two reviewer comments came in after kato's reply on the
        # same thread. We only need to process one (the most
        # recent) — the prompt builder will see the whole thread
        # via the comment context anyway.
        comments = [
            _comment(comment_id='100', resolution_target_id='100', body='original'),
            _kato_reply(comment_id='101', resolution_target_id='100'),
            _comment(comment_id='102', resolution_target_id='100', body='first followup'),
            _comment(comment_id='103', resolution_target_id='100', body='second followup'),
        ]
        result = self._run(comments)
        ids = [c.comment_id for c in result]
        self.assertEqual(ids, ['103'])

    def test_already_processed_followup_is_skipped(self) -> None:
        # State registry remembers we already handled comment 102 →
        # don't process it again on a subsequent scan tick.
        self.service.state_registry.mark_review_comment_processed(
            'client', '17', '102',
        )
        comments = [
            _comment(comment_id='100', resolution_target_id='100', body='original'),
            _kato_reply(comment_id='101', resolution_target_id='100'),
            _comment(comment_id='102', resolution_target_id='100', body='followup'),
        ]
        result = self._run(comments)
        self.assertEqual(result, [])

    def test_followup_returned_even_when_thread_key_was_marked(self) -> None:
        # Regression for the operator-reported bug: the OLD code marked
        # the THREAD/resolution key ('comment:100') when it handled the
        # original comment, which then silently swallowed every later
        # reply in that thread — the operator answering kato's question
        # never reached the agent. A thread-level mark must NOT block a
        # new follow-up; only the follow-up's own id can.
        self.service.state_registry.mark_review_comment_processed(
            'client', '17', 'comment:100',
        )
        comments = [
            _comment(comment_id='100', resolution_target_id='100', body='fix this'),
            _kato_reply(comment_id='101', resolution_target_id='100'),
            _comment(comment_id='102', resolution_target_id='100', body='still broken'),
        ]
        result = self._run(comments)
        self.assertEqual([c.comment_id for c in result], ['102'])

    def test_repository_id_and_context_set_on_returned_comments(self) -> None:
        # The gate also annotates each returned comment with the
        # repository id + thread context so the downstream prompt
        # builder has what it needs.
        comments = [
            _comment(comment_id='100', resolution_target_id='100', body='fresh'),
        ]
        result = self.service._unprocessed_review_comments(
            comments,
            repository_id='backend',
            pull_request_id='17',
            comment_context=[{'comment_id': '100', 'author': 'reviewer', 'body': 'fresh'}],
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(
            getattr(result[0], PullRequestFields.REPOSITORY_ID),
            'backend',
        )
        all_comments = getattr(result[0], ReviewCommentFields.ALL_COMMENTS, [])
        self.assertEqual(len(all_comments), 1)


if __name__ == '__main__':
    unittest.main()
