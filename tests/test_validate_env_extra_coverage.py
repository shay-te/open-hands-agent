"""Extra coverage for ``validate_env``."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kato_core_lib import validate_env as ve


class ReadEnvFileTests(unittest.TestCase):
    def test_raises_when_path_does_not_exist(self) -> None:
        # Line 101.
        with self.assertRaisesRegex(FileNotFoundError, 'env file not found'):
            ve._read_env_file('/does/not/exist.env')


class ValidateClaudeBinaryTests(unittest.TestCase):
    def test_error_when_binary_path_does_not_exist(self) -> None:
        # Lines 356-357: ``elif not Path(binary).exists()``.
        errors = ve.validate_claude_env({
            'KATO_CLAUDE_BINARY': '/nonexistent/claude',
        })
        self.assertTrue(any(
            'does not exist' in e for e in errors
        ))

    def test_error_when_timeout_below_minimum(self) -> None:
        # Line 365-366 (already covered) + 367-368 (ValueError).
        errors = ve.validate_claude_env({
            'KATO_CLAUDE_BINARY': '/bin/sh',
            'KATO_CLAUDE_TIMEOUT_SECONDS': '10',  # < 60
        })
        self.assertTrue(any(
            'at least 60' in e for e in errors
        ))

    def test_error_when_timeout_not_an_integer(self) -> None:
        # Lines 367-368.
        errors = ve.validate_claude_env({
            'KATO_CLAUDE_BINARY': '/bin/sh',
            'KATO_CLAUDE_TIMEOUT_SECONDS': 'abc',
        })
        self.assertTrue(any(
            'integer' in e for e in errors
        ))


class ValidateModeBranchesTests(unittest.TestCase):
    def test_openhands_mode_routes_to_claude_when_backend_claude(self) -> None:
        # Lines 387-388: openhands mode + backend=claude → claude path.
        env = {'KATO_AGENT_BACKEND': 'claude'}
        with patch.object(ve, 'validate_claude_env',
                          return_value=['claude-err']) as claude_v, \
             patch.object(ve, 'validate_openhands_env') as oh_v:
            result = ve._validate('openhands', env)
        claude_v.assert_called_once()
        oh_v.assert_not_called()
        self.assertEqual(result, ['claude-err'])

    def test_all_mode_routes_to_claude_when_backend_claude(self) -> None:
        # Line 392.
        env = {'KATO_AGENT_BACKEND': 'claude'}
        with patch.object(ve, 'validate_agent_env', return_value=[]), \
             patch.object(ve, 'validate_claude_env',
                          return_value=['claude-err']) as claude_v:
            result = ve._validate('all', env)
        claude_v.assert_called_once()
        self.assertEqual(result, ['claude-err'])


class MainEntryPointTests(unittest.TestCase):
    def test_main_returns_0_on_valid_env(self) -> None:
        # Lines 422-429.
        import sys
        argv_backup = sys.argv
        sys.argv = ['validate_env', '--mode', 'agent']
        try:
            with patch.object(ve, '_build_env', return_value={}), \
                 patch.object(ve, '_validate', return_value=[]):
                rc = ve.main()
            self.assertEqual(rc, 0)
        finally:
            sys.argv = argv_backup

    def test_main_returns_1_when_errors_present(self) -> None:
        # Lines 423-426: errors logged + exit code 1.
        import sys
        argv_backup = sys.argv
        sys.argv = ['validate_env', '--mode', 'all']
        try:
            with patch.object(ve, '_build_env', return_value={}), \
                 patch.object(ve, '_validate',
                              return_value=['err-1', 'err-2']):
                rc = ve.main()
            self.assertEqual(rc, 1)
        finally:
            sys.argv = argv_backup


class ScriptEntryPointTests(unittest.TestCase):
    def test_module_as_script_raises_systemexit(self) -> None:
        # Line 433: ``if __name__ == '__main__': raise SystemExit(main())``.
        # The runpy-loaded module is a fresh instance; our patches to
        # ``ve`` don't affect it. We just check that running raises
        # SystemExit with some integer code (whatever validation says).
        import runpy
        import sys
        argv_backup = sys.argv
        sys.argv = ['validate_env', '--mode', 'agent']
        try:
            with self.assertRaises(SystemExit) as ctx:
                runpy.run_module(
                    'kato_core_lib.validate_env', run_name='__main__',
                )
            self.assertIn(ctx.exception.code, (0, 1))
        finally:
            sys.argv = argv_backup


class ValidateRepositoryRootPathTests(unittest.TestCase):
    """Cover ``_validate_repository_root_path`` — the boot-time check
    that ``REPOSITORY_ROOT_PATH`` exists on disk. Two branches:
    empty path (skip) and path-does-not-exist (return error string)."""

    def test_empty_path_is_a_noop(self) -> None:
        from kato_core_lib.validate_env import _validate_repository_root_path
        # Empty / whitespace-only path → no check, no error.
        self.assertEqual(_validate_repository_root_path({}), [])
        self.assertEqual(
            _validate_repository_root_path({'REPOSITORY_ROOT_PATH': '   '}),
            [],
        )

    def test_nonexistent_path_returns_actionable_error(self) -> None:
        from kato_core_lib.validate_env import _validate_repository_root_path
        errors = _validate_repository_root_path({
            'REPOSITORY_ROOT_PATH': '/this/path/should/not/exist/anywhere',
        })
        self.assertEqual(len(errors), 1)
        # Operator-readable: must name the bad path so they know what
        # to fix in their .env.
        self.assertIn(
            '/this/path/should/not/exist/anywhere', errors[0],
        )
        self.assertIn('does not exist', errors[0])

    def test_existing_directory_passes(self) -> None:
        import tempfile
        from kato_core_lib.validate_env import _validate_repository_root_path
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                _validate_repository_root_path({
                    'REPOSITORY_ROOT_PATH': tmp,
                }),
                [],
            )


if __name__ == '__main__':
    unittest.main()
