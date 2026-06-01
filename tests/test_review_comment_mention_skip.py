"""The PR review-comment scan must skip comments @-mentioning a teammate.

Regression: issue/ticket comments are @-mention-filtered (the shared
``mention_utils``), but PR review comments were not — so a comment like
``@jane can you take this?`` on kato's PR was picked up and acted on by kato
instead of skipped. ``_unprocessed_review_comments`` now applies the same
filter.

The bot's identity is its login on the code-review platform that hosts the repo
(its GitHub / GitLab / Bitbucket username), resolved per provider. That is the
identity a reviewer actually ``@mentions``. The task-platform ``assignee`` is
added only as a SECONDARY identity, and ONLY when the code-host login is known
— because matching against a different platform's login alone could silently
drop a comment genuinely directed at the bot. So when the code-host identity
can't be resolved the filter is disabled (keeps everything) rather than risk a
wrong drop.

Single-comment cases on purpose: a lone non-kato reviewer comment can only be
dropped by the mention filter (no thread-dedup partner, no position gate, not
yet processed), so a dropped/kept result is unambiguously the filter's doing.
"""
import unittest
from unittest.mock import MagicMock

from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from kato_core_lib.data_layers.service.review_comment_service import ReviewCommentService


def _comment(comment_id: str, body: str) -> ReviewComment:
    return ReviewComment(
        pull_request_id='17', comment_id=comment_id, author='reviewer', body=body,
    )


class ReviewCommentMentionSkipTests(unittest.TestCase):
    def _service(self, *, task_login='kato_yt', review_login='kato_bb'):
        task_service = MagicMock()
        task_service.bot_login = task_login
        repository_service = MagicMock()
        repository_service.review_comment_bot_login.return_value = review_login
        state_registry = MagicMock()
        state_registry.is_review_comment_processed.return_value = False
        return ReviewCommentService(
            task_service=task_service,
            implementation_service=MagicMock(),
            repository_service=repository_service,
            state_registry=state_registry,
        )

    def _kept_ids(self, service, comments):
        kept = service._unprocessed_review_comments(
            comments, repository_id='client', pull_request_id='17', comment_context=[],
        )
        return [c.comment_id for c in kept]

    def test_skips_comment_mentioning_other_human(self) -> None:
        # The actual reported bug.
        service = self._service()
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane.doe please look at this')]),
            [],
        )

    def test_keeps_plain_comment_with_no_mention(self) -> None:
        service = self._service()
        self.assertEqual(
            self._kept_ids(service, [_comment('1', 'this also needs a unit test')]),
            ['1'],
        )

    def test_keeps_comment_mentioning_bot_code_host_login(self) -> None:
        service = self._service()
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@kato_bb also handle X')]),
            ['1'],
        )

    def test_keeps_comment_mentioning_bot_task_login_as_secondary(self) -> None:
        # When the code-host login IS known, the task assignee is a secondary
        # identity, so a comment @-mentioning the bot under it is kept too.
        service = self._service()
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@kato_yt fix the typo')]),
            ['1'],
        )

    def test_keeps_comment_mentioning_bot_among_others(self) -> None:
        service = self._service()
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane and @kato_bb please')]),
            ['1'],
        )

    def test_unknown_code_host_identity_disables_filter(self) -> None:
        # The mixed-deployment safety gate: the bot's review-platform login
        # can't be resolved (e.g. a GitHub PR with no configured GitHub
        # username). Matching against the task assignee alone could drop a
        # comment @-mentioning the bot under its (different) code-host handle,
        # so the filter is disabled — keep everything rather than risk it.
        service = self._service(task_login='kato_yt', review_login='')
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane.doe please look')]),
            ['1'],
        )

    def test_no_identities_at_all_disables_filter(self) -> None:
        service = self._service(task_login='', review_login='')
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane.doe please look')]),
            ['1'],
        )

    def test_me_secondary_login_is_normalized_away(self) -> None:
        # Code-host login known; task assignee is the YouTrack 'me' alias which
        # is treated as "no login" — filter still works off the code-host login.
        service = self._service(task_login='me', review_login='kato_bb')
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane.doe please look')]),
            [],
        )
        self.assertEqual(
            self._kept_ids(service, [_comment('2', '@kato_bb please look')]),
            ['2'],
        )

    def test_identity_lookup_failure_does_not_break_selection(self) -> None:
        # A best-effort identity lookup that raises must not crash the scan;
        # with no resolvable code-host identity the filter safely disables.
        service = self._service(task_login='kato_yt')
        service._repository_service.review_comment_bot_login.side_effect = RuntimeError('boom')
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane.doe please look')]),
            ['1'],
        )

    def test_code_host_login_alone_when_no_task_assignee(self) -> None:
        # review_login known, task assignee unset → filter runs on the
        # code-host login alone (no secondary identity appended).
        service = self._service(task_login='', review_login='kato_bb')
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane please')]), [],
        )
        self.assertEqual(
            self._kept_ids(service, [_comment('2', '@kato_bb please')]), ['2'],
        )

    def test_task_login_lookup_failure_falls_back_to_code_host(self) -> None:
        # A task_service.bot_login property that raises must not crash; the
        # filter proceeds on the resolved code-host identity alone.
        class _RaisingBotLogin:
            @property
            def bot_login(self):
                raise RuntimeError('boom')

        repository_service = MagicMock()
        repository_service.review_comment_bot_login.return_value = 'kato_bb'
        state_registry = MagicMock()
        state_registry.is_review_comment_processed.return_value = False
        service = ReviewCommentService(
            task_service=_RaisingBotLogin(),
            implementation_service=MagicMock(),
            repository_service=repository_service,
            state_registry=state_registry,
        )
        self.assertEqual(
            self._kept_ids(service, [_comment('1', '@jane please')]), [],
        )
        self.assertEqual(
            self._kept_ids(service, [_comment('2', '@kato_bb please')]), ['2'],
        )


if __name__ == '__main__':
    unittest.main()
