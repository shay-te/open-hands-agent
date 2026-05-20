"""Unit tests for the settings-value validation helpers I added to
``kato_settings_schema_utils``: ``validate_settings_values`` plus
its three small checkers (``_check_type`` / ``_check_url`` /
``_check_email``).

The webserver routes (``POST /api/settings``, ``POST /api/task-providers``,
etc.) call ``validate_settings_values`` before persisting any update,
so a regression here lets bad values land in ``~/.kato/settings.json``.
"""

from __future__ import annotations

import unittest

from kato_core_lib.helpers.kato_settings_schema_utils import (
    _check_email,
    _check_type,
    _check_url,
    validate_settings_values,
)


class CheckTypeTests(unittest.TestCase):
    def test_select_rejects_value_not_in_options(self) -> None:
        err = _check_type('FIELD', 'bogus', {'type': 'select', 'options': ['a', 'b']})
        self.assertIsNotNone(err)
        self.assertIn('must be one of', err)
        self.assertIn('bogus', err)

    def test_select_accepts_value_in_options(self) -> None:
        self.assertIsNone(_check_type(
            'F', 'a', {'type': 'select', 'options': ['a', 'b']},
        ))

    def test_select_with_no_options_accepts_anything(self) -> None:
        self.assertIsNone(_check_type('F', 'whatever', {'type': 'select'}))

    def test_number_rejects_non_numeric(self) -> None:
        err = _check_type('N', 'abc', {'type': 'number'})
        self.assertIsNotNone(err)
        self.assertIn('non-negative number', err)

    def test_number_rejects_negative(self) -> None:
        err = _check_type('N', '-5', {'type': 'number'})
        self.assertIsNotNone(err)

    def test_number_rejects_infinity(self) -> None:
        err = _check_type('N', 'inf', {'type': 'number'})
        self.assertIsNotNone(err)

    def test_number_accepts_zero_and_positive(self) -> None:
        self.assertIsNone(_check_type('N', '0', {'type': 'number'}))
        self.assertIsNone(_check_type('N', '42', {'type': 'number'}))
        self.assertIsNone(_check_type('N', '3.14', {'type': 'number'}))

    def test_bool_rejects_yes_no_etc(self) -> None:
        err = _check_type('B', 'yes', {'type': 'bool'})
        self.assertIsNotNone(err)
        self.assertIn('true', err)
        self.assertIn('false', err)

    def test_bool_accepts_true_false(self) -> None:
        self.assertIsNone(_check_type('B', 'true', {'type': 'bool'}))
        self.assertIsNone(_check_type('B', 'false', {'type': 'bool'}))

    def test_text_type_passes_anything(self) -> None:
        # Fall-through for text + unknown types.
        self.assertIsNone(_check_type('T', 'whatever', {'type': 'text'}))
        self.assertIsNone(_check_type('T', 'whatever', {}))


class CheckUrlTests(unittest.TestCase):
    def test_non_url_key_skipped(self) -> None:
        self.assertIsNone(_check_url('SOME_RANDOM_KEY', 'not-a-url'))

    def test_url_key_with_http_accepted(self) -> None:
        self.assertIsNone(_check_url('OPENHANDS_BASE_URL', 'http://x'))

    def test_url_key_with_https_accepted(self) -> None:
        self.assertIsNone(_check_url('OPENHANDS_BASE_URL', 'https://x'))

    def test_url_key_without_scheme_rejected(self) -> None:
        err = _check_url('OPENHANDS_BASE_URL', 'example.com')
        self.assertIsNotNone(err)
        self.assertIn('http://', err)


class CheckEmailTests(unittest.TestCase):
    def test_non_email_key_skipped(self) -> None:
        self.assertIsNone(_check_email('NOT_AN_EMAIL_KEY', 'whatever'))

    def test_email_key_with_valid_address_accepted(self) -> None:
        self.assertIsNone(_check_email('KATO_OPERATOR_EMAIL', 'me@example.com'))

    def test_email_key_without_at_rejected(self) -> None:
        err = _check_email('KATO_OPERATOR_EMAIL', 'no-at-sign')
        self.assertIsNotNone(err)

    def test_email_key_with_empty_local_part_rejected(self) -> None:
        err = _check_email('KATO_OPERATOR_EMAIL', '@example.com')
        self.assertIsNotNone(err)

    def test_email_key_without_dotted_domain_rejected(self) -> None:
        err = _check_email('KATO_OPERATOR_EMAIL', 'me@localhost')
        self.assertIsNotNone(err)


class ValidateSettingsValuesTests(unittest.TestCase):
    def test_empty_value_skipped(self) -> None:
        # Clearing a field is always valid even if the type check
        # would otherwise reject the empty string.
        self.assertEqual(
            validate_settings_values({'KATO_OPERATOR_EMAIL': ''}),
            [],
        )

    def test_whitespace_value_skipped(self) -> None:
        self.assertEqual(
            validate_settings_values({'KATO_OPERATOR_EMAIL': '   '}),
            [],
        )

    def test_returns_each_failing_check_concatenated(self) -> None:
        errors = validate_settings_values({
            'KATO_OPERATOR_EMAIL': 'not-an-email',
            'OPENHANDS_BASE_URL': 'not-a-url',
        })
        # Both failed → two error messages.
        self.assertEqual(len(errors), 2)

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(validate_settings_values({}), [])


if __name__ == '__main__':
    unittest.main()
