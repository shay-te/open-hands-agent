"""Tests for the generic resume-prompt renderer + session adapter.

The renderer + ``build_inputs_from_session`` adapter are pure and
provider-agnostic; tested here in isolation. The host-specific atomic
writer (where/when the snapshot is persisted) is tested in the host's
own suite.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.resume_prompt_utils import (
    ResumePromptInputs,
    build_inputs_from_session,
    render_resume_prompt,
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
        agent_session_id='abc12345',
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

    def test_agent_session_id_rendered_when_present(self) -> None:
        out = render_resume_prompt(_inputs(agent_session_id='abc12345'))
        self.assertIn('**Agent session id**: `abc12345`', out)

    def test_agent_session_id_omitted_when_empty(self) -> None:
        out = render_resume_prompt(_inputs(agent_session_id=''))
        self.assertNotIn('Agent session id', out)

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
        # Output must stay paste-friendly even if the agent dumped a
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


class ExtractTextDefensiveTests(unittest.TestCase):
    """Coverage for the ``_extract_assistant_text`` / ``_extract_user_text``
    type-narrowing guards. Each event-envelope variant exercises one
    branch — agent wire formats are permissive enough that all of these
    have shown up in real captures, so they are NOT unreachable."""

    def _e(self, event_type: str, raw):
        return SimpleNamespace(event_type=event_type, raw=raw)

    def test_assistant_event_with_non_dict_message_returns_empty(self) -> None:
        # ``raw['message']`` is not a dict (e.g. None or a list).
        events = [
            self._e('assistant', {'message': None}),
            self._e('assistant', {'message': 'a string, not a dict'}),
            self._e('assistant', {'message': ['list', 'instead']}),
        ]
        out = build_inputs_from_session(
            task_id='T1', task_summary='', branch_name='',
            workspace_path='/x', repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.recent_assistant_texts, [])
        self.assertEqual(out.last_assistant_text, '')

    def test_assistant_event_with_non_list_content_returns_empty(self) -> None:
        # ``message['content']`` is not a list (e.g. string, dict, None).
        events = [
            self._e('assistant', {'message': {'content': 'string content'}}),
            self._e('assistant', {'message': {'content': {'block': 1}}}),
            self._e('assistant', {'message': {'content': None}}),
            self._e('assistant', {'message': {}}),  # content missing entirely
        ]
        out = build_inputs_from_session(
            task_id='T1', task_summary='', branch_name='',
            workspace_path='/x', repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.recent_assistant_texts, [])

    def test_assistant_event_skips_non_dict_blocks(self) -> None:
        # A content block that's not a dict (e.g. a stray string in the
        # list) is skipped, but valid blocks alongside it are extracted.
        events = [
            self._e('assistant', {
                'message': {
                    'content': [
                        'stray string',
                        None,
                        ['nested list'],
                        {'type': 'text', 'text': 'valid text'},
                    ],
                },
            }),
        ]
        out = build_inputs_from_session(
            task_id='T1', task_summary='', branch_name='',
            workspace_path='/x', repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.recent_assistant_texts, ['valid text'])
        self.assertEqual(out.last_assistant_text, 'valid text')

    def test_user_event_with_non_dict_message_returns_empty(self) -> None:
        # ``raw['message']`` is not a dict.
        events = [
            self._e('user', {'message': None}),
            self._e('user', {'message': 'plain string'}),
            self._e('user', {}),  # 'message' key missing
        ]
        out = build_inputs_from_session(
            task_id='T1', task_summary='', branch_name='',
            workspace_path='/x', repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.last_user_text, '')

    def test_user_event_with_non_list_non_string_content_returns_empty(self) -> None:
        # ``content`` is neither a string nor a list.
        events = [
            self._e('user', {'message': {'role': 'user', 'content': None}}),
            self._e('user', {'message': {'role': 'user', 'content': {'a': 1}}}),
            self._e('user', {'message': {'role': 'user', 'content': 42}}),
        ]
        out = build_inputs_from_session(
            task_id='T1', task_summary='', branch_name='',
            workspace_path='/x', repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.last_user_text, '')

    def test_user_event_skips_non_dict_blocks(self) -> None:
        # Non-dict block entries are skipped, but valid text blocks
        # alongside them are still extracted.
        events = [
            self._e('user', {
                'message': {
                    'role': 'user',
                    'content': [
                        'stray',
                        None,
                        42,
                        {'type': 'text', 'text': 'real text'},
                    ],
                },
            }),
        ]
        out = build_inputs_from_session(
            task_id='T1', task_summary='', branch_name='',
            workspace_path='/x', repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.last_user_text, 'real text')


if __name__ == '__main__':
    unittest.main()
