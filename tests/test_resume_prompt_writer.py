"""Tests for the resume_prompt.md renderer + atomic writer.

The watcher polls live sessions and calls these helpers; the
renderer + writer themselves are pure and tested here in isolation.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kato_core_lib.helpers.resume_prompt_writer import (
    RESUME_PROMPT_FILENAME,
    ResumePromptInputs,
    build_inputs_from_session,
    render_resume_prompt,
    write_resume_prompt,
)


def _inputs(**overrides) -> ResumePromptInputs:
    defaults = dict(
        task_id='PROJ-1',
        task_summary='Fix the typo',
        branch_name='feature/proj-1',
        workspace_path='/x/workspaces/PROJ-1',
        repository_paths=['/x/workspaces/PROJ-1/client'],
        recent_assistant_texts=['I edited line 12'],
        last_user_text='please fix it',
        last_assistant_text='Done — edited line 12 in foo.py',
        claude_session_id='abc12345',
    )
    defaults.update(overrides)
    return ResumePromptInputs(**defaults)


class RenderResumePromptTests(unittest.TestCase):

    def test_header_lists_task_id_summary_branch_workspace(self) -> None:
        out = render_resume_prompt(_inputs())
        self.assertIn('# Resume prompt for PROJ-1', out)
        self.assertIn('**Task**: Fix the typo', out)
        self.assertIn('**Branch**: `feature/proj-1`', out)
        self.assertIn('**Workspace**: `/x/workspaces/PROJ-1`', out)

    def test_claude_session_id_rendered_when_present(self) -> None:
        out = render_resume_prompt(_inputs(claude_session_id='abc12345'))
        self.assertIn('**Claude session id**: `abc12345`', out)

    def test_claude_session_id_omitted_when_empty(self) -> None:
        out = render_resume_prompt(_inputs(claude_session_id=''))
        self.assertNotIn('Claude session id', out)

    def test_repositories_section_lists_every_path(self) -> None:
        out = render_resume_prompt(_inputs(repository_paths=[
            '/x/workspaces/PROJ-1/client',
            '/x/workspaces/PROJ-1/backend',
        ]))
        self.assertIn('/x/workspaces/PROJ-1/client', out)
        self.assertIn('/x/workspaces/PROJ-1/backend', out)

    def test_repositories_section_skipped_when_empty(self) -> None:
        out = render_resume_prompt(_inputs(repository_paths=[]))
        self.assertNotIn('## Repositories in scope', out)

    def test_recent_assistant_texts_become_bullets(self) -> None:
        out = render_resume_prompt(_inputs(recent_assistant_texts=[
            'first edit',
            'second edit',
        ]))
        self.assertIn('first edit', out)
        self.assertIn('second edit', out)
        self.assertIn('What\'s been done so far', out)

    def test_last_user_message_quoted_block(self) -> None:
        out = render_resume_prompt(_inputs(last_user_text='please fix it'))
        self.assertIn('## Last user message', out)
        self.assertIn('> please fix it', out)

    def test_multi_line_user_message_keeps_quote_prefix_per_line(self) -> None:
        out = render_resume_prompt(_inputs(
            last_user_text='line one\nline two',
        ))
        self.assertIn('> line one', out)
        self.assertIn('> line two', out)

    def test_last_assistant_section_present(self) -> None:
        out = render_resume_prompt(_inputs(
            last_assistant_text='Done — edited line 12 in foo.py',
        ))
        self.assertIn('## Last assistant message', out)
        self.assertIn('Done — edited line 12 in foo.py', out)

    def test_continuation_prompt_is_in_a_code_block(self) -> None:
        # The closing block must be a fenced code block so the
        # operator can paste it directly into another AI without
        # markdown rendering corrupting whitespace.
        out = render_resume_prompt(_inputs())
        idx = out.find('## Continue this task')
        self.assertGreater(idx, -1)
        rest = out[idx:]
        self.assertIn('```', rest)
        # Substantive content within the fence.
        self.assertIn('You are picking up task PROJ-1', rest)

    def test_continuation_prompt_references_branch_and_workspace(self) -> None:
        out = render_resume_prompt(_inputs(
            branch_name='feature/X', workspace_path='/ws/X',
        ))
        # Both should appear inside the continuation block.
        self.assertIn('Branch: feature/X', out)
        self.assertIn('Workspace root: /ws/X', out)

    def test_long_assistant_text_is_truncated_with_ellipsis(self) -> None:
        # Output must stay paste-friendly even if Claude dumped a
        # 10k-character analysis.
        out = render_resume_prompt(_inputs(
            last_assistant_text='A' * 5000,
        ))
        # Truncated to roughly 1600 chars (the renderer's cap).
        # Plenty of room under the absolute limit.
        self.assertLess(len(out), 8000)
        self.assertIn('…', out)

    def test_empty_inputs_still_produces_valid_markdown(self) -> None:
        # Operator opens a brand-new task with no turns yet — the
        # file should still be writable and render placeholders.
        out = render_resume_prompt(ResumePromptInputs(
            task_id='', task_summary='', branch_name='',
            workspace_path='', repository_paths=[],
            recent_assistant_texts=[], last_user_text='',
            last_assistant_text='',
        ))
        self.assertIn('# Resume prompt for (unknown)', out)
        self.assertIn('(no summary)', out)
        self.assertIn('(no branch)', out)
        self.assertIn('(no workspace)', out)

    # NOTE: capping the assistant-history bullets to N items lives
    # in ``build_inputs_from_session`` (NOT the renderer) — the
    # renderer is a pure print of what it's given. See the builder
    # tests below for the cap test.


class WriteResumePromptTests(unittest.TestCase):

    def test_writes_file_at_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / 'workspaces' / 'PROJ-1'
            ws.mkdir(parents=True)
            content = '# hello world'
            ok = write_resume_prompt(ws, content)
            self.assertTrue(ok)
            target = ws / RESUME_PROMPT_FILENAME
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(), content)

    def test_creates_parent_directory_if_missing(self) -> None:
        # Operator might invoke the writer for a not-yet-provisioned
        # task; the atomic-text helper should still create the dir.
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / 'never-existed'
            ok = write_resume_prompt(ws, 'hi')
            self.assertTrue(ok)
            self.assertTrue((ws / RESUME_PROMPT_FILENAME).is_file())

    def test_atomic_no_partial_file_on_failure(self) -> None:
        # When the workspace path is a FILE (not a directory), the
        # write fails cleanly — no half-written file lying around.
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / 'blocker'
            blocker.write_text('this is a file, not a directory')
            ok = write_resume_prompt(blocker, 'should fail')
            self.assertFalse(ok)
            # Original blocker file untouched.
            self.assertEqual(
                blocker.read_text(), 'this is a file, not a directory',
            )

    def test_no_op_when_workspace_path_blank(self) -> None:
        self.assertFalse(write_resume_prompt('', 'content'))
        self.assertFalse(write_resume_prompt(None, 'content'))


class BuildInputsFromSessionTests(unittest.TestCase):
    """The adapter that turns ``session.recent_events()`` into renderer inputs."""

    def _event(self, event_type: str, raw: dict):
        return SimpleNamespace(event_type=event_type, raw=raw)

    def test_extracts_assistant_text_from_text_block(self) -> None:
        events = [
            self._event('assistant', {
                'message': {
                    'content': [
                        {'type': 'text', 'text': 'hello from claude'},
                    ],
                },
            }),
        ]
        out = build_inputs_from_session(
            task_id='T1', task_summary='', branch_name='',
            workspace_path='/x', repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.recent_assistant_texts, ['hello from claude'])
        self.assertEqual(out.last_assistant_text, 'hello from claude')

    def test_ignores_assistant_tool_use_blocks(self) -> None:
        # tool_use blocks aren't conversation text; they're tool
        # plumbing and shouldn't pollute the "what's been done"
        # bullets.
        events = [
            self._event('assistant', {
                'message': {
                    'content': [
                        {'type': 'tool_use', 'name': 'Read'},
                    ],
                },
            }),
        ]
        out = build_inputs_from_session(
            task_id='T1', task_summary='', branch_name='',
            workspace_path='/x', repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.recent_assistant_texts, [])

    def test_extracts_user_text_from_string_content(self) -> None:
        events = [
            self._event('user', {
                'message': {'role': 'user', 'content': 'please fix it'},
            }),
        ]
        out = build_inputs_from_session(
            task_id='T1', task_summary='', branch_name='',
            workspace_path='/x', repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.last_user_text, 'please fix it')

    def test_extracts_user_text_from_block_content(self) -> None:
        events = [
            self._event('user', {
                'message': {
                    'role': 'user',
                    'content': [{'type': 'text', 'text': 'block form'}],
                },
            }),
        ]
        out = build_inputs_from_session(
            task_id='T1', task_summary='', branch_name='',
            workspace_path='/x', repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.last_user_text, 'block form')

    def test_handles_empty_events(self) -> None:
        out = build_inputs_from_session(
            task_id='T1', task_summary='', branch_name='',
            workspace_path='/x', repository_paths=[],
            recent_events=[],
        )
        self.assertEqual(out.recent_assistant_texts, [])
        self.assertEqual(out.last_user_text, '')
        self.assertEqual(out.last_assistant_text, '')

    def test_caps_recent_assistant_to_max(self) -> None:
        events = [
            self._event('assistant', {
                'message': {'content': [{'type': 'text', 'text': f't{i}'}]},
            }) for i in range(20)
        ]
        out = build_inputs_from_session(
            task_id='T1', task_summary='', branch_name='',
            workspace_path='/x', repository_paths=[],
            recent_events=events,
            max_recent_assistant=3,
        )
        # Newest 3 (t17, t18, t19) — order preserved.
        self.assertEqual(out.recent_assistant_texts, ['t17', 't18', 't19'])
        self.assertEqual(out.last_assistant_text, 't19')


if __name__ == '__main__':
    unittest.main()
