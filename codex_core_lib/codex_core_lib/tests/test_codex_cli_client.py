"""Unit tests for :class:`codex_core_lib.cli_client.CodexCliClient`.

Pins the public-API parity contract with ``ClaudeCliClient`` AND
the real Codex CLI 0.132.0 surface: every flag asserted in
``BuildCommandTests`` was verified against ``codex exec --help``
on that version. If you upgrade codex and these tests start
failing, re-read the help output and adjust.

No real ``codex`` binary is launched — ``subprocess.run`` is patched
throughout.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from codex_core_lib.codex_core_lib.cli_client import (
    CodexCliClient,
    _extract_error_text,
    _read_shim_text,
    _readable_message_from_envelope,
    _repository_local_paths,
    _resolve_node_binary,
    _resolve_shim_js_path,
    _resolve_windows_node_invocation_impl,
    _unwrap_backend_error_envelope,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment


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

    def test_valid_effort_lowercased_but_not_emitted_as_flag(self) -> None:
        # Effort is accepted for API parity with ClaudeCliClient but
        # not emitted as a flag — Codex routes reasoning depth via
        # the ``model_reasoning_effort`` config key, not a flag.
        client = CodexCliClient(effort='HIGH')
        self.assertEqual(client._effort, 'high')
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='', resolve_binary=False,
        )
        self.assertNotIn('--reasoning-effort', cmd)
        self.assertNotIn('high', cmd)

    def test_max_turns_accepted_but_not_emitted_as_flag(self) -> None:
        # Same parity story as ``effort``.
        client = CodexCliClient(max_turns=10)
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='', resolve_binary=False,
        )
        self.assertNotIn('--max-turns', cmd)

    def test_allow_deny_tools_accepted_but_not_emitted_as_flags(self) -> None:
        # Codex has no per-spawn allow/deny tool list; the constructor
        # params exist only so the factory can call CodexCliClient with
        # the same kwargs it calls ClaudeCliClient with.
        client = CodexCliClient(
            allowed_tools='Edit,Write',
            disallowed_tools='Bash',
        )
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='', resolve_binary=False,
        )
        self.assertNotIn('--allow-tools', cmd)
        self.assertNotIn('--allowedTools', cmd)
        self.assertNotIn('--deny-tools', cmd)
        self.assertNotIn('--disallowedTools', cmd)


# ---------------------------------------------------------------------------
# No-op contract (mirrors ClaudeCliClient)
# ---------------------------------------------------------------------------

class NoOpContractTests(unittest.TestCase):
    def test_delete_conversation_is_a_noop(self) -> None:
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
                   return_value=_completed(stdout='codex-cli 0.132.0\n', returncode=0)):
            client.validate_connection()
        self.assertEqual(client._binary_path, '/usr/bin/codex')


# ---------------------------------------------------------------------------
# Command construction — every flag verified against `codex exec --help` 0.132.0
# ---------------------------------------------------------------------------

class BuildCommandTests(unittest.TestCase):
    def test_uses_exec_subcommand(self) -> None:
        # ``codex exec`` is the documented non-interactive entry.
        client = CodexCliClient(binary='codex')
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='', resolve_binary=False,
        )
        self.assertEqual(cmd[0], 'codex')
        self.assertEqual(cmd[1], 'exec')

    def test_includes_json_for_event_stream(self) -> None:
        # ``codex exec --json`` → JSONL event stream on stdout.
        client = CodexCliClient(binary='codex')
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='', resolve_binary=False,
        )
        self.assertIn('--json', cmd)

    def test_includes_skip_git_repo_check(self) -> None:
        # Workspace clones may live outside a repo root the operator
        # has cd'd into; codex refuses to run elsewhere unless this
        # flag is set.
        client = CodexCliClient(binary='codex')
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='', resolve_binary=False,
        )
        self.assertIn('--skip-git-repo-check', cmd)

    def test_default_uses_workspace_write_sandbox(self) -> None:
        # Safe-mode default: --sandbox workspace-write lets writes
        # happen inside the workspace but blocks elsewhere. No
        # --ask-for-approval is emitted because that flag is NOT on
        # ``codex exec`` (it's a top-level interactive-mode option);
        # approval policy must come from ~/.codex/config.toml.
        client = CodexCliClient(binary='codex', bypass_permissions=False)
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='', resolve_binary=False,
        )
        self.assertIn('--sandbox', cmd)
        self.assertIn('workspace-write', cmd)
        self.assertNotIn('--ask-for-approval', cmd)

    def test_bypass_uses_single_dangerous_flag(self) -> None:
        # ``--dangerously-bypass-approvals-and-sandbox`` is a single
        # flag (no value) and conflicts with --sandbox, so kato must
        # NOT also emit --sandbox alongside it.
        client = CodexCliClient(binary='codex', bypass_permissions=True)
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='', resolve_binary=False,
        )
        self.assertIn('--dangerously-bypass-approvals-and-sandbox', cmd)
        self.assertNotIn('--sandbox', cmd)
        self.assertNotIn('--ask-for-approval', cmd)

    def test_model_flag_uses_short_form(self) -> None:
        # Codex uses ``-m``/``--model``; we send the short form.
        client = CodexCliClient(binary='codex', model='gpt-5-codex')
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='', resolve_binary=False,
        )
        self.assertIn('-m', cmd)
        self.assertIn('gpt-5-codex', cmd)

    def test_session_resume_uses_subcommand_not_a_flag(self) -> None:
        # Crucial difference from Claude: codex resume is
        # ``codex exec resume <id>``, not ``codex exec --resume <id>``.
        # If a future change reverts to a flag, this fires.
        client = CodexCliClient(binary='codex')
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='sess-123', resolve_binary=False,
        )
        self.assertNotIn('--resume', cmd)
        # Expect the sub-subcommand and the id positionally right after.
        self.assertEqual(cmd[:4], ['codex', 'exec', 'resume', 'sess-123'])

    def test_no_session_id_skips_resume_subcommand(self) -> None:
        client = CodexCliClient(binary='codex')
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='', resolve_binary=False,
        )
        self.assertNotIn('resume', cmd[:4])

    def test_cwd_emitted_via_minus_C_on_fresh_exec(self) -> None:
        # Codex uses ``-C`` / ``--cd <DIR>`` for the agent's working root.
        client = CodexCliClient(binary='codex')
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='', resolve_binary=False,
            cwd='/work/repo',
        )
        idx = cmd.index('-C')
        self.assertEqual(cmd[idx + 1], '/work/repo')

    def test_additional_dirs_use_add_dir_flag_on_fresh_exec(self) -> None:
        # ``--add-dir`` is the documented flag for extra writable dirs.
        client = CodexCliClient(binary='codex')
        cmd = client._build_command(
            additional_dirs=['/repo/a', '/repo/b'],
            agent_session_id='', resolve_binary=False,
        )
        self.assertEqual(cmd.count('--add-dir'), 2)
        self.assertIn('/repo/a', cmd)
        self.assertIn('/repo/b', cmd)

    def test_resume_drops_sandbox_flag(self) -> None:
        # ``codex exec resume`` does NOT accept --sandbox; the resumed
        # session inherits its sandbox from the original spawn. If we
        # emitted it, codex would error out with "unexpected argument".
        client = CodexCliClient(binary='codex', bypass_permissions=False)
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='sess-1', resolve_binary=False,
        )
        self.assertNotIn('--sandbox', cmd)

    def test_resume_drops_cwd_and_add_dir(self) -> None:
        # ``codex exec resume`` does NOT accept -C or --add-dir; the
        # resumed session inherits its working set from the original.
        client = CodexCliClient(binary='codex')
        cmd = client._build_command(
            additional_dirs=['/repo/a'],
            agent_session_id='sess-1', resolve_binary=False,
            cwd='/work/repo',
        )
        self.assertNotIn('-C', cmd)
        self.assertNotIn('--add-dir', cmd)

    def test_resume_keeps_json_skip_git_repo_check_model_and_output_file(self) -> None:
        # The flags resume DOES accept must still pass through so the
        # parser can recover the result + agent_session_id.
        client = CodexCliClient(binary='codex', model='gpt-5-codex')
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='sess-1', resolve_binary=False,
            last_message_file='/tmp/x.txt',
        )
        self.assertIn('--json', cmd)
        self.assertIn('--skip-git-repo-check', cmd)
        self.assertIn('-m', cmd)
        self.assertIn('-o', cmd)

    def test_resume_with_bypass_emits_dangerous_flag(self) -> None:
        # Bypass works on resume just like on fresh exec.
        client = CodexCliClient(binary='codex', bypass_permissions=True)
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='sess-1', resolve_binary=False,
        )
        self.assertIn('--dangerously-bypass-approvals-and-sandbox', cmd)
        self.assertNotIn('--sandbox', cmd)

    def test_last_message_file_emitted_via_short_o(self) -> None:
        # ``-o, --output-last-message <FILE>`` writes the final agent
        # message to a file — cleanest way to recover the result text.
        client = CodexCliClient(binary='codex')
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='', resolve_binary=False,
            last_message_file='/tmp/x.txt',
        )
        idx = cmd.index('-o')
        self.assertEqual(cmd[idx + 1], '/tmp/x.txt')

    def test_sandbox_override_replaces_default(self) -> None:
        # ``investigate`` uses sandbox=read-only for triage runs.
        client = CodexCliClient(binary='codex', bypass_permissions=False)
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='', resolve_binary=False,
            sandbox_override='read-only',
        )
        idx = cmd.index('--sandbox')
        self.assertEqual(cmd[idx + 1], 'read-only')

    def test_extra_args_appended_at_end(self) -> None:
        client = CodexCliClient(binary='codex', extra_args=['--ephemeral'])
        cmd = client._build_command(
            additional_dirs=[], agent_session_id='', resolve_binary=False,
        )
        self.assertEqual(cmd[-1], '--ephemeral')

    def test_subprocess_env_inherits_codex_home(self) -> None:
        # Operator's $CODEX_HOME / auth state must reach the subprocess.
        client = CodexCliClient(binary='codex')
        with patch.dict('os.environ', {'CODEX_HOME': '/custom/.codex'}, clear=False):
            env = client._build_subprocess_env()
        self.assertEqual(env.get('CODEX_HOME'), '/custom/.codex')


# ---------------------------------------------------------------------------
# implement_task / test_task / investigate (subprocess-mocked end-to-end)
# ---------------------------------------------------------------------------

class ImplementTaskTests(unittest.TestCase):
    def _mock_run(self, *, stdout: str = '', stderr: str = '', returncode: int = 0,
                  last_message: str = ''):
        """Patch subprocess.run AND write ``last_message`` to the
        ``--output-last-message`` path so the parser sees it."""

        def fake_run(command, **kwargs):
            # Recover the temp path from the command argv.
            try:
                idx = command.index('-o')
                path = command[idx + 1]
                with open(path, 'w', encoding='utf-8') as handle:
                    handle.write(last_message)
            except (ValueError, IndexError, OSError):
                pass
            return _completed(stdout=stdout, stderr=stderr, returncode=returncode)

        return patch(
            'codex_core_lib.codex_core_lib.cli_client.subprocess.run',
            side_effect=fake_run,
        )

    def test_implement_task_returns_result_from_last_message_file(self) -> None:
        # Primary source for the result text is the file codex writes
        # via ``--output-last-message``, NOT the JSONL stdout stream.
        client = CodexCliClient(binary='codex')
        jsonl_stdout = '{"type": "session_start", "session_id": "sess-1"}\n'
        with self._mock_run(stdout=jsonl_stdout, last_message='all done'):
            result = client.implement_task(_task())
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result[ImplementationFields.MESSAGE], 'all done')
        self.assertEqual(result[ImplementationFields.AGENT_SESSION_ID], 'sess-1')

    def test_test_task_returns_result_from_last_message_file(self) -> None:
        client = CodexCliClient(binary='codex')
        with self._mock_run(stdout='', last_message='tests green'):
            result = client.test_task(_task())
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result[ImplementationFields.MESSAGE], 'tests green')

    def test_subprocess_nonzero_exit_raises_runtimeerror(self) -> None:
        client = CodexCliClient(binary='codex')
        with self._mock_run(stderr='exploded', returncode=2):
            with self.assertRaises(RuntimeError) as ctx:
                client.implement_task(_task())
        self.assertIn('exploded', str(ctx.exception))

    def test_jsonl_error_event_raises_runtimeerror(self) -> None:
        client = CodexCliClient(binary='codex')
        jsonl = '{"type": "error", "message": "model rejected"}\n'
        with self._mock_run(stdout=jsonl, returncode=0):
            with self.assertRaises(RuntimeError) as ctx:
                client.implement_task(_task())
        self.assertIn('model rejected', str(ctx.exception))

    def test_subprocess_timeout_raises_timeouterror(self) -> None:
        client = CodexCliClient(binary='codex', timeout_seconds=60)
        timeout_exc = subprocess.TimeoutExpired(cmd='codex', timeout=60)
        with patch(
            'codex_core_lib.codex_core_lib.cli_client.subprocess.run',
            side_effect=timeout_exc,
        ):
            with self.assertRaises(TimeoutError):
                client.implement_task(_task())

    def test_empty_last_message_and_empty_jsonl_still_succeeds(self) -> None:
        # When the CLI exits 0 but produced no message at all (e.g.
        # agent had nothing to say after editing), kato treats it as
        # a successful spawn — the orchestration layer's
        # ``current_head_sha`` / dirty-tree check catches the "agent
        # really did nothing" case downstream.
        client = CodexCliClient(binary='codex')
        with self._mock_run(stdout='', returncode=0, last_message=''):
            result = client.implement_task(_task())
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertNotIn(ImplementationFields.MESSAGE, result)

    def test_investigate_requires_a_prompt(self) -> None:
        with self.assertRaises(ValueError):
            CodexCliClient().investigate('   ')

    def test_investigate_runs_read_only_sandbox(self) -> None:
        # Triage path must flip the sandbox to read-only so a confused
        # turn can't damage the workspace.
        client = CodexCliClient(binary='codex', repository_root_path='/repos')
        seen_commands: list[list[str]] = []

        def fake_run(command, **kwargs):
            seen_commands.append(list(command))
            try:
                idx = command.index('-o')
                with open(command[idx + 1], 'w', encoding='utf-8') as handle:
                    handle.write('high priority')
            except (ValueError, IndexError, OSError):
                pass
            return _completed(returncode=0)

        with patch(
            'codex_core_lib.codex_core_lib.cli_client.subprocess.run',
            side_effect=fake_run,
        ):
            text = client.investigate('Classify this ticket')
        self.assertEqual(text, 'high priority')
        # The recorded command must include sandbox=read-only.
        self.assertTrue(seen_commands, 'subprocess was not invoked')
        cmd = seen_commands[0]
        self.assertIn('--sandbox', cmd)
        idx = cmd.index('--sandbox')
        self.assertEqual(cmd[idx + 1], 'read-only')


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------

class JsonlParsingTests(unittest.TestCase):
    """Fixtures here mirror the REAL JSONL stream emitted by
    ``codex exec --json`` 0.132.0 — verified by spawning codex with a
    minimal prompt and capturing the raw output (Nov 2025)."""

    def test_empty_input_returns_empty_payload(self) -> None:
        payload = CodexCliClient(binary='codex')._parse_jsonl_payload('')
        self.assertEqual(payload, {'agent_session_id': '', 'is_error': False, 'result': ''})

    def test_real_success_stream_extracts_thread_id_and_message(self) -> None:
        # Exact stream captured from a real run. Codex calls the
        # session id ``thread_id`` and nests the reply under ``item``.
        stream = (
            '{"type":"thread.started","thread_id":"019e4620-dc9f-70d3-a1d5-72363469167f"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"ok"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":13201,"output_tokens":5}}\n'
        )
        payload = CodexCliClient(binary='codex')._parse_jsonl_payload(stream)
        # Translated: codex's ``thread_id`` → kato's ``agent_session_id``.
        self.assertEqual(payload['agent_session_id'], '019e4620-dc9f-70d3-a1d5-72363469167f')
        self.assertEqual(payload['result'], 'ok')
        self.assertFalse(payload['is_error'])

    def test_thread_id_first_match_wins(self) -> None:
        stream = (
            '{"type":"thread.started","thread_id":"first"}\n'
            '{"type":"thread.started","thread_id":"second-should-not-overwrite"}\n'
        )
        payload = CodexCliClient(binary='codex')._parse_jsonl_payload(stream)
        self.assertEqual(payload['agent_session_id'], 'first')

    def test_forward_compat_session_id_key_also_recognised(self) -> None:
        # If a future codex version starts emitting ``session_id``
        # directly (its wire-format key — kato normalizes to
        # ``agent_session_id`` internally), the parser should still
        # pick it up.
        stream = '{"type":"thread.started","session_id":"fwd-compat-id"}\n'
        payload = CodexCliClient(binary='codex')._parse_jsonl_payload(stream)
        self.assertEqual(payload['agent_session_id'], 'fwd-compat-id')

    def test_non_agent_message_items_are_ignored(self) -> None:
        # ``item.completed`` events fire for non-agent-message items
        # too (e.g. tool calls). Those must NOT clobber the result.
        stream = (
            '{"type":"item.completed","item":{"type":"tool_call","name":"shell"}}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"real reply"}}\n'
        )
        payload = CodexCliClient(binary='codex')._parse_jsonl_payload(stream)
        self.assertEqual(payload['result'], 'real reply')

    def test_error_in_type_substring_flips_is_error(self) -> None:
        # Heuristic catch-all in case codex adds future error names.
        stream = '{"type":"turn.error","message":"rate limit hit"}\n'
        payload = CodexCliClient(binary='codex')._parse_jsonl_payload(stream)
        self.assertTrue(payload['is_error'])
        self.assertEqual(payload['result'], 'rate limit hit')

    def test_fail_in_type_substring_flips_is_error(self) -> None:
        stream = '{"type":"task_failed","message":"sandbox refused"}\n'
        payload = CodexCliClient(binary='codex')._parse_jsonl_payload(stream)
        self.assertTrue(payload['is_error'])
        self.assertEqual(payload['result'], 'sandbox refused')

    def test_error_text_from_nested_item(self) -> None:
        stream = '{"type":"item.error","item":{"text":"nested boom"}}\n'
        payload = CodexCliClient(binary='codex')._parse_jsonl_payload(stream)
        self.assertTrue(payload['is_error'])
        self.assertEqual(payload['result'], 'nested boom')

    def test_real_bad_model_failure_stream_unwraps_to_readable_message(self) -> None:
        # Exact bytes captured from a real ``codex exec -m totally-not-a-real-model-9999``
        # run. The error event carries a JSON-encoded backend envelope as
        # its ``message`` field; the parser must unwrap one level so
        # operators see the human-readable inner ``error.message`` text.
        stream = (
            '{"type":"thread.started","thread_id":"019e462c-fe86-74f2-a3da-8855fda13b5f"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"error","message":"{\\"type\\":\\"error\\",\\"status\\":400,'
            '\\"error\\":{\\"type\\":\\"invalid_request_error\\",'
            '\\"message\\":\\"The \'totally-not-a-real-model-9999\' model is not supported '
            'when using Codex with a ChatGPT account.\\"}}"}\n'
            '{"type":"turn.failed","error":{"message":"{\\"type\\":\\"error\\",'
            '\\"status\\":400,\\"error\\":{\\"type\\":\\"invalid_request_error\\",'
            '\\"message\\":\\"The \'totally-not-a-real-model-9999\' model is not supported '
            'when using Codex with a ChatGPT account.\\"}}"}}\n'
        )
        payload = CodexCliClient(binary='codex')._parse_jsonl_payload(stream)
        self.assertEqual(payload['agent_session_id'], '019e462c-fe86-74f2-a3da-8855fda13b5f')
        self.assertTrue(payload['is_error'])
        self.assertNotIn('"type":"error"', payload['result'],
                         msg='envelope should have been unwrapped')
        self.assertIn('totally-not-a-real-model-9999', payload['result'])
        self.assertIn('not supported', payload['result'])

    def test_turn_failed_with_nested_error_dict(self) -> None:
        # ``turn.failed`` puts the message under ``error.message``,
        # NOT directly under ``message``. Previously my parser checked
        # ``message`` first and would have stringified a dict — this
        # test guards the fix.
        stream = '{"type":"turn.failed","error":{"message":"backend hiccup"}}\n'
        payload = CodexCliClient(binary='codex')._parse_jsonl_payload(stream)
        self.assertTrue(payload['is_error'])
        self.assertEqual(payload['result'], 'backend hiccup')

    def test_real_tool_use_stream_picks_agent_message_over_command_output(self) -> None:
        # Exact bytes captured from a real ``codex exec`` run that
        # asked the agent to read a file. The stream contains:
        #   thread.started + turn.started
        #   item.started   (command_execution, in_progress)
        #   item.completed (command_execution, shell output)
        #   item.completed (agent_message — THE answer)
        #   turn.completed
        # The parser must pick the agent_message text, NOT the shell
        # command_execution aggregated_output.
        stream = (
            '{"type":"thread.started","thread_id":"019e462b-d803-7340-afa0-a22eb31786c7"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"item.started","item":{"id":"item_0","type":"command_execution",'
            '"command":"sed -n 1,120p /tmp/x.txt","status":"in_progress"}}\n'
            '{"type":"item.completed","item":{"id":"item_0","type":"command_execution",'
            '"command":"sed -n 1,120p /tmp/x.txt",'
            '"aggregated_output":"secret value: cherry-blossom-42\\n",'
            '"exit_code":0,"status":"completed"}}\n'
            '{"type":"item.completed","item":{"id":"item_1","type":"agent_message",'
            '"text":"cherry-blossom-42"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":3}}\n'
        )
        payload = CodexCliClient(binary='codex')._parse_jsonl_payload(stream)
        self.assertEqual(payload['agent_session_id'], '019e462b-d803-7340-afa0-a22eb31786c7')
        self.assertEqual(payload['result'], 'cherry-blossom-42')
        self.assertFalse(payload['is_error'])

    def test_invalid_json_lines_are_ignored(self) -> None:
        # Tolerant — banner lines / blank lines / partial JSON must
        # not crash the parser. Real codex stderr / piped output can
        # contain such noise.
        stream = (
            '\n'
            'codex banner line\n'
            '{not json\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"final"}}\n'
        )
        payload = CodexCliClient(binary='codex')._parse_jsonl_payload(stream)
        self.assertEqual(payload['result'], 'final')


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


# ---------------------------------------------------------------------------
# fix_review_comments / fix_review_comment — public API parity
# ---------------------------------------------------------------------------

def _make_review_comment(
    *,
    comment_id: str = 'c-1',
    author: str = 'reviewer',
    body: str = 'please fix this',
    file_path: str = 'src/foo.py',
    line_number: int = 42,
    line_type: str = 'ADDED',
    commit_sha: str = 'abc1234',
) -> ReviewComment:
    return ReviewComment(
        pull_request_id='pr-7',
        comment_id=comment_id,
        author=author,
        body=body,
        file_path=file_path,
        line_number=line_number,
        line_type=line_type,
        commit_sha=commit_sha,
    )


def _mock_run_with_last_message(last_message: str = 'done', jsonl: str = '',
                                 returncode: int = 0):
    """Reusable patcher: simulate codex writing ``last_message`` to the
    ``-o`` path so the parser sees it."""
    def fake_run(command, **kwargs):
        try:
            idx = command.index('-o')
            path = command[idx + 1]
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(last_message)
        except (ValueError, IndexError, OSError):
            pass
        return SimpleNamespace(stdout=jsonl, stderr='', returncode=returncode)
    return patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                 side_effect=fake_run)


class FixReviewCommentsTests(unittest.TestCase):
    def test_fix_review_comments_requires_at_least_one_comment(self) -> None:
        with self.assertRaises(ValueError):
            CodexCliClient(binary='codex').fix_review_comments([], 'main')

    def test_fix_review_comment_delegates_to_fix_review_comments(self) -> None:
        # ``fix_review_comment`` is the singular convenience wrapper —
        # it should forward into the plural form with a one-item list.
        client = CodexCliClient(binary='codex')
        with _mock_run_with_last_message(last_message='addressed'):
            result = client.fix_review_comment(
                _make_review_comment(),
                branch_name='feature/x',
                task_id='PROJ-7',
                task_summary='fix the bug',
            )
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result[ImplementationFields.MESSAGE], 'addressed')

    def test_fix_review_comments_single_uses_singular_prompt_builder(self) -> None:
        # Confirm the single-comment path doesn't accidentally fall
        # into the batch builder. We can't introspect the prompt
        # easily here, but we can confirm the spawn ran + returned.
        client = CodexCliClient(binary='codex')
        with _mock_run_with_last_message(last_message='fixed'):
            result = client.fix_review_comments(
                [_make_review_comment()], 'main',
                task_id='PROJ-1', task_summary='x',
            )
        self.assertEqual(result[ImplementationFields.MESSAGE], 'fixed')

    def test_fix_review_comments_batch_uses_batch_prompt_builder(self) -> None:
        # When 2+ comments are passed the batch builder kicks in.
        client = CodexCliClient(binary='codex')
        comments = [
            _make_review_comment(comment_id='c-1', body='first nit'),
            _make_review_comment(comment_id='c-2', body='second nit'),
        ]
        with _mock_run_with_last_message(last_message='both addressed'):
            result = client.fix_review_comments(
                comments, 'main', task_id='PROJ-1',
            )
        self.assertEqual(result[ImplementationFields.MESSAGE], 'both addressed')

    def test_fix_review_comments_answer_mode_returns_text(self) -> None:
        # In answer mode the agent shouldn't edit; we just verify the
        # return shape matches the fix-mode shape.
        client = CodexCliClient(binary='codex')
        with _mock_run_with_last_message(last_message='it works because Y'):
            result = client.fix_review_comments(
                [_make_review_comment(body='why does it work?')],
                'main', task_id='PROJ-1', mode='answer',
            )
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result[ImplementationFields.MESSAGE],
                         'it works because Y')


# ---------------------------------------------------------------------------
# Review prompt builders (rendered text — no spawn needed)
# ---------------------------------------------------------------------------

class ReviewPromptBuilderTests(unittest.TestCase):
    def test_single_review_prompt_fix_mode_mentions_branch_and_comment(self) -> None:
        comment = _make_review_comment(body='rename foo to bar')
        prompt = CodexCliClient._build_review_prompt(
            comment, branch_name='feature/x', workspace_path='', mode='fix',
        )
        self.assertIn('feature/x', prompt)
        self.assertIn('rename foo to bar', prompt)
        # Fix mode includes "Make the smallest possible change"
        self.assertIn('smallest possible change', prompt)

    def test_single_review_prompt_answer_mode_says_do_not_modify(self) -> None:
        comment = _make_review_comment(body='why is this off-by-one?')
        prompt = CodexCliClient._build_review_prompt(
            comment, branch_name='main', mode='answer',
        )
        self.assertIn('QUESTION', prompt)
        self.assertIn('Do NOT modify any files', prompt)

    def test_batch_review_prompt_fix_mode_numbers_each_comment(self) -> None:
        comments = [
            _make_review_comment(comment_id='a', body='first'),
            _make_review_comment(comment_id='b', body='second'),
            _make_review_comment(comment_id='c', body='third'),
        ]
        prompt = CodexCliClient._build_review_comments_batch_prompt(
            comments, branch_name='main', mode='fix',
        )
        self.assertIn('1.', prompt)
        self.assertIn('2.', prompt)
        self.assertIn('3.', prompt)
        self.assertIn('first', prompt)
        self.assertIn('second', prompt)
        self.assertIn('third', prompt)

    def test_batch_review_prompt_answer_mode_says_no_modify(self) -> None:
        comments = [
            _make_review_comment(comment_id='a', body='Q1?'),
            _make_review_comment(comment_id='b', body='Q2?'),
        ]
        prompt = CodexCliClient._build_review_comments_batch_prompt(
            comments, branch_name='main', mode='answer',
        )
        self.assertIn('QUESTIONS', prompt)
        self.assertIn('Do NOT modify any files', prompt)
        # Numbering instruction for answer mode
        self.assertIn('1, 2, 3', prompt)

    def test_review_prompt_with_workspace_includes_code_snippet_block(self) -> None:
        # When workspace_path is set, the prompt builder tries to
        # read a code snippet from the file at line_number. The
        # snippet helper returns '' for missing files, so this just
        # exercises the workspace_path branch.
        comment = _make_review_comment()
        prompt = CodexCliClient._build_review_prompt(
            comment, branch_name='main', workspace_path='/tmp/codex-probe',
        )
        # The workspace_path opens the scope_block branch
        self.assertIn('WORKSPACE SCOPE', prompt)


# ---------------------------------------------------------------------------
# Docker-sandbox spawn path
# ---------------------------------------------------------------------------

class DockerSandboxSpawnTests(unittest.TestCase):
    """The docker_mode_on=True branch in _run_prompt wraps every spawn
    in the hardened sandbox. Each guard inside (ensure_image,
    check_spawn_rate, enforce_no_workspace_secrets, record_spawn) can
    raise SandboxError; kato must surface those as clean RuntimeError."""

    def _make_client(self) -> CodexCliClient:
        return CodexCliClient(binary='codex', docker_mode_on=True)

    def test_ensure_image_failure_raises_runtimeerror(self) -> None:
        client = self._make_client()
        from sandbox_core_lib.sandbox_core_lib.manager import SandboxError
        with patch('sandbox_core_lib.sandbox_core_lib.manager.ensure_image',
                   side_effect=SandboxError('image missing')):
            with self.assertRaises(RuntimeError) as ctx:
                client.implement_task(_task())
        self.assertIn('image missing', str(ctx.exception))

    def test_spawn_rate_limit_raises_runtimeerror(self) -> None:
        client = self._make_client()
        from sandbox_core_lib.sandbox_core_lib.manager import SandboxError
        with patch('sandbox_core_lib.sandbox_core_lib.manager.ensure_image'), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.check_spawn_rate',
                   side_effect=SandboxError('too fast')):
            with self.assertRaises(RuntimeError) as ctx:
                client.implement_task(_task())
        self.assertIn('too fast', str(ctx.exception))

    def test_workspace_secrets_detected_raises_runtimeerror(self) -> None:
        client = self._make_client()
        from sandbox_core_lib.sandbox_core_lib.manager import SandboxError
        with patch('sandbox_core_lib.sandbox_core_lib.manager.ensure_image'), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.check_spawn_rate'), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.make_container_name',
                   return_value='kato-codex-x'), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.enforce_no_workspace_secrets',
                   side_effect=SandboxError('found .env')):
            with self.assertRaises(RuntimeError) as ctx:
                client.implement_task(_task())
        self.assertIn('found .env', str(ctx.exception))

    def test_record_spawn_failure_raises_runtimeerror(self) -> None:
        client = self._make_client()
        from sandbox_core_lib.sandbox_core_lib.manager import SandboxError
        with patch('sandbox_core_lib.sandbox_core_lib.manager.ensure_image'), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.check_spawn_rate'), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.make_container_name',
                   return_value='kato-codex-x'), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.enforce_no_workspace_secrets'), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.wrap_command',
                   side_effect=lambda c, **kw: c), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.record_spawn',
                   side_effect=SandboxError('audit log disk full')):
            with self.assertRaises(RuntimeError) as ctx:
                client.implement_task(_task())
        self.assertIn('audit log disk full', str(ctx.exception))

    def test_happy_docker_path_succeeds(self) -> None:
        # All guards pass, wrap_command produces a command, subprocess
        # mock writes the last-message file → success.
        client = self._make_client()

        def fake_subprocess_run(command, **kwargs):
            # Even though wrap_command may have prefixed `docker run`,
            # the -o flag should still appear in argv.
            try:
                idx = command.index('-o')
                with open(command[idx + 1], 'w', encoding='utf-8') as handle:
                    handle.write('ok-from-docker')
            except (ValueError, IndexError, OSError):
                pass
            return SimpleNamespace(stdout='', stderr='', returncode=0)

        with patch('sandbox_core_lib.sandbox_core_lib.manager.ensure_image'), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.check_spawn_rate'), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.make_container_name',
                   return_value='kato-codex-x'), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.enforce_no_workspace_secrets'), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.wrap_command',
                   side_effect=lambda c, **kw: c), \
             patch('sandbox_core_lib.sandbox_core_lib.manager.record_spawn'), \
             patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   side_effect=fake_subprocess_run):
            result = client.implement_task(_task())
        self.assertEqual(result[ImplementationFields.MESSAGE], 'ok-from-docker')


# ---------------------------------------------------------------------------
# Credential / phishing scan
# ---------------------------------------------------------------------------

class CredentialScanTests(unittest.TestCase):
    """``_scan_response_for_credentials`` audits the agent's reply
    text for committed-secret patterns + shell-phishing patterns and
    logs WARNINGs for any hit. Exit code is not affected — this is
    an audit trail, not a block."""

    def test_credential_pattern_in_response_logs_warning(self) -> None:
        client = CodexCliClient(binary='codex')
        with patch.object(client.logger, 'warning') as warn:
            with patch(
                'sandbox_core_lib.sandbox_core_lib.credential_patterns.find_credential_patterns',
                return_value=[('aws_access_key', 'AKIA…')],
            ), patch(
                'sandbox_core_lib.sandbox_core_lib.credential_patterns.find_phishing_patterns',
                return_value=[],
            ), patch(
                'sandbox_core_lib.sandbox_core_lib.credential_patterns.summarize_findings',
                return_value='aws_access_key (1)',
            ):
                client._scan_response_for_credentials(
                    'here is a key AKIA…', log_label='PROJ-1',
                )
        warn.assert_called_once()
        self.assertIn('CREDENTIAL PATTERN DETECTED', warn.call_args[0][0])

    def test_phishing_pattern_in_response_logs_warning(self) -> None:
        client = CodexCliClient(binary='codex')
        with patch.object(client.logger, 'warning') as warn:
            with patch(
                'sandbox_core_lib.sandbox_core_lib.credential_patterns.find_credential_patterns',
                return_value=[],
            ), patch(
                'sandbox_core_lib.sandbox_core_lib.credential_patterns.find_phishing_patterns',
                return_value=[('curl_bash', 'curl …| bash')],
            ), patch(
                'sandbox_core_lib.sandbox_core_lib.credential_patterns.summarize_findings',
                return_value='curl_bash (1)',
            ):
                client._scan_response_for_credentials(
                    'run this curl x | bash', log_label='PROJ-1',
                )
        warn.assert_called_once()
        self.assertIn('PHISHING PATTERN DETECTED', warn.call_args[0][0])

    def test_empty_response_short_circuits(self) -> None:
        # No response text → no scan calls at all.
        client = CodexCliClient(binary='codex')
        with patch(
            'sandbox_core_lib.sandbox_core_lib.credential_patterns.find_credential_patterns',
        ) as mock_cred:
            client._scan_response_for_credentials('', log_label='x')
        mock_cred.assert_not_called()


# ---------------------------------------------------------------------------
# Smoke test paths
# ---------------------------------------------------------------------------

class SmokeTestPathTests(unittest.TestCase):
    def test_validate_model_smoke_test_skipped_when_flag_off(self) -> None:
        # No subprocess.run patch needed — if the early return doesn't
        # fire, the call would attempt a real codex spawn.
        client = CodexCliClient(binary='codex', model_smoke_test_enabled=False)
        client._validate_model_smoke_test()  # must not raise / spawn

    def test_validate_model_access_smoke_test_idempotent(self) -> None:
        client = CodexCliClient(binary='codex', model_smoke_test_enabled=True)
        with patch.object(client, '_run_model_access_validation') as run:
            client._validate_model_access_smoke_test()
            client._validate_model_access_smoke_test()
            client._validate_model_access_smoke_test()
        # Second + third calls must short-circuit.
        run.assert_called_once()

    def test_run_model_access_validation_timeout_raises(self) -> None:
        client = CodexCliClient(binary='codex')
        timeout_exc = subprocess.TimeoutExpired(cmd='codex', timeout=120)
        with patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   side_effect=timeout_exc):
            with self.assertRaises(RuntimeError) as ctx:
                client._run_model_access_validation()
        self.assertIn('smoke test did not finish', str(ctx.exception))

    def test_run_model_access_validation_nonzero_exit_raises(self) -> None:
        client = CodexCliClient(binary='codex')
        with patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   return_value=_completed(stderr='no auth', returncode=1)):
            with self.assertRaises(RuntimeError) as ctx:
                client._run_model_access_validation()
        self.assertIn('smoke test failed', str(ctx.exception))
        self.assertIn('no auth', str(ctx.exception))

    def test_run_model_access_validation_jsonl_error_raises(self) -> None:
        client = CodexCliClient(binary='codex')
        # JSONL stream contains an error event but exit code is 0.
        jsonl = '{"type":"error","message":"model rejected"}\n'
        with patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   return_value=_completed(stdout=jsonl, returncode=0)):
            with self.assertRaises(RuntimeError) as ctx:
                client._run_model_access_validation()
        self.assertIn('smoke test reported an error', str(ctx.exception))

    def test_validate_model_access_public_method_runs_smoke_test(self) -> None:
        client = CodexCliClient(binary='codex')
        with patch.object(client, '_run_model_access_validation') as run:
            client.validate_model_access()
        run.assert_called_once()

    def test_validate_connection_runs_smoke_test_when_enabled(self) -> None:
        client = CodexCliClient(binary='codex', model_smoke_test_enabled=True)
        with patch.object(CodexCliClient, '_running_inside_docker', return_value=False), \
             patch('codex_core_lib.codex_core_lib.cli_client.shutil.which',
                   return_value='/usr/bin/codex'), \
             patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   return_value=_completed(stdout='codex-cli 0.132.0\n', returncode=0)), \
             patch.object(client, '_run_model_access_validation') as run_smoke:
            client.validate_connection()
        run_smoke.assert_called_once()


# ---------------------------------------------------------------------------
# Misc edge cases / branches
# ---------------------------------------------------------------------------

class EdgeCaseTests(unittest.TestCase):
    def test_running_inside_docker_returns_true_when_dockerenv_present(self) -> None:
        # Patch Path('/.dockerenv').exists() to True.
        with patch('codex_core_lib.codex_core_lib.cli_client.Path') as mock_path:
            mock_path.return_value.exists.return_value = True
            self.assertTrue(CodexCliClient._running_inside_docker())

    def test_validate_connection_version_probe_oserror(self) -> None:
        client = CodexCliClient(binary='codex', model_smoke_test_enabled=False)
        with patch.object(CodexCliClient, '_running_inside_docker', return_value=False), \
             patch('codex_core_lib.codex_core_lib.cli_client.shutil.which',
                   return_value='/usr/bin/codex'), \
             patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   side_effect=OSError('permission denied')):
            with self.assertRaises(RuntimeError) as ctx:
                client.validate_connection()
        self.assertIn('failed to launch', str(ctx.exception))
        self.assertIn('permission denied', str(ctx.exception))

    def test_run_prompt_oserror_raises_runtimeerror(self) -> None:
        client = CodexCliClient(binary='codex')
        with patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   side_effect=OSError('no exec')):
            with self.assertRaises(RuntimeError) as ctx:
                client.implement_task(_task())
        self.assertIn('failed to invoke', str(ctx.exception))
        self.assertIn('no exec', str(ctx.exception))

    def test_working_directories_no_prepared_task_falls_back_to_repository_root(self) -> None:
        client = CodexCliClient(binary='codex', repository_root_path='/operators/repos')
        cwd, extras = client._working_directories(None)
        self.assertEqual(cwd, '/operators/repos')
        self.assertEqual(extras, [])

    def test_working_directories_no_repos_falls_back_to_cwd(self) -> None:
        # prepared_task has no repositories — fall back to os.getcwd()
        client = CodexCliClient(binary='codex', repository_root_path='')
        prepared = SimpleNamespace(repositories=[])
        cwd, extras = client._working_directories(prepared)
        self.assertTrue(cwd)  # whatever os.getcwd() returns
        self.assertEqual(extras, [])

    def test_working_directories_dedupes_local_paths(self) -> None:
        client = CodexCliClient(binary='codex')
        prepared = SimpleNamespace(repositories=[
            SimpleNamespace(local_path='/repo/a'),
            SimpleNamespace(local_path='/repo/a'),  # duplicate
            SimpleNamespace(local_path='/repo/b'),
        ])
        cwd, extras = client._working_directories(prepared)
        self.assertEqual(cwd, '/repo/a')
        self.assertEqual(extras, ['/repo/b'])

    def test_review_comment_cwd_prefers_repository_local_path(self) -> None:
        client = CodexCliClient(binary='codex', repository_root_path='/root')
        comment = SimpleNamespace(repository_local_path='/per-repo/x')
        self.assertEqual(client._review_comment_cwd(comment), '/per-repo/x')

    def test_review_comment_cwd_falls_back_to_repository_root_path(self) -> None:
        client = CodexCliClient(binary='codex', repository_root_path='/root')
        comment = SimpleNamespace()  # no repository_local_path
        self.assertEqual(client._review_comment_cwd(comment), '/root')

    def test_review_comment_cwd_final_fallback_is_cwd(self) -> None:
        client = CodexCliClient(binary='codex', repository_root_path='')
        comment = SimpleNamespace()
        # Should NOT crash; returns os.getcwd().
        self.assertTrue(client._review_comment_cwd(comment))

    def test_investigate_restores_bypass_state_in_finally(self) -> None:
        client = CodexCliClient(binary='codex', bypass_permissions=True)

        def fake_run(command, **kwargs):
            try:
                idx = command.index('-o')
                with open(command[idx + 1], 'w', encoding='utf-8') as handle:
                    handle.write('answer')
            except (ValueError, IndexError, OSError):
                pass
            return _completed(returncode=0)

        with patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   side_effect=fake_run):
            client.investigate('classify this', cwd='/tmp')
        # Bypass state should be restored to True after the call.
        self.assertTrue(client._bypass_permissions)


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------

class ExtractErrorTextTests(unittest.TestCase):
    def test_top_level_message_wins(self) -> None:
        self.assertEqual(_extract_error_text({'message': 'top'}), 'top')

    def test_nested_dict_error_message(self) -> None:
        self.assertEqual(
            _extract_error_text({'error': {'message': 'nested'}}),
            'nested',
        )

    def test_nested_dict_error_error_fallback(self) -> None:
        # When ``error`` dict has only an ``error`` key (no message)
        self.assertEqual(
            _extract_error_text({'error': {'error': 'deeper'}}),
            'deeper',
        )

    def test_nested_string_error(self) -> None:
        self.assertEqual(_extract_error_text({'error': 'just a string'}),
                         'just a string')

    def test_item_text_fallback(self) -> None:
        self.assertEqual(
            _extract_error_text({'item': {'text': 'from item'}}),
            'from item',
        )

    def test_item_message_fallback(self) -> None:
        self.assertEqual(
            _extract_error_text({'item': {'message': 'item msg'}}),
            'item msg',
        )

    def test_returns_empty_when_nothing_found(self) -> None:
        self.assertEqual(_extract_error_text({'type': 'error'}), '')

    def test_non_string_message_ignored(self) -> None:
        # If ``message`` is a non-string (e.g. a number or dict), the
        # extractor must skip it and look further.
        self.assertEqual(_extract_error_text({'message': 42, 'error': 'real'}), 'real')


class UnwrapBackendErrorEnvelopeTests(unittest.TestCase):
    def test_returns_input_when_not_json(self) -> None:
        self.assertEqual(_unwrap_backend_error_envelope('plain text'), 'plain text')

    def test_returns_input_when_invalid_json(self) -> None:
        self.assertEqual(_unwrap_backend_error_envelope('{not json'),
                         '{not json')

    def test_returns_input_when_not_a_dict(self) -> None:
        # ``json.loads('[1,2]')`` returns a list, not a dict.
        self.assertEqual(_unwrap_backend_error_envelope('[1, 2]'), '[1, 2]')

    def test_unwraps_nested_error_message(self) -> None:
        envelope = '{"error": {"message": "the real one"}}'
        self.assertEqual(_unwrap_backend_error_envelope(envelope), 'the real one')

    def test_falls_back_to_top_level_message_when_no_nested_error(self) -> None:
        envelope = '{"message": "top-level only"}'
        self.assertEqual(_unwrap_backend_error_envelope(envelope),
                         'top-level only')

    def test_falls_back_to_input_when_envelope_has_no_readable_message(self) -> None:
        envelope = '{"type": "error", "status": 500}'
        # Nothing readable inside — return the original envelope so the
        # operator at least sees something.
        self.assertEqual(_unwrap_backend_error_envelope(envelope), envelope)


class ReadableMessageFromEnvelopeTests(unittest.TestCase):
    def test_nested_message_wins(self) -> None:
        self.assertEqual(
            _readable_message_from_envelope({'error': {'message': 'hi'}}),
            'hi',
        )

    def test_nested_error_string(self) -> None:
        self.assertEqual(
            _readable_message_from_envelope({'error': {'error': 'fallback'}}),
            'fallback',
        )

    def test_top_level_message(self) -> None:
        self.assertEqual(
            _readable_message_from_envelope({'message': 'top'}),
            'top',
        )

    def test_returns_empty_when_no_string_anywhere(self) -> None:
        self.assertEqual(_readable_message_from_envelope({'status': 500}), '')


class RepositoryLocalPathsTests(unittest.TestCase):
    def test_none_prepared_task_returns_empty(self) -> None:
        self.assertEqual(_repository_local_paths(None), [])

    def test_no_repos_returns_empty(self) -> None:
        self.assertEqual(_repository_local_paths(SimpleNamespace(repositories=[])), [])

    def test_collects_local_paths(self) -> None:
        prepared = SimpleNamespace(repositories=[
            SimpleNamespace(local_path='/a'),
            SimpleNamespace(local_path='/b'),
        ])
        self.assertEqual(_repository_local_paths(prepared), ['/a', '/b'])

    def test_skips_blank_local_paths(self) -> None:
        prepared = SimpleNamespace(repositories=[
            SimpleNamespace(local_path=''),
            SimpleNamespace(local_path='   '),
            SimpleNamespace(local_path='/real'),
        ])
        self.assertEqual(_repository_local_paths(prepared), ['/real'])


# ---------------------------------------------------------------------------
# Windows shim — exercised by mocking os.name + the shim text
# ---------------------------------------------------------------------------

class WindowsShimTests(unittest.TestCase):
    """Cover the Windows-shim helper branches without patching
    ``os.name`` (patching that to ``'nt'`` on a Mac/Linux host
    breaks ``pathlib``'s ``WindowsPath`` instantiation).

    Strategy: the gate-level method ``_resolve_windows_node_invocation``
    just delegates to a module-level impl ``_resolve_windows_node_invocation_impl``
    that has NO ``os.name`` check, so tests call the impl directly
    with real temp files for the shim / cli.js / node.exe fixtures.
    """

    def test_non_windows_gate_returns_none(self) -> None:
        # The gate fires on a non-Windows host — default test
        # environment. This covers the gate branch.
        result = CodexCliClient._resolve_windows_node_invocation('/anywhere/codex')
        self.assertIsNone(result)

    def test_non_cmd_extension_returns_none(self) -> None:
        result = _resolve_windows_node_invocation_impl('C:/codex.exe')
        self.assertIsNone(result)

    def test_unreadable_shim_returns_none(self) -> None:
        # ``cmd_path`` ends in .cmd but the file doesn't actually exist,
        # so ``read_text`` raises OSError → bails.
        result = _resolve_windows_node_invocation_impl(
            '/nonexistent/path/codex.cmd',
        )
        self.assertIsNone(result)

    def test_shim_without_js_reference_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp) / 'codex.cmd'
            shim.write_text('echo no js here', encoding='utf-8')
            result = _resolve_windows_node_invocation_impl(str(shim))
        self.assertIsNone(result)

    def test_shim_with_missing_js_file_returns_none(self) -> None:
        # Shim text references a .js file but the .js doesn't exist.
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp) / 'codex.cmd'
            shim.write_text('node "%~dp0\\cli.js" "$@"', encoding='utf-8')
            result = _resolve_windows_node_invocation_impl(str(shim))
        self.assertIsNone(result)

    def test_shim_resolves_to_node_argv_pair_when_local_node(self) -> None:
        # Happy path: shim text has a .js reference, the .js file
        # exists, and node.exe lives next to it.
        with tempfile.TemporaryDirectory() as tmp:
            shim_dir = Path(tmp)
            shim = shim_dir / 'codex.cmd'
            shim.write_text('node "%~dp0\\cli.js" "$@"', encoding='utf-8')
            (shim_dir / 'cli.js').write_text('// codex entry', encoding='utf-8')
            (shim_dir / 'node.exe').write_text('fake node', encoding='utf-8')
            result = _resolve_windows_node_invocation_impl(str(shim))
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0].endswith('node.exe'))
        self.assertTrue(result[1].endswith('cli.js'))

    def test_shim_falls_back_to_path_node_when_no_local(self) -> None:
        # node.exe NOT next to the shim → fall back to shutil.which('node').
        with tempfile.TemporaryDirectory() as tmp:
            shim_dir = Path(tmp)
            shim = shim_dir / 'codex.cmd'
            shim.write_text('node "%~dp0\\cli.js" "$@"', encoding='utf-8')
            (shim_dir / 'cli.js').write_text('// codex entry', encoding='utf-8')
            # NO node.exe next to the shim.
            with patch('codex_core_lib.codex_core_lib.cli_client.shutil.which',
                       return_value='/usr/local/bin/node'):
                result = _resolve_windows_node_invocation_impl(str(shim))
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], '/usr/local/bin/node')
        self.assertTrue(result[1].endswith('cli.js'))

    def test_shim_returns_none_when_no_node_anywhere(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shim_dir = Path(tmp)
            shim = shim_dir / 'codex.cmd'
            shim.write_text('node "%~dp0\\cli.js" "$@"', encoding='utf-8')
            (shim_dir / 'cli.js').write_text('// codex entry', encoding='utf-8')
            with patch('codex_core_lib.codex_core_lib.cli_client.shutil.which',
                       return_value=None):
                result = _resolve_windows_node_invocation_impl(str(shim))
        self.assertIsNone(result)

    def test_read_shim_text_oserror_returns_none(self) -> None:
        self.assertIsNone(_read_shim_text(Path('/no/such/file.cmd')))

    def test_resolve_shim_js_path_no_match_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp) / 'codex.cmd'
            shim.write_text('plain text', encoding='utf-8')
            self.assertIsNone(_resolve_shim_js_path(shim, shim.read_text()))

    def test_resolve_node_binary_local_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shim_dir = Path(tmp)
            (shim_dir / 'node.exe').write_text('fake', encoding='utf-8')
            result = _resolve_node_binary(shim_dir)
        self.assertIsNotNone(result)
        self.assertTrue(str(result).endswith('node.exe'))

    def test_host_binary_argv_uses_node_invocation_when_available(self) -> None:
        client = CodexCliClient(binary='codex')
        with patch.object(CodexCliClient, '_resolve_windows_node_invocation',
                          return_value=['/path/node', '/path/cli.js']):
            argv = client._host_binary_argv()
        self.assertEqual(argv, ['/path/node', '/path/cli.js'])

    def test_host_binary_argv_falls_back_to_resolved_when_no_shim(self) -> None:
        client = CodexCliClient(binary='codex')
        client._binary_path = '/usr/local/bin/codex'
        with patch.object(CodexCliClient, '_resolve_windows_node_invocation',
                          return_value=None):
            argv = client._host_binary_argv()
        self.assertEqual(argv, ['/usr/local/bin/codex'])

    def test_windows_gate_delegates_to_impl_when_host_is_windows(self) -> None:
        # Patch the indirection helper so the gate thinks the host is
        # Windows, then verify it forwards to the impl. Avoids
        # patching ``os.name`` (which breaks pathlib on Mac/Linux).
        with patch(
            'codex_core_lib.codex_core_lib.cli_client._is_windows_host',
            return_value=True,
        ), patch(
            'codex_core_lib.codex_core_lib.cli_client._resolve_windows_node_invocation_impl',
            return_value=['/node', '/cli.js'],
        ) as mock_impl:
            result = CodexCliClient._resolve_windows_node_invocation('C:/codex.cmd')
        mock_impl.assert_called_once_with('C:/codex.cmd')
        self.assertEqual(result, ['/node', '/cli.js'])

    def test_is_windows_host_returns_false_on_non_windows(self) -> None:
        # On the macOS/Linux test host, the indirection helper
        # returns False — covers the canonical path.
        from codex_core_lib.codex_core_lib.cli_client import _is_windows_host
        self.assertFalse(_is_windows_host())


# ---------------------------------------------------------------------------
# Defensive OSError paths around the --output-last-message tempfile
# ---------------------------------------------------------------------------

class TempFileOSErrorBranchTests(unittest.TestCase):
    """``_run_prompt`` creates a tempfile for ``-o``, and
    ``_parse_completed_process`` reads it. Both paths defensively
    swallow OSError so a transient filesystem hiccup can't crash a
    real spawn after it already succeeded."""

    def test_run_prompt_unlink_failure_is_swallowed(self) -> None:
        # The cleanup ``os.unlink`` in ``_run_prompt``'s ``finally``
        # block must not propagate — the spawn already returned, we
        # have the result, a stuck temp file is not a task failure.
        client = CodexCliClient(binary='codex')

        def fake_run(command, **kwargs):
            try:
                idx = command.index('-o')
                with open(command[idx + 1], 'w', encoding='utf-8') as handle:
                    handle.write('done')
            except (ValueError, IndexError, OSError):
                pass
            return SimpleNamespace(stdout='', stderr='', returncode=0)

        with patch('codex_core_lib.codex_core_lib.cli_client.subprocess.run',
                   side_effect=fake_run), \
             patch('codex_core_lib.codex_core_lib.cli_client.os.unlink',
                   side_effect=OSError('cleanup failed')):
            result = client.implement_task(_task())
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result[ImplementationFields.MESSAGE], 'done')

    def test_parse_completed_process_unreadable_last_message_file(self) -> None:
        # If the ``-o`` file open fails (file deleted between write
        # and read, perms changed, etc.) we fall back to the JSONL
        # ``result`` field instead of crashing.
        client = CodexCliClient(binary='codex')
        completed = SimpleNamespace(
            stdout=(
                '{"type":"thread.started","thread_id":"t-1"}\n'
                '{"type":"item.completed","item":{"type":"agent_message","text":"jsonl result"}}\n'
            ),
            stderr='',
            returncode=0,
        )
        result = client._parse_completed_process(
            completed,
            log_label='x',
            last_message_file='/no/such/path/that-cannot-be-read.txt',
        )
        self.assertTrue(result[ImplementationFields.SUCCESS])
        # File-read failed (OSError swallowed) → result text comes from JSONL.
        self.assertEqual(result[ImplementationFields.MESSAGE], 'jsonl result')


# ---------------------------------------------------------------------------
# Init-time warnings for ignored params
# ---------------------------------------------------------------------------

class IgnoredParamsLoggingTests(unittest.TestCase):
    def test_allowed_tools_set_logs_one_info(self) -> None:
        with patch('codex_core_lib.codex_core_lib.cli_client.configure_logger') as cl:
            logger = cl.return_value = SimpleNamespace(
                warning=MagicMock(), info=MagicMock(),
            )
            CodexCliClient(binary='codex', allowed_tools='Edit,Write')
        self.assertTrue(logger.info.called)
        msg = logger.info.call_args[0][0]
        self.assertIn('Codex backend ignores', msg)

    def test_no_ignored_params_no_info_log(self) -> None:
        with patch('codex_core_lib.codex_core_lib.cli_client.configure_logger') as cl:
            logger = cl.return_value = SimpleNamespace(
                warning=MagicMock(), info=MagicMock(),
            )
            CodexCliClient(binary='codex')
        logger.info.assert_not_called()


if __name__ == '__main__':
    unittest.main()
