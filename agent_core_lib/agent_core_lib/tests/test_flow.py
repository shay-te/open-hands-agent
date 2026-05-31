"""A-Z flow test for ``agent_core_lib`` prompt assembly (Core-Lib Quality
Standard #2).

This is an INTEGRATION-level scenario: it composes the real prompt helpers
end-to-end the way a host agent client does when building (A) an
implementation prompt and (B) a review-fix prompt, then asserts the
composed text is coherent. It is deliberately product-agnostic — every
fixture is fake (fake ticket ids like ``PROJ-42``, tmp dirs, fake authors)
and assertions target structural behavior, not kato product wording.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    prepend_chat_workspace_context,
    repository_scope_text,
    review_comment_code_snippet,
    review_comment_context_text,
    review_comment_location_text,
    review_comments_batch_text,
    review_conversation_title,
    security_guardrails_text,
    workspace_scope_block,
)
from agent_core_lib.agent_core_lib.helpers.agents_instruction_utils import (
    repository_agents_instructions_text,
)


class ImplementationPromptFlowTests(unittest.TestCase):
    """SCENARIO A — assemble a full implementation prompt from the real
    helpers and assert the composed text holds together coherently."""

    def setUp(self) -> None:
        # A per-task workspace clone: one repo dir containing a real source
        # file plus a checked-in AGENTS.md, exactly as a provisioned clone
        # would look on disk.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = self._tmp.name
        self.repo_dir = os.path.join(self.workspace, 'svc')
        os.makedirs(os.path.join(self.repo_dir, 'app'))
        # Checked-in source file the review comment (scenario B) targets.
        self.source_file = Path(self.repo_dir) / 'app' / 'main.py'
        self.source_file.write_text(
            'import os\n'              # line 1
            'import sys\n'             # line 2
            '\n'                       # line 3
            'def handle(request):\n'   # line 4
            '    value = request.x\n'  # line 5  <- review target line
            '    return value\n'       # line 6
            '\n'                       # line 7
            'WIDGET = 1\n',            # line 8
            encoding='utf-8',
        )
        # Checked-in AGENTS.md whose body must surface via the wrapper.
        (Path(self.repo_dir) / 'AGENTS.md').write_text(
            'Use 2-space indent\n',
            encoding='utf-8',
        )

        self.task = SimpleNamespace(
            id='PROJ-42',
            summary='Fix the thing',
            branch_name='kato/PROJ-42',
        )
        self.prepared = SimpleNamespace(
            repositories=[
                SimpleNamespace(
                    id='svc',
                    local_path=self.repo_dir,
                    destination_branch='main',
                ),
            ],
            repository_branches={'svc': 'feature/x'},
            branch_name='feature/x',
            agents_instructions='Use 2-space indent',
        )

    def _assemble_prompt(self) -> str:
        base_prompt = 'TASK: implement Fix the thing.'
        guardrails = security_guardrails_text()
        scope = workspace_scope_block(
            [self.repo_dir],
            extra_refusal_guidance='To widen scope, sync the repo in your tool.',
        )
        repo_scope = repository_scope_text(self.task, self.prepared)
        agents = repository_agents_instructions_text(self.prepared.repositories)
        body = '\n\n'.join(
            block for block in (guardrails, scope, repo_scope, agents, base_prompt)
            if block
        )
        # Final wrapper a host applies last: continuity + inventory +
        # forbidden-repo guardrails prepended onto the assembled body.
        return prepend_chat_workspace_context(
            body,
            cwd=self.workspace,
            additional_dirs=[self.repo_dir],
            raw_ignored_value='secrets-repo',
            is_resumed_session=False,
        )

    def test_workspace_path_appears(self) -> None:
        prompt = self._assemble_prompt()
        # The allowed repo path must be in the strict scope block and the
        # cwd must appear in the inventory block.
        self.assertIn(self.repo_dir, prompt)
        self.assertIn(self.workspace, prompt)

    def test_extra_refusal_guidance_is_appended_verbatim(self) -> None:
        prompt = self._assemble_prompt()
        self.assertIn('To widen scope, sync the repo in your tool.', prompt)
        # And it lands inside the strict-boundary block, after its header.
        boundary_idx = prompt.index('WORKSPACE SCOPE — STRICT BOUNDARY')
        refusal_idx = prompt.index('To widen scope, sync the repo in your tool.')
        self.assertGreater(refusal_idx, boundary_idx)

    def test_repository_scope_lists_repo_with_branch_and_destination(self) -> None:
        prompt = self._assemble_prompt()
        self.assertIn('Only modify these repositories:', prompt)
        # The repo id, its prepared per-repo branch, and its destination
        # branch all render in one coherent line.
        self.assertIn('- svc at ' + self.repo_dir, prompt)
        self.assertIn('prepared branch feature/x from main', prompt)
        # The orchestration layer owns publishing — agent must not push.
        self.assertIn('do not run git checkout', prompt)

    def test_agents_md_body_surfaces_via_wrapper(self) -> None:
        prompt = self._assemble_prompt()
        # The AGENTS.md wrapper header + the file's actual checked-in body.
        self.assertIn('Repository AGENTS.md instructions:', prompt)
        self.assertIn('Use 2-space indent', prompt)
        # Rendered under the repo label + relative AGENTS.md path.
        self.assertIn('Repository svc at ' + self.repo_dir, prompt)
        self.assertIn('AGENTS.md:', prompt)

    def test_forbidden_repository_guardrail_present(self) -> None:
        prompt = self._assemble_prompt()
        self.assertIn('- secrets-repo', prompt)
        self.assertIn('AGENT_IGNORED_REPOSITORY_FOLDERS', prompt)
        self.assertIn('out of bounds', prompt)

    def test_continuity_block_present(self) -> None:
        prompt = self._assemble_prompt()
        self.assertIn('Continuity instruction (read first):', prompt)
        self.assertIn('authoritative record', prompt)

    def test_security_guardrails_present(self) -> None:
        prompt = self._assemble_prompt()
        self.assertIn('Security guardrails:', prompt)
        self.assertIn('untrusted data', prompt)

    def test_block_ordering_is_coherent(self) -> None:
        # The prepended context (continuity / inventory / forbidden) comes
        # before the original body (security + scope + base task), so the
        # agent reads boundaries before the task text.
        prompt = self._assemble_prompt()
        continuity_idx = prompt.index('Continuity instruction (read first):')
        forbidden_idx = prompt.index('- secrets-repo')
        base_idx = prompt.index('TASK: implement Fix the thing.')
        self.assertLess(continuity_idx, base_idx)
        self.assertLess(forbidden_idx, base_idx)


class ReviewFixPromptFlowTests(unittest.TestCase):
    """SCENARIO B — assemble a review-fix prompt batch and assert the
    location, code snippet, batch numbering, and context-drop behavior all
    compose coherently."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = self._tmp.name
        os.makedirs(os.path.join(self.workspace, 'app'))
        self.source_file = Path(self.workspace) / 'app' / 'main.py'
        # 8 real lines; the review targets line 5 ("value = request.x").
        self.source_file.write_text(
            'import os\n'              # 1
            'import sys\n'             # 2
            '\n'                       # 3
            'def handle(request):\n'   # 4
            '    value = request.x\n'  # 5  <- target
            '    return value\n'       # 6
            '\n'                       # 7
            'WIDGET = 1\n',            # 8
            encoding='utf-8',
        )
        self.target_line = 5

        # A file-anchored review comment plus its thread of replies. The
        # bot self-reply must be dropped from context; alice's must stay.
        self.file_comment = SimpleNamespace(
            comment_id='c-1',
            file_path='app/main.py',
            line_number=self.target_line,
            line_type='ADDED',
            commit_sha='abc123',
            author='alice',
            body='Please rename this',
            all_comments=[
                {'author': 'alice', 'body': 'Please rename this'},
                {'author': 'kato', 'body': 'Kato addressed review comment c-1 — done.'},
            ],
        )
        # A PR-level comment: no file_path / no line, so it renders as the
        # "(no file/line — PR-level comment)" entry in the batch.
        self.pr_comment = SimpleNamespace(
            comment_id='c-2',
            file_path='',
            line_number='',
            line_type='',
            commit_sha='',
            author='bob',
            body='Overall this needs a changelog entry',
            all_comments=[],
        )

    def test_conversation_title_uses_task_id(self) -> None:
        title = review_conversation_title(self.file_comment, task_id='PROJ-42')
        self.assertEqual(title, 'PROJ-42 [review]')
        # Falls back to the comment id when there's no task id.
        fallback = review_conversation_title(self.file_comment, task_id='')
        self.assertEqual(fallback, 'Fix review comment c-1')

    def test_location_text_renders_file_line_type_and_commit(self) -> None:
        location = review_comment_location_text(self.file_comment)
        self.assertIn('app/main.py:%d (ADDED)' % self.target_line, location)
        self.assertIn('Commit: abc123', location)

    def test_code_snippet_marks_the_target_line(self) -> None:
        snippet = review_comment_code_snippet(self.file_comment, self.workspace)
        self.assertIn('Code at line %d:' % self.target_line, snippet)
        # The target line carries the '→' marker and its real source text.
        self.assertIn('→', snippet)
        self.assertIn('value = request.x', snippet)
        # Surrounding context lines are present but NOT marked.
        self.assertIn('def handle(request):', snippet)
        target_rendered_line = [
            line for line in snippet.splitlines() if 'value = request.x' in line
        ][0]
        self.assertIn('→', target_rendered_line)
        context_rendered_line = [
            line for line in snippet.splitlines() if 'def handle(request):' in line
        ][0]
        self.assertNotIn('→', context_rendered_line)

    def test_batch_text_numbers_comments_and_renders_pr_level_entry(self) -> None:
        batch = review_comments_batch_text(
            [self.file_comment, self.pr_comment],
            workspace_path=self.workspace,
        )
        # Numbered 1. and 2.
        self.assertIn('1.', batch)
        self.assertIn('2.', batch)
        # The file comment includes its location + snippet + author body.
        self.assertIn('app/main.py:%d (ADDED)' % self.target_line, batch)
        self.assertIn('value = request.x', batch)
        self.assertIn('Comment by alice: Please rename this', batch)
        # The PR-level comment renders the no-file marker + its author body.
        self.assertIn('(no file/line — PR-level comment)', batch)
        self.assertIn('Comment by bob: Overall this needs a changelog entry', batch)
        # Ordering: comment 1 precedes comment 2 in the rendered batch.
        self.assertLess(batch.index('1.'), batch.index('2.'))

    def test_context_text_drops_self_reply_keeps_reviewer(self) -> None:
        context = review_comment_context_text(self.file_comment)
        self.assertIn('Review comment context:', context)
        # alice's reviewer comment is kept...
        self.assertIn('- alice: Please rename this', context)
        # ...the bot's own "Kato addressed ..." self-reply is dropped.
        self.assertNotIn('Kato addressed', context)
        self.assertNotIn('kato:', context)

    def test_context_text_empty_for_single_comment_thread(self) -> None:
        # A thread with one comment has no extra context to render.
        solo = SimpleNamespace(all_comments=[{'author': 'alice', 'body': 'hi'}])
        self.assertEqual(review_comment_context_text(solo), '')

    def test_full_review_fix_prompt_is_coherent(self) -> None:
        # Compose every review-fix helper into one prompt the way a host
        # client would, and assert the end-to-end text is coherent.
        title = review_conversation_title(self.file_comment, task_id='PROJ-42')
        location = review_comment_location_text(self.file_comment)
        snippet = review_comment_code_snippet(self.file_comment, self.workspace)
        batch = review_comments_batch_text(
            [self.file_comment, self.pr_comment],
            workspace_path=self.workspace,
        )
        context = review_comment_context_text(self.file_comment)
        prompt = '\n\n'.join([title, location, snippet, batch, context])

        # Title, the precise location, the marked snippet, the batch's
        # PR-level entry, and the de-duplicated context all coexist.
        self.assertIn('PROJ-42 [review]', prompt)
        self.assertIn('app/main.py:%d (ADDED)' % self.target_line, prompt)
        self.assertIn('Commit: abc123', prompt)
        self.assertIn('→', prompt)
        self.assertIn('(no file/line — PR-level comment)', prompt)
        self.assertIn('- alice: Please rename this', prompt)
        self.assertNotIn('Kato addressed', prompt)


if __name__ == '__main__':
    unittest.main()
