"""Coverage for ``env_file_runner.py`` — the real-vs-scaffold .env detector."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from security_scanner_core_lib.security_scanner_core_lib.runners.env_file_runner import (
    _is_real_env,
    _parse_env_line,
    _value_looks_real,
    run,
)


class IsRealEnvTests(unittest.TestCase):
    def test_bare_env_is_real(self) -> None:
        self.assertTrue(_is_real_env(Path('/repo/.env')))

    def test_env_dot_example_is_scaffold(self) -> None:
        # Line 88: name ends with scaffold suffix → False.
        self.assertFalse(_is_real_env(Path('/repo/.env.example')))
        self.assertFalse(_is_real_env(Path('/repo/.env.template')))
        self.assertFalse(_is_real_env(Path('/repo/.env.sample')))

    def test_random_filename_is_not_real(self) -> None:
        self.assertFalse(_is_real_env(Path('/repo/notes.txt')))

    def test_scaffold_suffix_check_returns_false(self) -> None:
        # Line 88: defensive — if a name is in the basename set AND also
        # ends with a scaffold suffix, the scaffold check still rejects it.
        # In practice the configured basenames don't include any with a
        # scaffold suffix, so we patch the basename set to construct the
        # exact double-match scenario the code is defending against.
        from unittest.mock import patch
        with patch(
            'security_scanner_core_lib.security_scanner_core_lib.runners.env_file_runner._REAL_ENV_BASENAMES',
            frozenset({'.env.example'}),  # in basenames AND ends in '.example'
        ):
            self.assertFalse(_is_real_env(Path('/repo/.env.example')))


class ParseEnvLineTests(unittest.TestCase):
    def test_blank_line_returns_none(self) -> None:
        # Line 118: blank/whitespace line → None.
        self.assertIsNone(_parse_env_line(''))
        self.assertIsNone(_parse_env_line('   \n'))

    def test_comment_line_returns_none(self) -> None:
        # Line 118: ``#``-prefixed comment line → None.
        self.assertIsNone(_parse_env_line('# this is a comment'))

    def test_no_equals_sign_returns_none(self) -> None:
        # Line 122: no ``=`` in line → None.
        self.assertIsNone(_parse_env_line('KEY-WITHOUT-VALUE'))

    def test_export_prefix_is_stripped(self) -> None:
        result = _parse_env_line('export FOO=bar')
        self.assertEqual(result, ('FOO', 'bar', ''))

    def test_invalid_key_name_returns_none(self) -> None:
        # Line 126: key with hyphens/special chars → not alphanumeric → None.
        self.assertIsNone(_parse_env_line('BAD-KEY=value'))
        self.assertIsNone(_parse_env_line('=valueonly'))

    def test_inline_comment_extracted(self) -> None:
        result = _parse_env_line('TOKEN=abc # security-scanner:placeholder')
        key, value, comment = result
        self.assertEqual(key, 'TOKEN')
        self.assertEqual(value, 'abc')
        self.assertIn('placeholder', comment)

    def test_single_quoted_value_preserves_hash(self) -> None:
        # Line 134: ``in_single`` toggle — # inside single quotes is not a comment.
        result = _parse_env_line("KEY='value#with#hash'")
        key, value, comment = result
        self.assertEqual(value, "'value#with#hash'")
        self.assertEqual(comment, '')


class ValueLooksRealTests(unittest.TestCase):
    def test_blank_value_is_not_real(self) -> None:
        self.assertFalse(_value_looks_real(''))
        self.assertFalse(_value_looks_real('"   "'))

    def test_short_value_is_not_real(self) -> None:
        # Anything < 6 chars is treated as config not credential.
        self.assertFalse(_value_looks_real('abc'))

    def test_real_looking_long_value_is_real(self) -> None:
        self.assertTrue(_value_looks_real('sk-1234567890abcdef'))


class RunEnvFileScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def test_oserror_reading_env_file_is_logged_and_skipped(self) -> None:
        # Lines 174-177: ``read_text`` raises OSError → log warning + skip.
        env_file = self.workspace / '.env'
        env_file.write_text('TOKEN=real-credential-string-here\n')
        mock_logger = MagicMock()
        with patch.object(Path, 'read_text', side_effect=PermissionError('locked')):
            findings = run(str(self.workspace), logger=mock_logger)
        self.assertEqual(findings, [])
        mock_logger.warning.assert_called()

    def test_oserror_reading_env_file_without_logger_silently_skipped(self) -> None:
        # Branch 175->177: ``logger is None`` skips the ``logger.warning``
        # call and falls through to ``continue``. Locks the no-logger
        # tolerance on a read failure (e.g. permission-locked .env).
        env_file = self.workspace / '.env'
        env_file.write_text('TOKEN=real-credential-string-here\n')
        with patch.object(Path, 'read_text', side_effect=PermissionError('locked')):
            findings = run(str(self.workspace))
        self.assertEqual(findings, [])

    def test_unparseable_lines_skipped(self) -> None:
        # Line 181: ``parsed is None`` → continue. Mix valid and invalid lines.
        env_file = self.workspace / '.env'
        env_file.write_text(
            '# comment\n'
            '\n'
            'BAD-KEY=value\n'
            'TOKEN=real-credential-string-here\n'
        )
        findings = run(str(self.workspace))
        self.assertEqual(len(findings), 1)
        self.assertIn('TOKEN', findings[0].message)

    def test_returns_empty_for_scaffold_env(self) -> None:
        # `.env.example` is scaffold → not scanned.
        (self.workspace / '.env.example').write_text(
            'TOKEN=real-looking-credential-here',
        )
        findings = run(str(self.workspace))
        self.assertEqual(findings, [])


if __name__ == '__main__':
    unittest.main()
