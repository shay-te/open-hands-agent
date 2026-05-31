"""Targeted branch-coverage tests for ``agent_prompt_utils``.

Each test names the specific gap it closes so future readers know
why the case looks deliberately narrow.
"""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    IGNORED_REPOSITORY_FOLDERS_ENV,
    _LEGACY_IGNORED_REPOSITORY_FOLDERS_ENV,
    forbidden_repository_guardrails_text,
    ignored_repository_folder_names,
    repository_scope_text,
    workspace_inventory_block,
    workspace_scope_block,
)


class IgnoredFoldersEnvNameTests(unittest.TestCase):
    """The ignored-folders env read prefers the generic name, falls back
    to the legacy ``KATO_*`` name (compatibility only), and the rendered
    guardrails/scope text uses generic wording by default."""

    def _isolated_env(self):
        # patch.dict snapshots + restores os.environ; inside we drop both
        # keys so the host's real environment can't pollute the case.
        ctx = patch.dict(os.environ, {}, clear=False)
        ctx.start()
        os.environ.pop(IGNORED_REPOSITORY_FOLDERS_ENV, None)
        os.environ.pop(_LEGACY_IGNORED_REPOSITORY_FOLDERS_ENV, None)
        self.addCleanup(ctx.stop)

    def test_generic_env_var_is_read(self) -> None:
        self._isolated_env()
        os.environ[IGNORED_REPOSITORY_FOLDERS_ENV] = 'a, b'
        self.assertEqual(ignored_repository_folder_names(), ['a', 'b'])

    def test_legacy_kato_env_var_still_works_as_fallback(self) -> None:
        self._isolated_env()
        os.environ[_LEGACY_IGNORED_REPOSITORY_FOLDERS_ENV] = 'c, d'
        self.assertEqual(ignored_repository_folder_names(), ['c', 'd'])

    def test_generic_env_var_wins_when_both_set(self) -> None:
        self._isolated_env()
        os.environ[IGNORED_REPOSITORY_FOLDERS_ENV] = 'generic-wins'
        os.environ[_LEGACY_IGNORED_REPOSITORY_FOLDERS_ENV] = 'legacy-loses'
        self.assertEqual(ignored_repository_folder_names(), ['generic-wins'])

    def test_canonical_constant_is_the_generic_name(self) -> None:
        self.assertEqual(IGNORED_REPOSITORY_FOLDERS_ENV, 'AGENT_IGNORED_REPOSITORY_FOLDERS')
        self.assertEqual(_LEGACY_IGNORED_REPOSITORY_FOLDERS_ENV, 'KATO_IGNORED_REPOSITORY_FOLDERS')

    def test_rendered_text_uses_generic_wording_by_default(self) -> None:
        guardrails = forbidden_repository_guardrails_text('secret-client, legacy-api')
        self.assertIn('AGENT_IGNORED_REPOSITORY_FOLDERS', guardrails)
        self.assertNotIn('KATO_IGNORED_REPOSITORY_FOLDERS', guardrails)

        block = workspace_scope_block(['/wks/PROJ/repo-a'])
        self.assertIn('AGENT_WORKSPACES_ROOT', block)
        self.assertIn('AGENT_REPOSITORY_ROOT_PATH', block)
        self.assertNotIn('KATO_WORKSPACES_ROOT', block)
        self.assertNotIn('KATO_REPOSITORY_ROOT_PATH', block)


class WorkspaceInventoryBlockBranchTests(unittest.TestCase):
    def test_extras_only_render_without_cwd_header(self) -> None:
        # Branch 73->75: ``cwd_text`` is empty so the ``(cwd)`` line is
        # skipped and the renderer goes straight to the extras loop.
        # Tasks created before the workspace is provisioned hit this
        # path — the inventory still anchors the agent to the extras.
        block = workspace_inventory_block(
            cwd='', additional_dirs=['/wks/PROJ/repo-a', '/wks/PROJ/repo-b'],
        )
        self.assertIn('Repositories available in this workspace:', block)
        self.assertNotIn('(cwd)', block)
        self.assertIn('- /wks/PROJ/repo-a', block)
        self.assertIn('- /wks/PROJ/repo-b', block)


class WorkspaceScopeBlockBranchTests(unittest.TestCase):
    def test_skips_paths_that_normalize_to_dot_or_blank(self) -> None:
        # Branch 155->151: ``normalized`` is empty or just '.' — fall
        # through without appending and loop to the next raw entry.
        # Without coverage here, a stray '.' in the caller's allowed-
        # paths config would silently render a malformed scope block.
        # Mixing in a real path proves the loop continues correctly.
        block = workspace_scope_block(['.', '', '/wks/PROJ/repo-a'])
        self.assertIn('/wks/PROJ/repo-a', block)
        # The bullet list shouldn't contain a lone '.' or empty line.
        self.assertNotIn('  - .\n', block)
        self.assertNotIn('  - \n', block)

    def test_returns_empty_when_every_path_is_filtered(self) -> None:
        # Same branch (155->151), but the filter takes EVERY path so
        # ``paths`` stays empty and the function short-circuits to ''.
        self.assertEqual(workspace_scope_block(['.', '', None]), '')


class RepositoryScopeTextBranchTests(unittest.TestCase):
    def test_prepared_task_without_branch_name_keeps_task_branch(self) -> None:
        # Branch 198->203: ``prepared_task.branch_name`` is falsy — the
        # ``if`` body is skipped and we fall straight through to the
        # ``if not repositories`` check (line 203). The task's own
        # ``branch_name`` must survive the prepared-task override.
        task = SimpleNamespace(
            id='PROJ-1',
            branch_name='task-branch',
            repository_branches={},
            repositories=[],
        )
        prepared = SimpleNamespace(
            repositories=[],
            repository_branches={},
            branch_name='',  # falsy — branch override skipped
        )
        out = repository_scope_text(task, prepared)
        # No repositories → falls into the "before making changes"
        # template which embeds the resolved branch name. The
        # branch name should be the TASK's branch, not the empty
        # prepared one.
        self.assertIn('task-branch', out)
        self.assertIn('Before making changes', out)


if __name__ == '__main__':
    unittest.main()
