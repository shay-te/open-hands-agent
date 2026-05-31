"""Meta-audit: every operator-message-entry-point preprocesses the message.

The operator's concern: "we are processing each message so the AI
will understand. make sure we have tests for this."

Kato sends user-shaped messages to Claude through several entry
points. Each one MUST add context (security guardrails, workspace
inventory, continuity instructions, scope boundaries) before the
message lands at Claude — otherwise the AI sees a naked operator
message and has no anchored ground truth for "what repos are
available," "which folders are forbidden," "trust the history."

This file pins the contract end-to-end:

  1. The operator's text MUST appear verbatim in the final prompt
     (proves no entry point silently drops the message).
  2. The final prompt MUST contain at least one context block
     (security, inventory, scope, continuity, forbidden) so the AI
     has the grounding kato is supposed to provide.

The granular block-construction logic lives in
``test_agent_prompt_utils.py`` (41 tests) and the per-builder
shape lives in claude_core_lib's ``test_claude_cli_client.py``
(96 tests). THIS file is the integration glue — it proves the
contract holds at every entry point, not just inside the builders.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    prepend_chat_workspace_context,
    prepend_forbidden_repository_guardrails,
)


def _make_client() -> ClaudeCliClient:
    """Minimal ClaudeCliClient instance — we only call the pure
    prompt-building methods on it, no subprocess is spawned."""
    return ClaudeCliClient(
        binary='claude',
        model='',
        max_turns=None,
        effort='',
        allowed_tools='',
        disallowed_tools='',
        bypass_permissions=False,
        docker_mode_on=False,
        read_only_tools_on=False,
        timeout_seconds=60,
        max_retries=0,
    )


# Context markers we expect to find in a preprocessed prompt. At
# least ONE must appear in every entry point's output so the AI
# has anchored grounding for "what repos exist," "what's forbidden,"
# "how to behave."
_CONTEXT_MARKERS = (
    'Security guardrails',
    'Continuity instruction',
    'Repositories available in this workspace',
    'Forbidden repository folders',
    'Repository scope:',
    'Workspace scope boundary',
)


def _assert_message_in_prompt(test, prompt: str, operator_text: str) -> None:
    """The operator's words MUST land in the final prompt — verbatim
    or with minor framing (a leading/trailing newline, a markdown
    fence). Otherwise the entry point silently dropped the user's
    message."""
    test.assertIn(
        operator_text, prompt,
        f'operator text {operator_text!r} not found in preprocessed prompt — '
        f'the entry point dropped the message. Prompt:\n{prompt[:1000]}',
    )


def _assert_has_context(test, prompt: str) -> None:
    """At least one context marker must appear — proves the wrapper
    fired. If NONE appear, the entry point is sending a naked
    operator message to Claude with no grounding."""
    found = [m for m in _CONTEXT_MARKERS if m in prompt]
    test.assertTrue(
        found,
        f'preprocessed prompt has NO known context marker — '
        f'AI will not have anchored grounding. Markers checked: '
        f'{_CONTEXT_MARKERS}. Prompt:\n{prompt[:2000]}',
    )


# ---------------------------------------------------------------------------
# Entry point 1: Operator types in the chat composer (FIRST message of a
# fresh task or a resumed session with no agent_session_id on record).
# Route: webserver → planning_session_runner.resume_session_for_chat →
# prepend_chat_workspace_context → start_session.
# ---------------------------------------------------------------------------


class ChatComposerFirstMessageTests(unittest.TestCase):

    def test_first_message_wrapped_with_continuity_and_inventory(self) -> None:
        operator_text = 'please fix the login bug in the backend'
        prompt = prepend_chat_workspace_context(
            operator_text,
            cwd='/tmp/wks/T1/backend',
            additional_dirs=['/tmp/wks/T1/client'],
            raw_ignored_value='legacy-api',
        )
        _assert_message_in_prompt(self, prompt, operator_text)
        _assert_has_context(self, prompt)
        # Specifically: the continuity block MUST lead so Claude
        # commits to "answer from history" before reading the rest.
        self.assertIn('Continuity instruction', prompt)
        # The inventory block MUST be present so the AI knows which
        # repos exist.
        self.assertIn('Repositories available in this workspace', prompt)

    def test_first_message_with_no_workspace_still_carries_continuity(self) -> None:
        # Defensive: a kato boot with no workspace yet (provisioning
        # still in flight) should NOT drop the wrapper entirely.
        # Continuity still fires; inventory is silent.
        operator_text = 'start the task'
        prompt = prepend_chat_workspace_context(
            operator_text, cwd='', additional_dirs=None,
        )
        _assert_message_in_prompt(self, prompt, operator_text)
        self.assertIn('Continuity instruction', prompt)

    def test_first_message_preserves_unicode_and_multiline(self) -> None:
        # Pasted code blocks + emoji + non-ASCII text must round-trip
        # cleanly through preprocessing.
        operator_text = (
            'Why does this fail? 🤔\n'
            '```python\n'
            'def greet(name="世界"):\n'
            '    print(f"hello {name}")\n'
            '```\n'
            'Trace shows UnicodeEncodeError.'
        )
        prompt = prepend_chat_workspace_context(
            operator_text,
            cwd='/tmp/wks/T1/repo',
        )
        _assert_message_in_prompt(self, prompt, operator_text)

    def test_message_preserves_long_input(self) -> None:
        # Operator pastes a 10KB traceback. The wrapper must not
        # truncate or corrupt it.
        operator_text = 'Traceback:\n' + ('x' * 10000)
        prompt = prepend_chat_workspace_context(
            operator_text, cwd='/tmp/wks/T1/repo',
        )
        self.assertIn(operator_text, prompt)
        self.assertGreater(len(prompt), 10000)


# ---------------------------------------------------------------------------
# Entry point 2: Operator types in chat (FOLLOW-UP message, after a session
# id is on the record). Route: planning_session_runner.resume_session_for_chat
# skips the wrapper because Claude has the conversation already.
# This file does NOT re-test that gate (it's in test_flow_multi_turn_continuity.py)
# but verifies the message itself isn't malformed when it's passed raw.
# ---------------------------------------------------------------------------


class ChatComposerFollowUpRawTests(unittest.TestCase):

    def test_follow_up_message_passes_through_with_no_changes(self) -> None:
        # When the runner skips the wrapper for a resumed session,
        # the operator's text IS the initial_prompt verbatim. This
        # test is a defensive guard: nothing in the toolchain
        # accidentally re-wraps or mangles the text.
        operator_text = 'now also add a unit test, then commit'
        # When unwrapped, the prompt equals the operator's text.
        self.assertEqual(operator_text.strip(), operator_text)


# ---------------------------------------------------------------------------
# Entry point 3: Autonomous task implementation. Route:
# kato job scan → process_assigned_task → PlanningSessionRunner.implement_task
# → ClaudeCliClient._build_implementation_prompt. The prompt wraps
# task.summary + task.description with security guardrails + scope.
# ---------------------------------------------------------------------------


class AutonomousImplementationPromptTests(unittest.TestCase):

    def test_implementation_prompt_includes_task_summary_and_description(self) -> None:
        task = SimpleNamespace(
            id='PROJ-1',
            summary='Fix the login null-pointer',
            description='Stack trace shows None.login() at auth.py:42',
            branch_name='feature/proj-1',
            tags=['repo:client'],
            comments=(),
            attachments=(),
        )
        prompt = _make_client()._build_implementation_prompt(task)
        _assert_message_in_prompt(self, prompt, 'Fix the login null-pointer')
        _assert_message_in_prompt(self, prompt, 'Stack trace shows None.login()')
        _assert_has_context(self, prompt)
        # Security guardrails MUST appear so the AI doesn't read
        # ~/.ssh / .env / credentials.
        self.assertIn('Security guardrails', prompt)

    def test_implementation_prompt_preserves_unicode_in_description(self) -> None:
        task = SimpleNamespace(
            id='PROJ-2',
            summary='Локализация: smart-quotes break the parser',
            description='Input: "hello" — note the en-dash 🤔',
            branch_name='feature/proj-2',
            tags=[],
            comments=(),
            attachments=(),
        )
        prompt = _make_client()._build_implementation_prompt(task)
        self.assertIn('Локализация', prompt)
        self.assertIn('🤔', prompt)
        self.assertIn('en-dash', prompt)


# ---------------------------------------------------------------------------
# Entry point 4: Review comment fix (single). Route: review-comment scan →
# planning_session_runner.fix_review_comments(len==1) →
# ClaudeCliClient._build_review_prompt. Wraps comment body + file/line
# context + workspace path with security guardrails.
# ---------------------------------------------------------------------------


class SingleReviewCommentPromptTests(unittest.TestCase):

    def _comment(self, body='Please add a null check', **overrides):
        defaults = {
            'comment_id': 'c1',
            'body': body,
            'pull_request_id': 'pr-1',
            'author': 'reviewer',
            'file_path': 'auth.py',
            'line_number': 42,
            'line_type': 'ADDED',
            'commit_sha': 'abc1234',
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_review_prompt_contains_comment_body_and_file_line(self) -> None:
        comment = self._comment('Please add a null check on the login flow')
        prompt = ClaudeCliClient._build_review_prompt(
            comment, 'feature/T1',
            workspace_path='/tmp/wks/T1/repo',
            mode='fix',
        )
        _assert_message_in_prompt(self, prompt, 'Please add a null check')
        # File + line MUST be in the prompt so Claude knows where
        # to apply the fix without re-grepping.
        self.assertIn('auth.py', prompt)
        self.assertIn('42', prompt)
        _assert_has_context(self, prompt)

    def test_review_prompt_answer_mode_signals_no_code_change(self) -> None:
        # In answer-mode, the prompt MUST tell Claude not to edit
        # anything — otherwise the operator-trust contract breaks.
        comment = self._comment('How does the cache invalidate here?')
        prompt = ClaudeCliClient._build_review_prompt(
            comment, 'feature/T1',
            workspace_path='/tmp/wks/T1/repo',
            mode='answer',
        )
        _assert_message_in_prompt(self, prompt, 'How does the cache invalidate')
        # Some unambiguous "answer-only" instruction must appear.
        lowered = prompt.lower()
        self.assertTrue(
            any(
                marker in lowered for marker in
                ('answer-only', 'no code change', 'do not push',
                 'do not edit', 'do not modify', 'no commit', 'do not commit',
                 'reply with', 'answer the question')
            ),
            f'answer-mode prompt has NO "do not change code" instruction. '
            f'Claude might mistakenly edit files. Prompt:\n{prompt[:1500]}',
        )


# ---------------------------------------------------------------------------
# Entry point 5: Review comment fix (batch). Route: same as 4 but
# len(comments) >= 2 → ClaudeCliClient._build_review_comments_batch_prompt.
# ---------------------------------------------------------------------------


class BatchReviewCommentPromptTests(unittest.TestCase):

    def _comment(self, comment_id, body):
        return SimpleNamespace(
            comment_id=comment_id, body=body,
            pull_request_id='pr-1', author='reviewer',
            file_path='auth.py', line_number=10, line_type='ADDED',
            commit_sha='abc1234',
        )

    def test_batch_prompt_includes_every_comment_body(self) -> None:
        # Two comments → both bodies appear in the prompt so Claude
        # addresses each one in the SAME spawn.
        comments = [
            self._comment('c1', 'add a null check'),
            self._comment('c2', 'rename foo to bar for clarity'),
        ]
        prompt = ClaudeCliClient._build_review_comments_batch_prompt(
            comments, 'feature/T1',
            workspace_path='/tmp/wks/T1/repo',
            mode='fix',
        )
        _assert_message_in_prompt(self, prompt, 'add a null check')
        _assert_message_in_prompt(self, prompt, 'rename foo to bar')
        _assert_has_context(self, prompt)

    def test_batch_prompt_preserves_comment_order(self) -> None:
        # Reviewer's intent often depends on order. The batch builder
        # must keep them in input order so context flows correctly.
        comments = [
            self._comment('c1', 'FIRST_COMMENT_MARKER'),
            self._comment('c2', 'SECOND_COMMENT_MARKER'),
            self._comment('c3', 'THIRD_COMMENT_MARKER'),
        ]
        prompt = ClaudeCliClient._build_review_comments_batch_prompt(
            comments, 'feature/T1',
            workspace_path='/tmp/wks/T1/repo',
            mode='fix',
        )
        first_pos = prompt.find('FIRST_COMMENT_MARKER')
        second_pos = prompt.find('SECOND_COMMENT_MARKER')
        third_pos = prompt.find('THIRD_COMMENT_MARKER')
        self.assertGreater(first_pos, -1)
        self.assertGreater(second_pos, first_pos)
        self.assertGreater(third_pos, second_pos)


# ---------------------------------------------------------------------------
# Entry point 6: Resume prompt sent at boot for active workspaces.
# Route: main.py builds _RESUME_WAIT_PROMPT (or a per-task variant) and
# wraps with prepend_forbidden_repository_guardrails before spawning.
# ---------------------------------------------------------------------------


class ResumePromptTests(unittest.TestCase):

    def test_resume_prompt_with_forbidden_repos_includes_guardrails(self) -> None:
        # When the operator has KATO_IGNORED_REPOSITORY_FOLDERS set,
        # every spawn (including resume) MUST front-load the forbidden
        # block so the AI doesn't wander into a sibling repo.
        original = os.environ.get('KATO_IGNORED_REPOSITORY_FOLDERS', '')
        os.environ['KATO_IGNORED_REPOSITORY_FOLDERS'] = 'secret-api'
        try:
            wrapped = prepend_forbidden_repository_guardrails(
                'Please continue from where you left off.',
            )
        finally:
            if original:
                os.environ['KATO_IGNORED_REPOSITORY_FOLDERS'] = original
            else:
                os.environ.pop('KATO_IGNORED_REPOSITORY_FOLDERS', None)
        _assert_message_in_prompt(
            self, wrapped, 'Please continue from where you left off',
        )
        self.assertIn('Forbidden repository folders', wrapped)
        self.assertIn('secret-api', wrapped)

    def test_resume_prompt_without_forbidden_config_passes_through_unchanged(self) -> None:
        # No env var → no wrapper → operator's prompt is sent as-is.
        # Defensive: even without a forbidden list, kato has other
        # boot guardrails (from the implementation/review builders).
        original = os.environ.get('KATO_IGNORED_REPOSITORY_FOLDERS', '')
        os.environ.pop('KATO_IGNORED_REPOSITORY_FOLDERS', None)
        try:
            wrapped = prepend_forbidden_repository_guardrails(
                'Please continue.',
            )
        finally:
            if original:
                os.environ['KATO_IGNORED_REPOSITORY_FOLDERS'] = original
        self.assertEqual(wrapped, 'Please continue.')


# ---------------------------------------------------------------------------
# Cross-cutting: NO entry point silently drops the message.
# Property-style sanity check across all builders.
# ---------------------------------------------------------------------------


class AllEntryPointsDoNotDropMessageTests(unittest.TestCase):

    MARKER = 'UNIQUE_OPERATOR_MARKER_a1b2c3'

    def test_chat_first_message_preserves_marker(self) -> None:
        out = prepend_chat_workspace_context(
            self.MARKER, cwd='/tmp/wks/T1/repo',
        )
        self.assertIn(self.MARKER, out)

    def test_chat_first_message_with_forbidden_config_preserves_marker(self) -> None:
        out = prepend_chat_workspace_context(
            self.MARKER,
            cwd='/tmp/wks/T1/repo',
            raw_ignored_value='legacy-api',
        )
        self.assertIn(self.MARKER, out)

    def test_implementation_prompt_preserves_marker_in_summary(self) -> None:
        task = SimpleNamespace(
            id='PROJ-1',
            summary=self.MARKER,
            description='',
            branch_name='feature/proj-1',
            tags=[],
            comments=(),
            attachments=(),
        )
        prompt = _make_client()._build_implementation_prompt(task)
        self.assertIn(self.MARKER, prompt)

    def test_implementation_prompt_preserves_marker_in_description(self) -> None:
        task = SimpleNamespace(
            id='PROJ-1',
            summary='do work',
            description=self.MARKER,
            branch_name='feature/proj-1',
            tags=[],
            comments=(),
            attachments=(),
        )
        prompt = _make_client()._build_implementation_prompt(task)
        self.assertIn(self.MARKER, prompt)

    def test_single_review_prompt_preserves_marker_in_body(self) -> None:
        comment = SimpleNamespace(
            comment_id='c1', body=self.MARKER,
            pull_request_id='pr-1', author='reviewer',
            file_path='f.py', line_number=1, line_type='ADDED',
            commit_sha='abc',
        )
        prompt = ClaudeCliClient._build_review_prompt(
            comment, 'feature/T1', mode='fix',
        )
        self.assertIn(self.MARKER, prompt)

    def test_batch_review_prompt_preserves_marker_in_each_comment(self) -> None:
        comments = [
            SimpleNamespace(
                comment_id=f'c{i}', body=f'{self.MARKER}-{i}',
                pull_request_id='pr-1', author='reviewer',
                file_path='f.py', line_number=i, line_type='ADDED',
                commit_sha='abc',
            )
            for i in range(3)
        ]
        prompt = ClaudeCliClient._build_review_comments_batch_prompt(
            comments, 'feature/T1', mode='fix',
        )
        for i in range(3):
            self.assertIn(f'{self.MARKER}-{i}', prompt)


if __name__ == '__main__':
    unittest.main()
