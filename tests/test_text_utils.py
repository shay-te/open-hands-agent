import types
import unittest


from kato.helpers.text_utils import (
    alphanumeric_lower_text,
    condensed_text,
    normalized_lower_text,
    normalized_text,
    text_from_attr,
    text_from_mapping,
)


class TextUtilsTests(unittest.TestCase):
    def test_normalized_text_trims_and_handles_none(self) -> None:
        self.assertEqual(normalized_text('  value  '), 'value')
        self.assertEqual(normalized_text(None), '')

    def test_normalized_lower_text_lowercases_trimmed_text(self) -> None:
        self.assertEqual(normalized_lower_text('  VaLuE  '), 'value')

    def test_condensed_text_collapses_internal_whitespace(self) -> None:
        self.assertEqual(condensed_text('  many \n spaced \t words  '), 'many spaced words')

    def test_alphanumeric_lower_text_strips_non_alnum_characters(self) -> None:
        self.assertEqual(alphanumeric_lower_text(' In Progress! '), 'inprogress')

    def test_text_from_mapping_returns_trimmed_value(self) -> None:
        self.assertEqual(text_from_mapping({'name': '  test  '}, 'name'), 'test')
        self.assertEqual(text_from_mapping(None, 'name', ' fallback '), 'fallback')

    def test_text_from_attr_returns_trimmed_value(self) -> None:
        obj = types.SimpleNamespace(name='  test  ')
        self.assertEqual(text_from_attr(obj, 'name'), 'test')
        self.assertEqual(text_from_attr(obj, 'missing', ' fallback '), 'fallback')
