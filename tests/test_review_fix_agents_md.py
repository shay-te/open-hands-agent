"""Tests for AGENTS.md surfacing in review-fix prompts.

Pin down two things:

1. ``agents_instructions_for_path(workspace_path)`` walks the
   workspace clone for ``AGENTS.md`` files and returns the same
   wrapper string the implementation prompt uses.
2. The Claude (and OpenHands) review-fix prompt builders include
   the AGENTS.md content when the workspace has one.

Today the implementation prompt already inlines AGENTS.md via
``prepared_task.agents_instructions``, but the review-fix path
didn't have access to ``prepared_task`` (only to a workspace path
on the comment). The new helper closes that gap.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from kato_core_lib.data_layers.data.fields import PullRequestFields
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from agent_core_lib.agent_core_lib.helpers.agents_instruction_utils import (
    agents_instructions_for_path,
)


class AgentsInstructionsForPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_empty_path_returns_empty_string(self) -> None:
        self.assertEqual(agents_instructions_for_path(''), '')

    def test_nonexistent_path_returns_empty_string(self) -> None:
        self.assertEqual(
            agents_instructions_for_path(str(self.root / 'does-not-exist')),
            '',
        )

    def test_no_agents_md_returns_empty_string(self) -> None:
        # A real workspace with no AGENTS.md anywhere → no block.
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'app.py').write_text('print("hi")', encoding='utf-8')
        self.assertEqual(agents_instructions_for_path(str(self.root)), '')

    def test_root_agents_md_is_included(self) -> None:
        (self.root / 'AGENTS.md').write_text(
            '# Project conventions\n\nUse 4-space indentation.\n',
            encoding='utf-8',
        )
        text = agents_instructions_for_path(
            str(self.root), repository_id='client',
        )
        self.assertIn('Repository AGENTS.md instructions', text)
        self.assertIn('Repository client', text)
        self.assertIn('AGENTS.md', text)
        self.assertIn('4-space indentation', text)

    def test_nested_agents_md_files_are_all_included(self) -> None:
        (self.root / 'AGENTS.md').write_text('Root rules.', encoding='utf-8')
        (self.root / 'frontend').mkdir()
        (self.root / 'frontend' / 'AGENTS.md').write_text(
            'Frontend-specific rules.', encoding='utf-8',
        )
        text = agents_instructions_for_path(str(self.root))
        self.assertIn('Root rules', text)
        self.assertIn('Frontend-specific rules', text)
        self.assertIn('frontend/AGENTS.md', text)

    def test_falls_back_to_directory_name_when_no_repository_id(self) -> None:
        repo_dir = self.root / 'my-repo'
        repo_dir.mkdir()
        (repo_dir / 'AGENTS.md').write_text('rules', encoding='utf-8')
        text = agents_instructions_for_path(str(repo_dir))
        self.assertIn('Repository my-repo', text)


class ReviewPromptIncludesAgentsMdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / 'AGENTS.md').write_text(
            'Use rg before editing. Do not run npm build.',
            encoding='utf-8',
        )

    def _comment(self, body: str = 'fix the typo') -> ReviewComment:
        c = ReviewComment(
            pull_request_id='17', comment_id='100',
            author='reviewer', body=body,
        )
        setattr(c, PullRequestFields.REPOSITORY_ID, 'client')
        return c

    def test_singular_review_prompt_inlines_agents_md(self) -> None:
        prompt = ClaudeCliClient._build_review_prompt(
            self._comment(),
            'feature/proj-1',
            workspace_path=str(self.root),
        )
        self.assertIn('Repository AGENTS.md instructions', prompt)
        self.assertIn('Use rg before editing', prompt)

    def test_singular_answer_mode_inlines_agents_md(self) -> None:
        prompt = ClaudeCliClient._build_review_prompt(
            self._comment(body='how does this work?'),
            'feature/proj-1',
            workspace_path=str(self.root),
            mode='answer',
        )
        self.assertIn('Repository AGENTS.md instructions', prompt)

    def test_batched_review_prompt_inlines_agents_md(self) -> None:
        prompt = ClaudeCliClient._build_review_comments_batch_prompt(
            [self._comment(), self._comment()],
            'feature/proj-1',
            workspace_path=str(self.root),
        )
        self.assertIn('Repository AGENTS.md instructions', prompt)
        self.assertIn('Use rg before editing', prompt)

    def test_no_agents_md_omits_the_block(self) -> None:
        # Workspace with no AGENTS.md → prompt should not mention
        # AGENTS.md at all (no orphaned header).
        empty_workspace = self.root / 'empty'
        empty_workspace.mkdir()
        prompt = ClaudeCliClient._build_review_prompt(
            self._comment(),
            'feature/proj-1',
            workspace_path=str(empty_workspace),
        )
        self.assertNotIn('Repository AGENTS.md instructions', prompt)

    def test_no_workspace_path_omits_the_block(self) -> None:
        # Operator-less / older setups without a resolved workspace
        # path should not crash and should not emit a malformed
        # AGENTS.md header.
        prompt = ClaudeCliClient._build_review_prompt(
            self._comment(),
            'feature/proj-1',
            workspace_path='',
        )
        self.assertNotIn('Repository AGENTS.md instructions', prompt)


if __name__ == '__main__':
    unittest.main()
