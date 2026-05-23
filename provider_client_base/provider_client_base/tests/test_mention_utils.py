"""Tests for the shared @-mention comment filter.

Pin down the rule used by every ticket platform's
``_task_comment_entries`` so kato stops acting on comments addressed
to humans other than its own bot user. Every stand-in here is a
plain string / value — no Mocks, no magic.
"""
from __future__ import annotations

import unittest

from provider_client_base.provider_client_base.helpers.mention_utils import (
    extract_mention_logins,
    is_comment_addressed_elsewhere,
)


class ExtractMentionLoginsTests(unittest.TestCase):

    def test_empty_body_returns_empty(self) -> None:
        self.assertEqual(extract_mention_logins(''), [])
        self.assertEqual(extract_mention_logins(None), [])
        self.assertEqual(extract_mention_logins(0), [])

    def test_finds_single_mention_lowercased(self) -> None:
        self.assertEqual(
            extract_mention_logins('hey @Jane.Doe can you check this'),
            ['jane.doe'],
        )

    def test_finds_multiple_mentions_preserving_order(self) -> None:
        self.assertEqual(
            extract_mention_logins('@kato_bot please ping @alice and @bob-jr'),
            ['kato_bot', 'alice', 'bob-jr'],
        )

    def test_email_addresses_do_not_count_as_mentions(self) -> None:
        # ``foo@example.com`` must NOT register as ``@example`` — the
        # lookbehind on ``[\w.]`` blocks email-like contexts.
        self.assertEqual(
            extract_mention_logins('email me at foo@example.com'),
            [],
        )

    def test_mentions_adjacent_to_punctuation(self) -> None:
        # Comma / period / colon directly after the login are fine.
        self.assertEqual(
            extract_mention_logins('@alice, @bob: please look. @carol.'),
            ['alice', 'bob', 'carol'],
        )

    def test_bare_at_sign_is_not_a_mention(self) -> None:
        self.assertEqual(extract_mention_logins('cost is $5 @ each'), [])
        self.assertEqual(extract_mention_logins('@'), [])

    def test_non_string_body_is_coerced_to_string(self) -> None:
        # Defensive — extract_body callbacks may return non-strings.
        self.assertEqual(
            extract_mention_logins(['@alice']),  # type: ignore[arg-type]
            ['alice'],
        )

    def test_underscore_dot_hyphen_in_login(self) -> None:
        self.assertEqual(
            extract_mention_logins('@my_user.name-v2 hello'),
            ['my_user.name-v2'],
        )


class IsCommentAddressedElsewhereTests(unittest.TestCase):

    # ---- filter disabled paths ----

    def test_empty_bot_login_disables_filter(self) -> None:
        self.assertFalse(is_comment_addressed_elsewhere('@alice please', ''))
        self.assertFalse(is_comment_addressed_elsewhere('@alice please', None))

    def test_me_alias_disables_filter(self) -> None:
        # YouTrack's ``"me"`` is a query alias, not a real login — it
        # could never literally appear in a ``@mention``. Treat as
        # "filter disabled" rather than silently keeping nothing.
        self.assertFalse(is_comment_addressed_elsewhere('@alice please', 'me'))
        self.assertFalse(is_comment_addressed_elsewhere('@alice please', 'ME'))
        self.assertFalse(is_comment_addressed_elsewhere('@alice please', '  me  '))

    # ---- the actual rule ----

    def test_no_mentions_in_body_is_kept(self) -> None:
        # General project note → kato should still see it.
        self.assertFalse(
            is_comment_addressed_elsewhere('this also needs a unit test', 'kato_bot'),
        )

    def test_mention_matches_bot_is_kept(self) -> None:
        self.assertFalse(
            is_comment_addressed_elsewhere('@kato_bot fix the typo', 'kato_bot'),
        )

    def test_mention_to_someone_else_is_skipped(self) -> None:
        # The actual reported bug.
        self.assertTrue(
            is_comment_addressed_elsewhere('@jane.doe please look at this', 'kato_bot'),
        )

    def test_bot_among_others_is_kept(self) -> None:
        # If the operator addressed kato AND someone else, the
        # comment is still meant for kato → keep it.
        self.assertTrue(
            # only @alice — not kato.
            is_comment_addressed_elsewhere('@alice and @bob', 'kato_bot'),
        )
        self.assertFalse(
            # kato is one of the addressees.
            is_comment_addressed_elsewhere('@alice and @kato_bot', 'kato_bot'),
        )

    def test_case_insensitive_match(self) -> None:
        self.assertFalse(
            is_comment_addressed_elsewhere('@Kato_Bot fix it', 'kato_bot'),
        )
        self.assertFalse(
            is_comment_addressed_elsewhere('@kato_bot fix it', 'KATO_BOT'),
        )

    def test_email_addresses_do_not_trigger_skip(self) -> None:
        # ``foo@example.com`` must not register as a mention of
        # ``example``; otherwise plain operator notes that include an
        # email would be silently dropped.
        self.assertFalse(
            is_comment_addressed_elsewhere(
                'forward this to ops@example.com please',
                'kato_bot',
            ),
        )

    def test_non_string_body_is_handled(self) -> None:
        # extract_body callbacks may return non-strings (Jira ADF,
        # numbers, lists). Filter must not crash.
        self.assertFalse(is_comment_addressed_elsewhere(42, 'kato_bot'))
        self.assertFalse(is_comment_addressed_elsewhere(None, 'kato_bot'))

    def test_bot_login_stripped_of_whitespace(self) -> None:
        self.assertTrue(
            is_comment_addressed_elsewhere('@jane please', '  kato_bot  '),
        )


if __name__ == '__main__':
    unittest.main()
