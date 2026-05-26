"""Targeted branch-coverage tests for ``agent_prompt_utils``.

Each test names the specific gap it closes so future readers know
why the case looks deliberately narrow.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    repository_scope_text,
    workspace_inventory_block,
    workspace_scope_block,
)


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
