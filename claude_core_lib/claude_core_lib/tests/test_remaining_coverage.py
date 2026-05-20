"""Final coverage for claude_core_lib gaps."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class OneShotUtilsTests(unittest.TestCase):
    """Coverage for ``helpers/one_shot_utils.py`` (lines 14-84)."""

    def test_claude_one_shot_returns_stdout_on_success(self) -> None:
        from claude_core_lib.claude_core_lib.helpers.one_shot_utils import (
            claude_one_shot,
        )
        completed = MagicMock(returncode=0, stdout='answer text', stderr='')
        with patch('subprocess.run', return_value=completed):
            result = claude_one_shot('prompt', binary='claude', model='haiku')
        self.assertEqual(result, 'answer text')

    def test_claude_one_shot_no_model_omits_model_flag(self) -> None:
        from claude_core_lib.claude_core_lib.helpers.one_shot_utils import (
            claude_one_shot,
        )
        completed = MagicMock(returncode=0, stdout='ok', stderr='')
        with patch('subprocess.run', return_value=completed) as run:
            claude_one_shot('prompt', binary='claude')
        cmd = run.call_args.args[0]
        self.assertNotIn('--model', cmd)

    def test_claude_one_shot_raises_on_timeout(self) -> None:
        from claude_core_lib.claude_core_lib.helpers.one_shot_utils import (
            claude_one_shot, ClaudeOneShotError,
        )
        with patch(
            'subprocess.run',
            side_effect=subprocess.TimeoutExpired(cmd=['claude'], timeout=120),
        ):
            with self.assertRaises(ClaudeOneShotError):
                claude_one_shot('prompt')

    def test_claude_one_shot_raises_on_oserror(self) -> None:
        from claude_core_lib.claude_core_lib.helpers.one_shot_utils import (
            claude_one_shot, ClaudeOneShotError,
        )
        with patch('subprocess.run', side_effect=OSError('claude not found')):
            with self.assertRaises(ClaudeOneShotError):
                claude_one_shot('prompt')

    def test_claude_one_shot_raises_on_nonzero_returncode(self) -> None:
        from claude_core_lib.claude_core_lib.helpers.one_shot_utils import (
            claude_one_shot, ClaudeOneShotError,
        )
        completed = MagicMock(returncode=1, stdout='', stderr='auth error')
        with patch('subprocess.run', return_value=completed):
            with self.assertRaisesRegex(ClaudeOneShotError, 'auth error'):
                claude_one_shot('prompt')

    def test_claude_one_shot_handles_no_stderr_text(self) -> None:
        from claude_core_lib.claude_core_lib.helpers.one_shot_utils import (
            claude_one_shot, ClaudeOneShotError,
        )
        completed = MagicMock(returncode=1, stdout='', stderr='')
        with patch('subprocess.run', return_value=completed):
            with self.assertRaisesRegex(ClaudeOneShotError, '<no stderr>'):
                claude_one_shot('prompt')

    def test_make_claude_one_shot_returns_closure(self) -> None:
        from claude_core_lib.claude_core_lib.helpers.one_shot_utils import (
            make_claude_one_shot,
        )
        closure = make_claude_one_shot(binary='claude', model='haiku')
        completed = MagicMock(returncode=0, stdout='done', stderr='')
        with patch('subprocess.run', return_value=completed):
            result = closure('hello')
        self.assertEqual(result, 'done')


class ReviewCommentsBatchSnippetTests(unittest.TestCase):
    """Lines 367-372 in helpers/agent_prompt_utils.py: when
    review_comment_code_snippet returns a non-empty string, indent it
    and append. Drive this with a real workspace file."""

    def test_renders_indented_snippet_when_workspace_has_file(self) -> None:
        from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
            review_comments_batch_text,
        )
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            target = workspace / 'src' / 'a.py'
            target.parent.mkdir(parents=True)
            target.write_text('\n'.join(f'line {n}' for n in range(1, 11)))
            comment = SimpleNamespace(
                author='reviewer', body='wrong',
                file_path='src/a.py', line_number=5,
                line_type='ADDED', commit_sha='abc',
            )
            text = review_comments_batch_text(
                [comment], workspace_path=str(workspace),
            )
        # Snippet was appended (line 372).
        self.assertIn('→ 5 | line 5', text)


class CliClientBatchReviewAnswerModeTests(unittest.TestCase):
    """Lines 494-569 in cli_client.py: batch review prompt builder
    answer-mode path."""

    def test_fix_mode_batch_prompt_renders(self) -> None:
        # Line 569 (fix-mode batch prompt return).
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
        from provider_client_base.provider_client_base.data.review_comment import (
            ReviewComment,
        )
        comments = [
            ReviewComment(
                pull_request_id='pr-1', comment_id=f'c{i}', author='r',
                body=f'fix {i}', file_path='a.py', line_number=i,
                line_type='', commit_sha='',
            )
            for i in (1, 2)
        ]
        with tempfile.TemporaryDirectory() as td:
            prompt = ClaudeCliClient._build_review_comments_batch_prompt(
                comments, 'feat/x', workspace_path=td, mode='fix',
            )
        self.assertIn('Address the following', prompt)

    def test_answer_mode_batch_prompt_includes_question_instructions(
        self,
    ) -> None:
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
        from provider_client_base.provider_client_base.data.review_comment import (
            ReviewComment,
        )
        comment_a = ReviewComment(
            pull_request_id='pr-1', comment_id='c1', author='reviewer',
            body='what does this do?',
            file_path='src/a.py', line_number=5,
            line_type='', commit_sha='',
        )
        comment_b = ReviewComment(
            pull_request_id='pr-1', comment_id='c2', author='reviewer',
            body='why is this here?',
            file_path='src/b.py', line_number=10,
            line_type='', commit_sha='',
        )
        with tempfile.TemporaryDirectory() as td:
            prompt = ClaudeCliClient._build_review_comments_batch_prompt(
                [comment_a, comment_b], 'feat/x',
                workspace_path=td, mode='answer',
            )
        self.assertIn('QUESTIONS', prompt)
        self.assertIn('Do NOT modify any files', prompt)
        self.assertIn('Number your answers', prompt)


class CliClientSingleReviewAnswerModeTests(unittest.TestCase):
    """Line 642 in cli_client.py: single-comment review prompt builder
    answer-mode entry."""

    def test_answer_mode_single_prompt_renders(self) -> None:
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
        from provider_client_base.provider_client_base.data.review_comment import (
            ReviewComment,
        )
        comment = ReviewComment(
            pull_request_id='pr-1', comment_id='c1', author='reviewer',
            body='question?',
            file_path='src/a.py', line_number=5,
            line_type='', commit_sha='',
        )
        with tempfile.TemporaryDirectory() as td:
            prompt = ClaudeCliClient._build_review_prompt(
                comment, 'feat/x', workspace_path=td, mode='answer',
            )
        self.assertIn('QUESTION', prompt)


class SessionManagerFromConfigTests(unittest.TestCase):
    """Lines 109-115 in session/manager.py: ``from_config`` happy +
    early returns."""

    def test_from_config_returns_none_for_non_claude_backend(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        result = ClaudeSessionManager.from_config(
            open_cfg=SimpleNamespace(), agent_backend='openhands',
        )
        self.assertIsNone(result)

    def test_from_config_uses_env_state_dir_when_set(self) -> None:
        # Line 112: env var override.
        import os
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(
                os.environ, {'KATO_SESSION_STATE_DIR': td}, clear=False,
            ):
                result = ClaudeSessionManager.from_config(
                    open_cfg=SimpleNamespace(), agent_backend='claude',
                )
        self.assertIsNotNone(result)
        self.assertEqual(str(result._state_dir), td)


class SessionManagerSetDoneCallbackTests(unittest.TestCase):
    """Line 150: ``_done_callback = callback`` setter."""

    def test_set_done_callback(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as td:
            manager = ClaudeSessionManager(state_dir=td)
            callback = MagicMock()
            manager.set_done_callback(callback)
        self.assertIs(manager._done_callback, callback)


class SessionManagerLoadPersistedRecordsTests(unittest.TestCase):
    """Lines 724-725: ``active`` records on disk are demoted to
    ``terminated`` on startup."""

    def test_active_record_is_marked_terminated_on_load(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
            SESSION_STATUS_ACTIVE,
            SESSION_STATUS_TERMINATED,
        )
        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td)
            record_payload = {
                'task_id': 'T1',
                'task_summary': 'fix bug',
                'cwd': '/tmp',
                'claude_session_id': 'sess-1',
                'status': SESSION_STATUS_ACTIVE,
                'updated_at_epoch': 1000.0,
                'created_at_epoch': 1000.0,
            }
            (state_dir / 'T1.json').write_text(json.dumps(record_payload))
            manager = ClaudeSessionManager(state_dir=str(state_dir))
        # Active → terminated after load. The dict key is lowercased
        # so that case-mismatched lookups still find the same record.
        record = manager._records.get(manager._lookup_key('T1'))
        self.assertIsNotNone(record)
        self.assertEqual(record.status, SESSION_STATUS_TERMINATED)

    def test_load_skips_unreadable_records(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td)
            (state_dir / 'bad.json').write_text('not json at all')
            # Should not raise; bad record is skipped + logged.
            manager = ClaudeSessionManager(state_dir=str(state_dir))
        self.assertEqual(manager._records, {})


if __name__ == '__main__':
    unittest.main()
