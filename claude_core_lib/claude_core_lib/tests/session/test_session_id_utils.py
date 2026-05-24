"""Tests for ``fix_session_id`` — the single normalizer every site uses.

Pin the exact contract so a refactor or accidental ``str(x or '').strip()``
re-introduction would fail loudly. Each test names the bug pattern the
input represents.
"""
from __future__ import annotations

import unittest

from claude_core_lib.claude_core_lib.session.session_id_utils import (
    fix_session_id,
)


class FixSessionIdTests(unittest.TestCase):

    # ----- the empty / missing cases ---------------------------------

    def test_none_becomes_empty_string(self) -> None:
        # ``getattr(record, 'claude_session_id', None)`` returns None
        # if the attribute is missing. Must coerce to '' so the
        # caller's ``if session_id:`` truthy guards keep working.
        self.assertEqual(fix_session_id(None), '')

    def test_empty_string_stays_empty(self) -> None:
        self.assertEqual(fix_session_id(''), '')

    def test_whitespace_only_becomes_empty(self) -> None:
        # The bug the helper was introduced to fix:
        # ``claude --resume '   '`` blew up with "No conversation
        # found". Whitespace-only must be treated as "no id".
        self.assertEqual(fix_session_id('   '), '')
        self.assertEqual(fix_session_id('\t\n'), '')
        self.assertEqual(fix_session_id(' \t\n '), '')

    # ----- the clean happy path --------------------------------------

    def test_already_clean_string_returned_unchanged(self) -> None:
        self.assertEqual(
            fix_session_id('abc-123-deadbeef'),
            'abc-123-deadbeef',
        )

    def test_strips_leading_and_trailing_whitespace(self) -> None:
        self.assertEqual(fix_session_id('  abc  '), 'abc')
        self.assertEqual(fix_session_id('\tabc\n'), 'abc')

    def test_preserves_internal_whitespace(self) -> None:
        # Session ids should never have spaces inside them in real
        # usage, but the helper is for normalization not validation —
        # don't touch anything in the middle.
        self.assertEqual(fix_session_id('a b'), 'a b')

    # ----- type coercion ---------------------------------------------

    def test_non_string_types_are_coerced_via_str(self) -> None:
        # Defensive: a legacy record format / test fixture / int id
        # shouldn't crash deep in the spawn path. Coerce and strip.
        self.assertEqual(fix_session_id(42), '42')
        self.assertEqual(fix_session_id(0), '0')

    def test_bytes_are_coerced(self) -> None:
        # ``str(b'...')`` adds the ``b''`` prefix — undesirable but
        # consistent with how Python coerces bytes. Pin the behavior
        # so a future change is intentional.
        self.assertEqual(fix_session_id(b'abc'), "b'abc'")

    # ----- the duck-typed attribute patterns -------------------------

    def test_works_with_getattr_pattern(self) -> None:
        # The most common call shape across the codebase.
        record = type('R', (), {'claude_session_id': '  abc  '})()
        self.assertEqual(
            fix_session_id(getattr(record, 'claude_session_id', '')),
            'abc',
        )

    def test_works_with_missing_attribute_pattern(self) -> None:
        record = type('R', (), {})()
        self.assertEqual(
            fix_session_id(getattr(record, 'claude_session_id', None)),
            '',
        )

    def test_works_with_dict_get_pattern(self) -> None:
        payload = {'claude_session_id': '  abc  '}
        self.assertEqual(fix_session_id(payload.get('claude_session_id')), 'abc')
        self.assertEqual(fix_session_id(payload.get('missing')), '')


if __name__ == '__main__':
    unittest.main()
