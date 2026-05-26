"""Full coverage for runners/_helpers.py.

Tests EXCLUDE_DIRS membership, iter_workspace_files walker,
and workspace_relative path normaliser.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from security_scanner_core_lib.security_scanner_core_lib.runners._helpers import (
    EXCLUDE_DIRS,
    RunnerUnavailableError,
    iter_workspace_files,
    workspace_relative,
)


class ExcludeDirsTests(unittest.TestCase):
    def test_contains_common_dependency_dirs(self) -> None:
        for d in ('node_modules', '.venv', 'venv', '__pycache__', 'dist', 'build'):
            self.assertIn(d, EXCLUDE_DIRS)

    def test_contains_vcs_dirs(self) -> None:
        self.assertIn('.git', EXCLUDE_DIRS)
        self.assertIn('.hg', EXCLUDE_DIRS)
        self.assertIn('.svn', EXCLUDE_DIRS)

    def test_contains_cache_dirs(self) -> None:
        for d in ('.mypy_cache', '.pytest_cache', '.ruff_cache', '.tox', '.cache'):
            self.assertIn(d, EXCLUDE_DIRS)

    def test_is_frozenset(self) -> None:
        self.assertIsInstance(EXCLUDE_DIRS, frozenset)

    def test_does_not_contain_src(self) -> None:
        self.assertNotIn('src', EXCLUDE_DIRS)


class RunnerUnavailableErrorTests(unittest.TestCase):
    def test_is_exception_subclass(self) -> None:
        self.assertTrue(issubclass(RunnerUnavailableError, Exception))

    def test_message_preserved(self) -> None:
        exc = RunnerUnavailableError('bandit not installed')
        self.assertIn('bandit not installed', str(exc))

    def test_can_be_raised_and_caught(self) -> None:
        with self.assertRaises(RunnerUnavailableError):
            raise RunnerUnavailableError('tool missing')


class IterWorkspaceFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def _make_file(self, relpath: str, content: str = '') -> Path:
        p = self.workspace / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def test_empty_workspace_yields_nothing(self) -> None:
        self.assertEqual(list(iter_workspace_files(self.workspace)), [])

    def test_yields_top_level_files(self) -> None:
        f = self._make_file('main.py', 'x=1')
        results = list(iter_workspace_files(self.workspace))
        self.assertIn(f, results)

    def test_yields_nested_files(self) -> None:
        f = self._make_file('src/nested/deep.py')
        results = list(iter_workspace_files(self.workspace))
        self.assertIn(f, results)

    def test_skips_excluded_dirs(self) -> None:
        for excluded in ('.git', 'node_modules', '__pycache__', '.venv'):
            self._make_file(f'{excluded}/secret.txt', 'value')
        results = list(iter_workspace_files(self.workspace))
        self.assertEqual(results, [])

    def test_skips_excluded_dirs_nested(self) -> None:
        self._make_file('src/node_modules/pkg/index.js')
        self._make_file('src/app.py')
        results = list(iter_workspace_files(self.workspace))
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].name.endswith('app.py'))

    def test_yields_multiple_files_across_dirs(self) -> None:
        self._make_file('a.py')
        self._make_file('sub/b.py')
        self._make_file('sub/c.txt')
        results = list(iter_workspace_files(self.workspace))
        self.assertEqual(len(results), 3)

    def test_non_directory_workspace_yields_nothing(self) -> None:
        results = list(iter_workspace_files(self.workspace / 'nonexistent'))
        self.assertEqual(results, [])

    def test_yields_hidden_files_not_in_excluded(self) -> None:
        f = self._make_file('.env', 'KEY=val')
        results = list(iter_workspace_files(self.workspace))
        self.assertIn(f, results)

    def test_skips_broken_symlinks_that_are_neither_dir_nor_file(self) -> None:
        # Branch 63->58: a child that is neither ``is_dir()`` nor
        # ``is_file()`` (broken symlink → both return False) must be
        # skipped silently rather than crashing or yielded. Locks the
        # walker's tolerance for stray dangling links in repos.
        import os
        real = self._make_file('real.py', 'x=1')
        broken = self.workspace / 'broken_link'
        os.symlink(str(self.workspace / 'does_not_exist'), str(broken))
        results = list(iter_workspace_files(self.workspace))
        self.assertIn(real, results)
        self.assertNotIn(broken, results)


class WorkspaceRelativeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def test_returns_relative_path_string(self) -> None:
        target = self.workspace / 'src' / 'app.py'
        result = workspace_relative(self.workspace, target)
        self.assertEqual(result, 'src/app.py')

    def test_top_level_file_returns_name_only(self) -> None:
        target = self.workspace / 'main.py'
        result = workspace_relative(self.workspace, target)
        self.assertEqual(result, 'main.py')

    def test_outside_workspace_falls_back_to_absolute(self) -> None:
        outside = Path('/tmp/some_other_file.py')
        result = workspace_relative(self.workspace, outside)
        self.assertEqual(result, str(outside))

    def test_result_is_string(self) -> None:
        target = self.workspace / 'a.py'
        result = workspace_relative(self.workspace, target)
        self.assertIsInstance(result, str)


if __name__ == '__main__':
    unittest.main()
