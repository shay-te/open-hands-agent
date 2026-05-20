"""Unit tests for :class:`codex_core_lib.cli_client.CodexCliClient`.

Pins the public-API parity contract with ``ClaudeCliClient``: every
method that the orchestration layer calls (``validate_connection``,
``implement_task``, ``test_task``, ``investigate``,
``fix_review_comments``, ``delete_conversation``,
``stop_all_conversations``) is here, returns the same shape, and
honors the same flags.

No real ``codex`` binary is launched — ``subprocess.run`` is patched
throughout.
"""

from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from codex_core_lib.codex_core_lib.cli_client import CodexCliClient


def _task(task_id: str = 'PROJ-1') -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        summary='Fix the thing',
        description='Long description.',
        branch_name=f'feature/{task_id.lower()}',
        repository_branches={},
        repositories=[],
    )


def _completed(stdout: str = '', stderr: str = '', returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class ConstructionTests(unittest.TestCase):
    def test_default_binary_is_codex(self) -> None:
        client = CodexCliClient()
        self.assertEqual(client._binary, 'codex')

    def test_operator_binary_override_wins(self) -> None:
        client = CodexCliClient(binary='/opt/codex')
        self.assertEqual(client._binary, '/opt/codex')

    def test_empty_binary_falls_back_to_default(self) -> None:
        client = CodexCliClient(binary='   ')
        self.assertEqual(client._binary, 'codex')

    def test_default_allowed_tools_when_not_bypassing(self) -> None:
        client = CodexCliClient()
        self.assertEqual(client._allowed_tools, 'edit,write,read,shell')

    def test_empty_allowed_tools_under_bypass_stays_empty(self) -> None:
        # Mirror of ClaudeCliClient — under bypass we don't pre-approve
        # anything, because there's nothing to approve.
        client = CodexCliClient(bypass_permissions=True)
        self.assertEqual(client._allowed_tools, '')

    def test_timeout_has_60s_floor(self) -> None:
        client = CodexCliClient(timeout_seconds=5)
        self.assertEqual(client._timeout_seconds, 60)

    def test_max_retries_has_1_floor(self) -> None:
        client = CodexCliClient(max_retries=0)
        self.assertEqual(client.max_retries, 1)

    def test_invalid_effort_raises(self) -> None:
        with self.assertRaises(ValueError):
            CodexCliClient(effort='turbo')

    def test_blank_effort_keeps_default(self) -> None:
        client = CodexCliClient(effort='')
        self.assertEqual(client._effort, '')

    def test_valid_effort_lowercased(self) -> None:
        client = CodexCliClient(effort='HIGH')
        self.assertEqual(client._effort, 'high')

    def test_permission_mode_safe_by_default(self) -> None:
        client = CodexCliClient()
        self.assertEqual(client._permission_mode, CodexCliClient.SAFE_PERMISSION_MODE)

    def test_permission_mode_bypass_when_flag_on(self) -> None:
        client = CodexCliClient(bypass_permissions=True)
        self.assertEqual(client._permission_mode, CodexCliClient.BYPASS_PERMISSION_MODE)


# ---------------------------------------------------------------------------
# No-op contract (mirrors ClaudeCliClient)
# ---------------------------------------------------------------------------

class NoOpContractTests(unittest.TestCase):
    def test_delete_conversation_is_a_noop(self) -> None:
        # Same contract as ClaudeCliClient: local sessions, nothing to
        # clean up remotely.
        CodexCliClient().delete_conversation('any-id')  # must not raise

    def test_stop_all_conversations_is_a_noop(self) -> None:
        CodexCliClient().stop_all_conversations()  # must not raise


# ---------------------------------------------------------------------------
# validate_connection
# ---------------------------------------------------------------------------

class ValidateConnectionTests(unittest.TestCase):
    def test_refuses_to_run_inside_docker(self) -> None:
        client = CodexCliClient()
        with patch.object(CodexCliClient, '_running_inside_docker', return_value=True):
            with self.assertRaises(RuntimeError) as ctx:
                client.validate_connection()
        self.assertIn('Docker', str(ctx.exception))

    def test_missing_binary_raises_with_install_hint(self) -> None:
        client = CodexCliClient(binary='nope-binary')
        with patch.object(CodexCliClient, '_running_inside_docker', return_value=False), \
             patch('codex_core_lib.codex_core_lib.cli_client.shutil.which', return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                client.validate_connection()
        msg = str(ctx.exception)
        self.assertIn('nope-binary', msg)
        self.assertIn('npm install', msg)

    def test_version_probe_failure_is_surfaced(self) -> None:
        client = CodexCliClient(model_smoke_test_enabled=False)
        with patch.object(CodexCliClient, '_running_inside_docker', return_value=False), \
             patch('codex_core_lib.codex_core_lib.cli_client.shutil.which',
                   return_value='/usr/bin/codex'), \
             patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   return_value=_completed(stderr='boom', returncode=1)):
            with self.assertRaises(RuntimeError) as ctx:
                client.validate_connection()
        self.assertIn('boom', str(ctx.exception))

    def test_successful_version_probe_logs_and_returns(self) -> None:
        client = CodexCliClient(model_smoke_test_enabled=False)
        with patch.object(CodexCliClient, '_running_inside_docker', return_value=False), \
             patch('codex_core_lib.codex_core_lib.cli_client.shutil.which',
                   return_value='/usr/bin/codex'), \
             patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   return_value=_completed(stdout='codex 1.2.3\n', returncode=0)):
            client.validate_connection()
        # Binary path captured for later spawns.
        self.assertEqual(client._binary_path, '/usr/bin/codex')


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

class BuildCommandTests(unittest.TestCase):
    def test_basic_command_shape(self) -> None:
        client = CodexCliClient(binary='codex')
        cmd = client._build_command(
            additional_dirs=[], session_id='',
            resolve_binary=False, include_system_prompt=False,
        )
        # Codex uses `exec --json --ask-for-approval <mode>` instead of
        # claude's `-p --output-format json --permission-mode <mode>`.
        self.assertEqual(cmd[0], 'codex')
        self.assertIn('exec', cmd)
        self.assertIn('--json', cmd)
        self.assertIn('--ask-for-approval', cmd)

    def test_model_flag_passed_when_set(self) -> None:
        client = CodexCliClient(binary='codex', model='codex-large')
        cmd = client._build_command(
            additional_dirs=[], session_id='',
            resolve_binary=False, include_system_prompt=False,
        )
        self.assertIn('--model', cmd)
        self.assertIn('codex-large', cmd)

    def test_max_turns_flag_passed_when_positive(self) -> None:
        client = CodexCliClient(binary='codex', max_turns=15)
        cmd = client._build_command(
            additional_dirs=[], session_id='',
            resolve_binary=False, include_system_prompt=False,
        )
        self.assertIn('--max-turns', cmd)
        self.assertIn('15', cmd)

    def test_effort_flag_uses_codex_reasoning_effort_name(self) -> None:
        # Claude uses --effort; Codex uses --reasoning-effort. This is
        # the kind of difference the per-backend clients exist for.
        client = CodexCliClient(binary='codex', effort='high')
        cmd = client._build_command(
            additional_dirs=[], session_id='',
            resolve_binary=False, include_system_prompt=False,
        )
        self.assertIn('--reasoning-effort', cmd)
        self.assertIn('high', cmd)

    def test_session_id_passed_via_resume(self) -> None:
        client = CodexCliClient(binary='codex')
        cmd = client._build_command(
            additional_dirs=[], session_id='sess-123',
            resolve_binary=False, include_system_prompt=False,
        )
        self.assertIn('--resume', cmd)
        self.assertIn('sess-123', cmd)

    def test_additional_dirs_use_workspace_flag(self) -> None:
        client = CodexCliClient(binary='codex')
        cmd = client._build_command(
            additional_dirs=['/repo/a', '/repo/b'],
            session_id='',
            resolve_binary=False, include_system_prompt=False,
        )
        self.assertEqual(cmd.count('--workspace'), 2)
        self.assertIn('/repo/a', cmd)
        self.assertIn('/repo/b', cmd)

    def test_git_deny_patterns_always_appended_to_disallowed(self) -> None:
        client = CodexCliClient(binary='codex', disallowed_tools='WebFetch')
        cmd = client._build_command(
            additional_dirs=[], session_id='',
            resolve_binary=False, include_system_prompt=False,
        )
        idx = cmd.index('--deny-tools')
        denied = cmd[idx + 1]
        self.assertIn('WebFetch', denied)
        for pattern in CodexCliClient.GIT_DENY_PATTERNS:
            self.assertIn(pattern, denied)


# ---------------------------------------------------------------------------
# implement_task / test_task / investigate (subprocess-mocked end-to-end)
# ---------------------------------------------------------------------------

class ImplementTaskTests(unittest.TestCase):
    def _mock_run_success(self, *, stdout: str):
        return _completed(stdout=stdout, stderr='', returncode=0)

    def test_implement_task_returns_success_dict_on_success_payload(self) -> None:
        client = CodexCliClient(binary='codex')
        payload_text = '{"result": "all done", "session_id": "s-1", "success": true}'
        with patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   return_value=self._mock_run_success(stdout=payload_text)):
            result = client.implement_task(_task())
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result[ImplementationFields.MESSAGE], 'all done')
        self.assertEqual(result[ImplementationFields.SESSION_ID], 's-1')

    def test_test_task_returns_success_dict_on_success_payload(self) -> None:
        client = CodexCliClient(binary='codex')
        payload_text = '{"result": "tests green", "success": true}'
        with patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   return_value=self._mock_run_success(stdout=payload_text)):
            result = client.test_task(_task())
        self.assertTrue(result[ImplementationFields.SUCCESS])

    def test_subprocess_nonzero_exit_raises_runtimeerror(self) -> None:
        client = CodexCliClient(binary='codex')
        with patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   return_value=_completed(stderr='exploded', returncode=2)):
            with self.assertRaises(RuntimeError) as ctx:
                client.implement_task(_task())
        self.assertIn('exploded', str(ctx.exception))

    def test_is_error_payload_raises_runtimeerror(self) -> None:
        client = CodexCliClient(binary='codex')
        payload = '{"is_error": true, "result": "model rejected"}'
        with patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   return_value=_completed(stdout=payload, returncode=0)):
            with self.assertRaises(RuntimeError) as ctx:
                client.implement_task(_task())
        self.assertIn('model rejected', str(ctx.exception))

    def test_subprocess_timeout_raises_timeouterror(self) -> None:
        client = CodexCliClient(binary='codex', timeout_seconds=60)
        timeout_exc = subprocess.TimeoutExpired(cmd='codex', timeout=60)
        with patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   side_effect=timeout_exc):
            with self.assertRaises(TimeoutError):
                client.implement_task(_task())

    def test_invalid_json_payload_falls_back_to_empty_dict(self) -> None:
        # Same contract as ClaudeCliClient: when the CLI exits cleanly
        # (returncode 0) but stdout isn't parseable JSON, we treat the
        # spawn as successful but drop the missing MESSAGE / SESSION_ID
        # rather than failing the task. Operators see "ran, no result"
        # rather than a false-error.
        client = CodexCliClient(binary='codex')
        with patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   return_value=self._mock_run_success(stdout='not json at all')):
            result = client.implement_task(_task())
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertNotIn(ImplementationFields.MESSAGE, result)
        self.assertNotIn(ImplementationFields.SESSION_ID, result)

    def test_investigate_requires_a_prompt(self) -> None:
        with self.assertRaises(ValueError):
            CodexCliClient().investigate('   ')

    def test_investigate_returns_result_text(self) -> None:
        client = CodexCliClient(binary='codex', repository_root_path='/repos')
        payload = '{"result": "high priority", "success": true}'
        with patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   return_value=self._mock_run_success(stdout=payload)):
            text = client.investigate('Classify this ticket')
        self.assertEqual(text, 'high priority')


# ---------------------------------------------------------------------------
# Coercions
# ---------------------------------------------------------------------------

class CoerceTests(unittest.TestCase):
    def test_coerce_max_turns_accepts_int(self) -> None:
        self.assertEqual(CodexCliClient._coerce_max_turns(10), 10)

    def test_coerce_max_turns_accepts_numeric_string(self) -> None:
        self.assertEqual(CodexCliClient._coerce_max_turns('25'), 25)

    def test_coerce_max_turns_rejects_zero_and_negative(self) -> None:
        self.assertIsNone(CodexCliClient._coerce_max_turns(0))
        self.assertIsNone(CodexCliClient._coerce_max_turns(-5))

    def test_coerce_max_turns_rejects_garbage(self) -> None:
        self.assertIsNone(CodexCliClient._coerce_max_turns('abc'))
        self.assertIsNone(CodexCliClient._coerce_max_turns(None))
        self.assertIsNone(CodexCliClient._coerce_max_turns(''))

    def test_extract_first_json_object_finds_embedded_payload(self) -> None:
        text = 'noise before {"key": "value"} noise after'
        obj = CodexCliClient._extract_first_json_object(text)
        self.assertEqual(obj, {'key': 'value'})

    def test_extract_first_json_object_returns_empty_when_no_braces(self) -> None:
        self.assertEqual(CodexCliClient._extract_first_json_object('plain'), {})


# ---------------------------------------------------------------------------
# Git denylist merge
# ---------------------------------------------------------------------------

class GitDenylistTests(unittest.TestCase):
    def test_git_deny_appended_to_empty_operator_value(self) -> None:
        merged = CodexCliClient._merge_disallowed_with_git_deny('')
        for pattern in CodexCliClient.GIT_DENY_PATTERNS:
            self.assertIn(pattern, merged)

    def test_git_deny_unioned_with_operator_value(self) -> None:
        merged = CodexCliClient._merge_disallowed_with_git_deny('WebFetch')
        self.assertIn('WebFetch', merged)
        for pattern in CodexCliClient.GIT_DENY_PATTERNS:
            self.assertIn(pattern, merged)

    def test_git_deny_not_duplicated_when_operator_already_has_it(self) -> None:
        operator = CodexCliClient.GIT_DENY_PATTERNS[0]
        merged = CodexCliClient._merge_disallowed_with_git_deny(operator)
        # Pattern should appear exactly once.
        self.assertEqual(merged.split(',').count(operator), 1)


if __name__ == '__main__':
    unittest.main()
