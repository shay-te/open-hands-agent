from __future__ import annotations

import io
import json
import threading
import time
import unittest
import unittest.mock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from claude_core_lib.claude_core_lib.session.streaming import SessionEvent, StreamingClaudeSession


class _FakeProc:
    """Minimal subprocess.Popen stand-in for the streaming session tests."""

    def __init__(self, stdout_lines: list[str] | None = None) -> None:
        self.pid = 1234
        self._stdout_buffer = b''.join(
            (line + '\n').encode('utf-8') for line in (stdout_lines or [])
        )
        self.stdout = io.BytesIO(self._stdout_buffer)
        self.stderr = io.BytesIO(b'')
        self.stdin = MagicMock()
        self.stdin.write = MagicMock()
        self.stdin.flush = MagicMock()
        self.stdin.close = MagicMock()
        self._returncode: int | None = None
        self._wait_event = threading.Event()
        self.signals_sent: list[int] = []
        self._exit_after_close = True

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        if self._returncode is not None:
            return self._returncode
        if self._exit_after_close:
            self._returncode = 0
            return 0
        if timeout is None:
            self._wait_event.wait()
            return self._returncode or 0
        if not self._wait_event.wait(timeout):
            import subprocess
            raise subprocess.TimeoutExpired(cmd=['claude'], timeout=timeout)
        return self._returncode or 0

    def send_signal(self, sig):
        self.signals_sent.append(sig)
        self._returncode = -sig

    def force_exit(self, returncode: int = 0) -> None:
        self._returncode = returncode
        self._wait_event.set()


class StreamingClaudeSessionTests(unittest.TestCase):
    def test_start_requires_task_id(self) -> None:
        with self.assertRaisesRegex(ValueError, 'task_id is required'):
            StreamingClaudeSession(task_id='')

    def test_start_launches_subprocess_and_pins_session_id(self) -> None:
        fake_proc = _FakeProc(stdout_lines=[
            json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 'live-123'}),
        ])
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ) as mock_popen, patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1', cwd='/tmp')
            session.start()

        cmd = mock_popen.call_args.args[0]
        self.assertIn('-p', cmd)
        self.assertIn('--output-format', cmd)
        self.assertIn('stream-json', cmd)
        self.assertIn('--input-format', cmd)
        # A session-id is pinned up front so a restart can resume it.
        self.assertIn('--session-id', cmd)
        # After the init event arrives, agent_session_id adopts the id
        # Claude actually confirmed in its init event (not necessarily the
        # pinned UUID kato passed via --session-id; if Claude reports a
        # different id the session corrects to that actual id so the next
        # --resume targets the right JSONL).
        self.assertEqual(session.agent_session_id, 'live-123')

    def test_allowed_additional_dirs_returns_spawn_time_paths(self) -> None:
        # The Claude CLI bakes ``--add-dir`` into the subprocess at
        # spawn time — there is no in-flight widening API. Callers
        # use ``allowed_additional_dirs()`` to ask "was this path
        # part of the original spawn?" so they can flag operators
        # to restart the chat tab when new repos are cloned later.
        session = StreamingClaudeSession(
            task_id='UNA-1',
            cwd='/wks/UNA-1/backend',
            additional_dirs=['/wks/UNA-1/client', '/wks/UNA-1/core-lib'],
        )
        dirs = session.allowed_additional_dirs()
        self.assertIsInstance(dirs, tuple)
        self.assertEqual(
            dirs, ('/wks/UNA-1/client', '/wks/UNA-1/core-lib'),
        )

    def test_allowed_additional_dirs_empty_when_no_extras(self) -> None:
        # Single-repo task: no extra dirs at spawn → empty tuple,
        # NOT None. Callers iterate the result; None would explode.
        session = StreamingClaudeSession(
            task_id='UNA-2',
            cwd='/wks/UNA-2/only',
        )
        self.assertEqual(session.allowed_additional_dirs(), ())

    def test_allowed_additional_dirs_filters_blank_entries(self) -> None:
        # The constructor strips blanks (matches the existing
        # normalization in __init__). Returned tuple must mirror
        # that — the public accessor is the "truth" about what the
        # subprocess actually got.
        session = StreamingClaudeSession(
            task_id='UNA-3',
            cwd='/wks/UNA-3/only',
            additional_dirs=['', '  ', '/wks/UNA-3/real', None],
        )
        self.assertEqual(
            session.allowed_additional_dirs(),
            ('/wks/UNA-3/real',),
        )

    def test_allowed_additional_dirs_is_immutable_snapshot(self) -> None:
        # Returned tuple cannot be mutated to widen the sandbox
        # post-hoc. Defends against a misguided caller that grabs
        # the result and tries to append to it; tuples don't
        # support .append, so the surface is structurally read-only.
        session = StreamingClaudeSession(
            task_id='UNA-4',
            cwd='/wks/UNA-4/only',
            additional_dirs=['/wks/UNA-4/extra'],
        )
        dirs = session.allowed_additional_dirs()
        with self.assertRaises(AttributeError):
            dirs.append('/etc/passwd')  # type: ignore[attr-defined]

    def test_start_with_additional_dirs_emits_add_dir_per_path(self) -> None:
        # Multi-repo tasks need every repo accessible to Claude, not
        # just the cwd one. Without this the chat agent sees only its
        # cwd and refuses cross-repo questions ("verify the front
        # end" → "frontend repo is forbidden") because the only
        # frontend-named entry it knows about came from
        # ``KATO_IGNORED_REPOSITORY_FOLDERS``. ``--add-dir`` per
        # sibling repo path is what fixes that.
        fake_proc = _FakeProc()
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ) as mock_popen, patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(
                task_id='UNA-2489',
                cwd='/wks/UNA-2489/ob-love-admin-backend',
                additional_dirs=[
                    '/wks/UNA-2489/ob-love-admin-client',
                    '/wks/UNA-2489/workflow-core-lib',
                ],
            )
            session.start()
        cmd = mock_popen.call_args.args[0]
        # Every additional dir should produce a ``--add-dir <path>``
        # pair. We don't assert ordering against other flags, only
        # that each pair is present in sequence.
        add_dir_indices = [i for i, a in enumerate(cmd) if a == '--add-dir']
        self.assertEqual(len(add_dir_indices), 2)
        add_dir_values = [cmd[i + 1] for i in add_dir_indices]
        self.assertIn('/wks/UNA-2489/ob-love-admin-client', add_dir_values)
        self.assertIn('/wks/UNA-2489/workflow-core-lib', add_dir_values)

    def test_start_with_resume_id_passes_resume_flag_only(self) -> None:
        # ``claude --resume <id>`` keeps the same session id by default
        # (forking is opt-in via ``--fork-session``), so we don't pass
        # ``--session-id`` alongside it — Claude rejects the duplicate
        # and the spawn would fail before the adopted session can run.
        # The resumed id is captured synchronously
        # so the UI can show the right chip before the system_init
        # event arrives.
        fake_proc = _FakeProc()
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ) as mock_popen, patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(
                task_id='PROJ-1',
                resume_session_id='  earlier-session-uuid\n',
            )
            session.start()
        cmd = mock_popen.call_args.args[0]
        self.assertEqual(session.agent_session_id, 'earlier-session-uuid')
        self.assertIn('--resume', cmd)
        self.assertEqual(
            cmd[cmd.index('--resume') + 1],
            'earlier-session-uuid',
        )
        # We deliberately don't pass --session-id when resuming;
        # Claude keeps the resumed id without it.
        self.assertNotIn('--session-id', cmd)

    def test_spawn_log_prints_fresh_session_id(self) -> None:
        # The "sustain the print" line: every spawn must log the id +
        # whether it is fresh, so an operator can diff one task across
        # a kato restart.
        fake_proc = _FakeProc()
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            with self.assertLogs(
                'kato.workflow.StreamingClaudeSession', level='INFO',
            ) as cm:
                session.start()
        joined = ' '.join(cm.output)
        self.assertIn('fresh session id', joined)
        self.assertIn(session.agent_session_id, joined)
        self.assertNotIn('resuming session id', joined)

    def test_spawn_log_prints_resuming_session_id(self) -> None:
        fake_proc = _FakeProc()
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(
                task_id='PROJ-1', resume_session_id='keep-me-123',
            )
            with self.assertLogs(
                'kato.workflow.StreamingClaudeSession', level='INFO',
            ) as cm:
                session.start()
        joined = ' '.join(cm.output)
        self.assertIn('resuming session id keep-me-123', joined)

    def test_send_user_message_writes_ndjson_envelope(self) -> None:
        fake_proc = _FakeProc()
        fake_proc._exit_after_close = False
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            session.start()
            session.send_user_message('please add a hover state')

        fake_proc.stdin.write.assert_called_once()
        written_bytes = fake_proc.stdin.write.call_args.args[0]
        self.assertTrue(written_bytes.endswith(b'\n'))
        payload = json.loads(written_bytes.decode('utf-8').strip())
        self.assertEqual(payload['type'], 'user')
        self.assertEqual(
            payload['message']['content'][0]['text'],
            'please add a hover state',
        )
        # Cleanup: trigger graceful exit so the daemon threads stop.
        fake_proc.force_exit()
        session.terminate(grace_seconds=0.2)

    def test_send_user_message_with_images_appends_image_blocks(self) -> None:
        fake_proc = _FakeProc()
        fake_proc._exit_after_close = False
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            session.start()
            session.send_user_message(
                'look at this',
                images=[
                    {'media_type': 'image/png', 'data': 'AAAA'},
                    {'media_type': 'image/jpeg', 'data': 'BBBB'},
                ],
            )

        written_bytes = fake_proc.stdin.write.call_args.args[0]
        payload = json.loads(written_bytes.decode('utf-8').strip())
        content = payload['message']['content']
        # Text comes first, then one block per image.
        self.assertEqual(content[0]['type'], 'text')
        self.assertEqual(content[0]['text'], 'look at this')
        self.assertEqual(content[1]['type'], 'image')
        self.assertEqual(content[1]['source']['media_type'], 'image/png')
        self.assertEqual(content[1]['source']['data'], 'AAAA')
        self.assertEqual(content[2]['type'], 'image')
        self.assertEqual(content[2]['source']['media_type'], 'image/jpeg')

        fake_proc.force_exit()
        session.terminate(grace_seconds=0.2)

    def test_send_user_message_with_only_images_skips_text_block(self) -> None:
        fake_proc = _FakeProc()
        fake_proc._exit_after_close = False
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            session.start()
            session.send_user_message(
                '',
                images=[{'media_type': 'image/png', 'data': 'AAAA'}],
            )

        written_bytes = fake_proc.stdin.write.call_args.args[0]
        payload = json.loads(written_bytes.decode('utf-8').strip())
        content = payload['message']['content']
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]['type'], 'image')

        fake_proc.force_exit()
        session.terminate(grace_seconds=0.2)

    def test_send_user_message_drops_unsupported_media_types(self) -> None:
        fake_proc = _FakeProc()
        fake_proc._exit_after_close = False
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            session.start()
            session.send_user_message(
                'check',
                images=[
                    {'media_type': 'image/tiff', 'data': 'AAAA'},  # unsupported
                    {'media_type': 'image/png', 'data': 'BBBB'},
                    {'media_type': '', 'data': 'CCCC'},  # missing
                    {'media_type': 'image/png', 'data': ''},  # empty data
                ],
            )

        written_bytes = fake_proc.stdin.write.call_args.args[0]
        payload = json.loads(written_bytes.decode('utf-8').strip())
        content = payload['message']['content']
        # Text + one valid image survives; the rest are dropped.
        image_blocks = [b for b in content if b.get('type') == 'image']
        self.assertEqual(len(image_blocks), 1)
        self.assertEqual(image_blocks[0]['source']['data'], 'BBBB')

        fake_proc.force_exit()
        session.terminate(grace_seconds=0.2)

    def test_send_user_message_with_no_text_and_no_images_is_noop(self) -> None:
        fake_proc = _FakeProc()
        fake_proc._exit_after_close = False
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            session.start()
            session.send_user_message('', images=[])

        # No write happened — empty payload is silently dropped.
        fake_proc.stdin.write.assert_not_called()
        fake_proc.force_exit()
        session.terminate(grace_seconds=0.2)

    def test_send_permission_response_writes_control_response_envelope(self) -> None:
        fake_proc = _FakeProc()
        fake_proc._exit_after_close = False
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            session.start()
            # Stash a captured request so allow echoes the original input
            # back as ``updatedInput`` (the real wire contract for
            # ``--permission-prompt-tool stdio``).
            with session._pending_control_requests_lock:
                session._pending_control_requests['req-77'] = {
                    'tool_name': 'Bash',
                    'input': {'command': 'ls /tmp'},
                }
            session.send_permission_response('req-77', allow=True, rationale='ok')

        written = fake_proc.stdin.write.call_args.args[0]
        payload = json.loads(written.decode('utf-8').strip())
        self.assertEqual(payload['type'], 'control_response')
        response = payload['response']
        self.assertEqual(response['subtype'], 'success')
        self.assertEqual(response['request_id'], 'req-77')
        decision = response['response']
        self.assertEqual(decision['behavior'], 'allow')
        self.assertEqual(decision['updatedInput'], {'command': 'ls /tmp'})
        fake_proc.force_exit()
        session.terminate(grace_seconds=0.2)

    def test_send_permission_response_deny_carries_rationale(self) -> None:
        fake_proc = _FakeProc()
        fake_proc._exit_after_close = False
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            session.start()
            session.send_permission_response('req-99', allow=False, rationale='not safe')

        written = fake_proc.stdin.write.call_args.args[0]
        payload = json.loads(written.decode('utf-8').strip())
        decision = payload['response']['response']
        self.assertEqual(decision['behavior'], 'deny')
        self.assertEqual(decision['message'], 'not safe')
        fake_proc.force_exit()
        session.terminate(grace_seconds=0.2)

    def test_send_user_message_raises_when_subprocess_dead(self) -> None:
        session = StreamingClaudeSession(task_id='PROJ-1')
        with self.assertRaisesRegex(RuntimeError, 'subprocess is not running'):
            session.send_user_message('hi')

    def test_events_iter_yields_until_terminal(self) -> None:
        fake_proc = _FakeProc(stdout_lines=[
            json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 's1'}),
            json.dumps({'type': 'assistant', 'message': {'role': 'assistant'}}),
            json.dumps({'type': 'result', 'subtype': 'success',
                        'is_error': False, 'result': 'done'}),
        ])
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            session.start()
            collected: list[SessionEvent] = []
            # Wait briefly for reader thread to drain the stdout buffer.
            for _ in range(40):
                if len(session.recent_events()) >= 3:
                    break
                time.sleep(0.05)
            for event in session.events_iter():
                collected.append(event)
                if event.is_terminal:
                    break

        self.assertEqual([event.event_type for event in collected],
                         ['system', 'assistant', 'result'])
        self.assertTrue(collected[-1].is_terminal)
        self.assertIs(session.terminal_event, collected[-1])

    def test_terminate_closes_stdin_and_kills_after_grace(self) -> None:
        fake_proc = _FakeProc()
        fake_proc._exit_after_close = False  # simulate hung subprocess
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-1')
            session.start()
            session.terminate(grace_seconds=0.1)

        fake_proc.stdin.close.assert_called_once()
        # SIGTERM is the first escalation after the grace window.
        self.assertIn(15, fake_proc.signals_sent)


class StreamingClaudeSessionPureMethodTests(unittest.TestCase):
    """Methods that can be tested without a live subprocess.

    These exercise the parsing, event-classification, and detection
    logic — the SSE tail loop, the stale-resume detector, the wait-planning
    done-sentinel detection, and similar — by constructing fake events
    or instantiating a session and exercising the helper directly.
    """

    def _build_session(self, *, resume_session_id: str = '') -> StreamingClaudeSession:
        # Instantiate without ``start()`` — we only need the helper methods.
        return StreamingClaudeSession(
            task_id='PROJ-1',
            cwd='/tmp/repo',
            resume_session_id=resume_session_id,
        )

    def test_permission_request_details_reads_top_level_fields(self) -> None:
        event = SessionEvent(raw={
            'type': 'permission_request',
            'tool_name': 'Bash',
            'request_id': 'req-1',
        })
        tool, req_id = StreamingClaudeSession._permission_request_details(event)
        self.assertEqual(tool, 'Bash')
        self.assertEqual(req_id, 'req-1')

    def test_permission_request_details_reads_nested_request_object(self) -> None:
        # ``--permission-prompt-tool stdio`` nests the fields under request.
        event = SessionEvent(raw={
            'type': 'control_request',
            'id': 'req-99',
            'request': {'tool_name': 'Edit'},
        })
        tool, req_id = StreamingClaudeSession._permission_request_details(event)
        self.assertEqual(tool, 'Edit')
        self.assertEqual(req_id, 'req-99')

    def test_permission_request_details_falls_back_to_placeholders(self) -> None:
        event = SessionEvent(raw={'type': 'permission_request'})
        tool, req_id = StreamingClaudeSession._permission_request_details(event)
        self.assertEqual(tool, 'tool')
        self.assertEqual(req_id, '?')

    def test_parse_stdout_line_returns_none_on_non_json(self) -> None:
        session = self._build_session()
        # Bypass the noisy logger by replacing it.
        session.logger = MagicMock()
        result = session._parse_stdout_line('not-json{}')
        self.assertIsNone(result)
        session.logger.warning.assert_called_once()

    def test_parse_stdout_line_returns_none_when_payload_not_dict(self) -> None:
        session = self._build_session()
        session.logger = MagicMock()
        # Valid JSON but not a dict.
        result = session._parse_stdout_line('[1, 2, 3]')
        self.assertIsNone(result)

    def test_parse_stdout_line_builds_event_from_dict_payload(self) -> None:
        session = self._build_session()
        event = session._parse_stdout_line('{"type": "system", "subtype": "init"}')
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, 'system')
        self.assertEqual(event.subtype, 'init')

    def test_stderr_indicates_stale_resume_false_when_no_resume_id(self) -> None:
        session = self._build_session(resume_session_id='')
        # No resume id configured → marker check short-circuits.
        self.assertFalse(session._stderr_indicates_stale_resume([
            'No conversation found with session ID: anything',
        ]))

    def test_stderr_indicates_stale_resume_true_when_marker_present(self) -> None:
        session = self._build_session(resume_session_id='dead-uuid')
        self.assertTrue(session._stderr_indicates_stale_resume([
            'some chatter',
            'No conversation found with session ID: dead-uuid',
            'more chatter',
        ]))

    def test_stderr_indicates_stale_resume_false_when_marker_missing(self) -> None:
        session = self._build_session(resume_session_id='alive-uuid')
        self.assertFalse(session._stderr_indicates_stale_resume([
            'No conversation found with session ID: different-uuid',
            'something else',
        ]))

    def test_maybe_capture_session_id_pins_first_session_id_only(self) -> None:
        session = self._build_session()
        # First init event with session_id → pinned.
        session._maybe_capture_session_id(SessionEvent(raw={
            'type': 'system', 'subtype': 'init', 'session_id': 'first',
        }))
        self.assertEqual(session.agent_session_id, 'first')
        # Subsequent events with different ids must not overwrite.
        session._maybe_capture_session_id(SessionEvent(raw={
            'type': 'system', 'session_id': 'second',
        }))
        self.assertEqual(session.agent_session_id, 'first')

    def test_maybe_capture_session_id_ignores_empty_candidate(self) -> None:
        session = self._build_session()
        session._maybe_capture_session_id(SessionEvent(raw={
            'type': 'system', 'session_id': '',
        }))
        self.assertEqual(session.agent_session_id, '')

    def test_capture_confirms_matching_resumed_id_once(self) -> None:
        session = self._build_session(resume_session_id='sess-abc')
        # Production: id is pinned in _build_command before any event.
        session._agent_session_id = 'sess-abc'
        session.logger = MagicMock()
        ev = SessionEvent(raw={
            'type': 'system', 'subtype': 'init', 'session_id': 'sess-abc',
        })
        session._maybe_capture_session_id(ev)
        session._maybe_capture_session_id(ev)  # second init: no dup log
        self.assertEqual(session.agent_session_id, 'sess-abc')
        session.logger.info.assert_called_once()
        self.assertIn('confirmed', session.logger.info.call_args.args[0])
        session.logger.warning.assert_not_called()

    def test_capture_warns_once_on_fresh_session_id_mismatch(self) -> None:
        session = self._build_session(resume_session_id='')
        session._agent_session_id = 'generated-id'
        session.logger = MagicMock()
        corrections: list[str] = []
        session._session_id_correction_callback = corrections.append
        ev = SessionEvent(raw={
            'type': 'system', 'subtype': 'init', 'session_id': 'actual-id',
        })
        session._maybe_capture_session_id(ev)
        session._maybe_capture_session_id(ev)  # only one warning, one correction
        self.assertEqual(session.agent_session_id, 'actual-id')
        self.assertEqual(corrections, ['actual-id'])
        session.logger.warning.assert_called_once()
        self.assertIn(
            'adopting', session.logger.warning.call_args.args[-1],
        )
        session.logger.info.assert_not_called()

    def test_capture_warns_once_on_resumed_session_id_mismatch(self) -> None:
        session = self._build_session(resume_session_id='sess-abc')
        session._agent_session_id = 'sess-abc'
        session.logger = MagicMock()
        corrections: list[str] = []
        session._session_id_correction_callback = corrections.append
        ev = SessionEvent(raw={
            'type': 'system', 'subtype': 'init', 'session_id': 'sess-zzz',
        })
        session._maybe_capture_session_id(ev)
        session._maybe_capture_session_id(ev)  # only one warning, one correction
        self.assertEqual(session.agent_session_id, 'sess-abc')
        self.assertEqual(corrections, [])
        session.logger.warning.assert_called_once()
        self.assertIn(
            'keeping', session.logger.warning.call_args.args[-1],
        )
        session.logger.info.assert_not_called()

    def test_capture_swallows_correction_callback_exception(self) -> None:
        # If the session_id_correction_callback raises (e.g. the manager
        # can't persist the corrected id because the state dir went
        # read-only), the streaming session must NOT crash mid-event.
        # The exception is logged and the stream continues.
        session = self._build_session(resume_session_id='')
        session._agent_session_id = 'generated-id'
        session.logger = MagicMock()

        def broken_callback(_actual_id):
            raise RuntimeError('persist failed')

        session._session_id_correction_callback = broken_callback
        ev = SessionEvent(raw={
            'type': 'system', 'subtype': 'init', 'session_id': 'actual-id',
        })
        # Must NOT raise.
        session._maybe_capture_session_id(ev)
        # The callback exception is logged via logger.exception.
        session.logger.exception.assert_called_once()
        msg = session.logger.exception.call_args.args[0]
        self.assertIn('session_id_correction_callback raised', msg)
        self.assertEqual(session.agent_session_id, 'actual-id')

    def test_maybe_fire_done_sentinel_fires_callback_once(self) -> None:
        callback_calls: list = []
        session = self._build_session()
        session._done_callback = callback_calls.append

        sentinel_event = SessionEvent(raw={
            'type': 'assistant',
            'message': {
                'content': [
                    {'type': 'text', 'text': 'all set <KATO_TASK_DONE>'},
                ],
            },
        })
        session._maybe_fire_done_sentinel(sentinel_event)
        session._maybe_fire_done_sentinel(sentinel_event)  # second fire → no-op
        self.assertEqual(callback_calls, ['PROJ-1'])

    def test_maybe_fire_done_sentinel_no_op_when_no_callback(self) -> None:
        session = self._build_session()
        session._done_callback = None
        session._maybe_fire_done_sentinel(SessionEvent(raw={
            'type': 'assistant',
            'message': {'content': [{'type': 'text', 'text': '<KATO_TASK_DONE>'}]},
        }))
        # Must not raise — already covered by the early return.

    def test_maybe_fire_done_sentinel_skips_non_assistant_events(self) -> None:
        callback_calls: list = []
        session = self._build_session()
        session._done_callback = callback_calls.append
        session._maybe_fire_done_sentinel(SessionEvent(raw={
            'type': 'user',
            'message': {'content': [{'type': 'text', 'text': '<KATO_TASK_DONE>'}]},
        }))
        self.assertEqual(callback_calls, [])

    def test_maybe_fire_done_sentinel_skips_when_message_missing(self) -> None:
        session = self._build_session()
        session._done_callback = MagicMock()
        session._maybe_fire_done_sentinel(SessionEvent(raw={'type': 'assistant'}))
        session._done_callback.assert_not_called()

    def test_maybe_fire_done_sentinel_swallows_callback_exception(self) -> None:
        session = self._build_session()
        session.logger = MagicMock()

        def broken_callback(_task_id):
            raise RuntimeError('publish failed')

        session._done_callback = broken_callback
        # Must not propagate — reader thread can't crash on a bad callback.
        session._maybe_fire_done_sentinel(SessionEvent(raw={
            'type': 'assistant',
            'message': {'content': [{'type': 'text', 'text': '<KATO_TASK_DONE>'}]},
        }))
        session.logger.exception.assert_called_once()

    def test_pending_control_request_tool_returns_empty_when_none_pending(self) -> None:
        session = self._build_session()
        self.assertEqual(session.pending_control_request_tool(), '')

    def test_pending_control_request_tool_returns_tool_name(self) -> None:
        session = self._build_session()
        session._maybe_capture_control_request(SessionEvent(raw={
            'type': 'control_request',
            'request_id': 'req-1',
            'request': {'tool_name': 'Bash'},
        }))
        self.assertEqual(session.pending_control_request_tool(), 'Bash')

    def test_maybe_capture_control_request_ignores_non_control_event(self) -> None:
        session = self._build_session()
        session._maybe_capture_control_request(SessionEvent(raw={
            'type': 'permission_request',
            'request_id': 'req-1',
            'request': {'tool_name': 'Bash'},
        }))
        self.assertEqual(session.pending_control_request_tool(), '')

    def test_maybe_capture_control_request_ignores_blank_request_id(self) -> None:
        session = self._build_session()
        session._maybe_capture_control_request(SessionEvent(raw={
            'type': 'control_request',
            'request_id': '',
            'request': {'tool_name': 'Bash'},
        }))
        self.assertEqual(session.pending_control_request_tool(), '')

    def test_maybe_capture_control_request_ignores_non_dict_request(self) -> None:
        session = self._build_session()
        session._maybe_capture_control_request(SessionEvent(raw={
            'type': 'control_request',
            'request_id': 'req-1',
            'request': 'not a dict',
        }))
        self.assertEqual(session.pending_control_request_tool(), '')

    def test_stderr_snapshot_returns_copy_of_lines(self) -> None:
        session = self._build_session()
        session._stderr_lines.extend(['line a', 'line b'])
        snapshot = session.stderr_snapshot()
        self.assertEqual(snapshot, ['line a', 'line b'])
        # Mutating the snapshot must not affect the session's buffer.
        snapshot.append('mutated')
        self.assertEqual(session.stderr_snapshot(), ['line a', 'line b'])

    def test_events_after_returns_empty_when_index_past_end(self) -> None:
        session = self._build_session()
        # No events appended yet → empty + index 0.
        events, idx = session.events_after(0)
        self.assertEqual(events, [])
        self.assertEqual(idx, 0)
        # Past-end index → empty + same total.
        session._recent_events.append(SessionEvent(raw={'type': 'system'}))
        events, idx = session.events_after(99)
        self.assertEqual(events, [])
        self.assertEqual(idx, 1)

    def test_events_after_returns_slice_from_index(self) -> None:
        session = self._build_session()
        evts = [SessionEvent(raw={'type': 'system', 'n': i}) for i in range(3)]
        for e in evts:
            session._recent_events.append(e)
        events, idx = session.events_after(1)
        self.assertEqual(len(events), 2)
        self.assertEqual(idx, 3)

    def test_events_after_handles_negative_index(self) -> None:
        session = self._build_session()
        session._recent_events.append(SessionEvent(raw={'type': 'system'}))
        # Negative start clamps to 0.
        events, idx = session.events_after(-5)
        self.assertEqual(len(events), 1)
        self.assertEqual(idx, 1)

    def test_recent_events_with_limit_returns_tail_only(self) -> None:
        session = self._build_session()
        for i in range(5):
            session._recent_events.append(SessionEvent(raw={'type': 'system', 'n': i}))
        # limit=2 → last 2 events.
        result = session.recent_events(limit=2)
        self.assertEqual(len(result), 2)

    def test_recent_events_no_limit_returns_all(self) -> None:
        session = self._build_session()
        for i in range(3):
            session._recent_events.append(SessionEvent(raw={'type': 'system'}))
        self.assertEqual(len(session.recent_events()), 3)

    def test_has_finished_false_before_terminal_event(self) -> None:
        session = self._build_session()
        self.assertFalse(session.has_finished)
        self.assertIsNone(session.terminal_event)

    def test_has_finished_true_after_terminal_event_assigned(self) -> None:
        session = self._build_session()
        terminal = SessionEvent(raw={
            'type': 'result', 'subtype': 'final', 'is_error': False,
        })
        session._terminal_event = terminal
        self.assertTrue(session.has_finished)
        self.assertIs(session.terminal_event, terminal)

    def test_is_working_false_when_not_alive(self) -> None:
        session = self._build_session()
        # No subprocess → not alive → not working.
        self.assertFalse(session.is_working)

    def test_session_event_is_terminal_true_for_result(self) -> None:
        event = SessionEvent(raw={'type': 'result'})
        self.assertTrue(event.is_terminal)

    def test_session_event_is_terminal_false_for_normal_types(self) -> None:
        for et in ('system', 'assistant', 'stream_event', 'user'):
            event = SessionEvent(raw={'type': et})
            self.assertFalse(event.is_terminal)

    def test_session_event_to_dict_wraps_raw(self) -> None:
        # ``to_dict`` envelopes the raw payload plus metadata; check structure.
        raw = {'type': 'system', 'subtype': 'init'}
        d = SessionEvent(raw=raw).to_dict()
        self.assertEqual(d['raw'], raw)
        self.assertIn('received_at_epoch', d)

    def test_session_event_subtype_pulled_from_raw(self) -> None:
        # Property access on raw {'subtype': ...}.
        event = SessionEvent(raw={'type': 'system', 'subtype': 'init'})
        self.assertEqual(event.subtype, 'init')

    def test_task_id_and_cwd_properties_round_trip(self) -> None:
        session = StreamingClaudeSession(task_id='PROJ-A', cwd='/repo/x')
        self.assertEqual(session.task_id, 'PROJ-A')
        self.assertEqual(session.cwd, '/repo/x')

    def test_is_working_returns_true_during_assistant_streaming(self) -> None:
        # ``is_working`` reads the recent-events log. We don't have a live
        # subprocess (so ``is_alive`` is False) → it short-circuits to False.
        # Cover the inverse by faking a live process.
        session = self._build_session()
        session._proc = SimpleNamespace(poll=lambda: None)  # alive
        session._recent_events.append(SessionEvent(raw={'type': 'assistant'}))
        self.assertTrue(session.is_working)

    def test_is_working_returns_false_after_result_event(self) -> None:
        session = self._build_session()
        session._proc = SimpleNamespace(poll=lambda: None)  # alive
        session._recent_events.append(SessionEvent(raw={'type': 'assistant'}))
        session._recent_events.append(SessionEvent(raw={'type': 'result'}))
        # Last event is ``result`` → turn closed → not working.
        self.assertFalse(session.is_working)

    def test_is_working_returns_false_when_only_system_events(self) -> None:
        session = self._build_session()
        session._proc = SimpleNamespace(poll=lambda: None)  # alive
        session._recent_events.append(SessionEvent(raw={'type': 'system'}))
        # No assistant/result events → not actively working.
        self.assertFalse(session.is_working)

    def test_validate_image_blocks_rejects_non_list_input(self) -> None:
        from claude_core_lib.claude_core_lib.session.streaming import _validate_image_blocks
        self.assertEqual(_validate_image_blocks('not a list'), [])
        self.assertEqual(_validate_image_blocks(None), [])

    def test_validate_image_blocks_drops_non_dict_entries(self) -> None:
        from claude_core_lib.claude_core_lib.session.streaming import _validate_image_blocks
        blocks = _validate_image_blocks(['plain string', {'media_type': 'image/png', 'data': 'abc'}])
        self.assertEqual(len(blocks), 1)

    def test_validate_image_blocks_drops_unknown_media_type(self) -> None:
        from claude_core_lib.claude_core_lib.session.streaming import _validate_image_blocks
        self.assertEqual(
            _validate_image_blocks([{'media_type': 'video/mp4', 'data': 'x'}]),
            [],
        )

    def test_validate_image_blocks_drops_oversized_entries(self) -> None:
        from claude_core_lib.claude_core_lib.session.streaming import (
            _validate_image_blocks, _MAX_IMAGE_BYTES,
        )
        # Data > base64-expanded cap → dropped.
        oversized = 'x' * (int(_MAX_IMAGE_BYTES * 4 / 3) + 2000)
        self.assertEqual(
            _validate_image_blocks([{'media_type': 'image/png', 'data': oversized}]),
            [],
        )

    def test_validate_image_blocks_drops_empty_data(self) -> None:
        from claude_core_lib.claude_core_lib.session.streaming import _validate_image_blocks
        self.assertEqual(
            _validate_image_blocks([{'media_type': 'image/png', 'data': ''}]),
            [],
        )

    def test_events_iter_yields_terminal_and_stops(self) -> None:
        # The iterator yields events until the terminal event, then stops.
        session = self._build_session()
        session._proc = SimpleNamespace(poll=lambda: None)
        # Queue one normal event + one terminal event.
        session._event_queue.put(SessionEvent(raw={'type': 'assistant'}))
        session._event_queue.put(SessionEvent(raw={'type': 'result'}))
        collected = list(session.events_iter())
        self.assertEqual(len(collected), 2)
        self.assertTrue(collected[-1].is_terminal)

    def test_poll_event_returns_none_when_queue_empty(self) -> None:
        session = self._build_session()
        # Don't put anything → poll returns None.
        self.assertIsNone(session.poll_event(timeout=0.01))

    def test_poll_event_returns_queued_event(self) -> None:
        session = self._build_session()
        evt = SessionEvent(raw={'type': 'system'})
        session._event_queue.put(evt)
        self.assertIs(session.poll_event(timeout=0.5), evt)

    def test_pending_control_request_tool_returns_unknown_for_missing_name(self) -> None:
        # The pending request dict is present but has no tool name → '<unknown>'.
        session = self._build_session()
        session._maybe_capture_control_request(SessionEvent(raw={
            'type': 'control_request',
            'request_id': 'req-1',
            'request': {'something_else': 'value'},
        }))
        self.assertEqual(session.pending_control_request_tool(), '<unknown>')

    def test_pending_control_request_tool_skips_non_dict_request(self) -> None:
        # Defensive: stale entries where the request shape got corrupted.
        session = self._build_session()
        with session._pending_control_requests_lock:
            session._pending_control_requests['req-1'] = 'not a dict'
        self.assertEqual(session.pending_control_request_tool(), '')

    def test_stderr_reader_loop_returns_when_proc_or_stderr_missing(self) -> None:
        # Lines 1014-1015: defensive early return.
        session = self._build_session()
        session._proc = None
        session._stderr_reader_loop()  # must not raise

        session._proc = SimpleNamespace(stderr=None)
        session._stderr_reader_loop()  # must not raise

    def test_stdout_reader_loop_returns_when_proc_or_stdout_missing(self) -> None:
        # Same defensive early return on the stdout reader.
        session = self._build_session()
        session._proc = None
        session._stdout_reader_loop()  # must not raise

        session._proc = SimpleNamespace(stdout=None)
        session._stdout_reader_loop()  # must not raise

    def test_close_stdin_locked_no_op_when_proc_missing(self) -> None:
        session = self._build_session()
        session._proc = None
        session._close_stdin_locked()  # must not raise

    def test_close_stdin_locked_swallows_close_exception(self) -> None:
        # Some pipes raise when ``close()`` is double-called; swallow.
        session = self._build_session()
        fake_stdin = MagicMock()
        fake_stdin.close.side_effect = OSError('already closed')
        session._proc = SimpleNamespace(stdin=fake_stdin)
        session._close_stdin_locked()  # must not raise

    def test_send_signal_locked_no_op_when_proc_missing(self) -> None:
        import signal as _signal
        session = self._build_session()
        session._proc = None
        session._send_signal_locked(_signal.SIGTERM)  # must not raise

    def test_send_signal_locked_swallows_oserror(self) -> None:
        import signal as _signal
        session = self._build_session()
        fake_proc = MagicMock()
        fake_proc.send_signal.side_effect = ProcessLookupError('gone')
        session._proc = fake_proc
        session._send_signal_locked(_signal.SIGTERM)  # must not raise

    def test_write_stdin_line_raises_when_proc_missing(self) -> None:
        session = self._build_session()
        with self.assertRaisesRegex(RuntimeError, 'stdin closed'):
            session._write_stdin_line({'type': 'user'})

    def test_write_stdin_line_raises_on_broken_pipe(self) -> None:
        session = self._build_session()
        fake_stdin = MagicMock()
        fake_stdin.write.side_effect = BrokenPipeError('pipe gone')
        session._proc = SimpleNamespace(
            stdin=fake_stdin,
            poll=lambda: None,
        )
        with self.assertRaisesRegex(RuntimeError, 'stdin broke'):
            session._write_stdin_line({'type': 'user'})

    def test_send_user_message_raises_when_proc_dead(self) -> None:
        # Already-dead proc → no write should be attempted.
        session = self._build_session()
        # stdin is non-None but poll() returns non-None (process exited).
        session._proc = SimpleNamespace(stdin=MagicMock(), poll=lambda: 1)
        with self.assertRaisesRegex(RuntimeError, 'stdin closed'):
            session._write_stdin_line({'type': 'user'})

    def test_send_permission_response_requires_request_id(self) -> None:
        session = self._build_session()
        with self.assertRaisesRegex(ValueError, 'request_id is required'):
            session.send_permission_response(request_id='', allow=True)
        with self.assertRaisesRegex(ValueError, 'request_id is required'):
            session.send_permission_response(request_id='   ', allow=True)

    def test_bypass_permissions_clears_prompt_tool(self) -> None:
        # Line 183-185: ``bypassPermissions`` with no prompt tool → tool cleared.
        session = StreamingClaudeSession(
            task_id='PROJ-1',
            permission_mode='bypassPermissions',
            permission_prompt_tool='',
        )
        self.assertEqual(session._permission_prompt_tool, '')

    def test_explicit_prompt_tool_overrides_default(self) -> None:
        # Line 182: ``normalized_prompt_tool`` truthy → use it as-is.
        session = StreamingClaudeSession(
            task_id='PROJ-1',
            permission_prompt_tool='stdio',
        )
        self.assertEqual(session._permission_prompt_tool, 'stdio')

    def test_start_raises_when_already_started(self) -> None:
        # Line 326: starting an already-started session is a programming
        # error — raise so the caller knows their state machine is wrong.
        session = self._build_session()
        session._proc = SimpleNamespace(poll=lambda: None)  # already running
        with self.assertRaisesRegex(RuntimeError, 'already started'):
            session.start()

    def test_escalate_to_sigterm_returns_when_proc_exits_after_signal(self) -> None:
        # Line 558 happy branch: SIGTERM is sent; if the proc exits cleanly
        # within 2s, we don't escalate to kill.
        session = self._build_session()
        session.logger = MagicMock()
        fake = _FakeProc()
        fake._returncode = None
        session._proc = fake
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming._wait_for_exit',
            return_value=True,
        ):
            session._escalate_to_sigterm(fake)
        self.assertIn(15, fake.signals_sent)

    def test_escalate_to_sigterm_falls_through_to_kill_on_hang(self) -> None:
        # Line 558: SIGTERM-then-still-alive → _escalate_to_kill is invoked.
        session = self._build_session()
        session.logger = MagicMock()
        fake = _FakeProc()
        session._proc = fake
        # First wait (post-SIGTERM) returns False → SIGKILL escalation;
        # second wait (post-SIGKILL inside _escalate_to_kill) returns True.
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming._wait_for_exit',
            side_effect=[False, True],
        ), patch.object(session, '_escalate_to_kill') as mock_kill:
            session._escalate_to_sigterm(fake)
        mock_kill.assert_called_once()

    def test_events_iter_yields_through_empty_queue_waits(self) -> None:
        # Lines 587-588: ``Empty`` exception → continue loop; eventually
        # session becomes not-alive AND queue empty → loop ends.
        session = self._build_session()
        # Session never had a proc → not alive.
        # Iterator should immediately end (queue empty + not alive).
        result = list(session.events_iter())
        self.assertEqual(result, [])

    def test_events_iter_drains_queue_after_session_dies(self) -> None:
        # Session is dead but queue has events → drain them before stopping.
        session = self._build_session()
        evt1 = SessionEvent(raw={'type': 'system'})
        evt2 = SessionEvent(raw={'type': 'result'})  # terminal
        session._event_queue.put(evt1)
        session._event_queue.put(evt2)
        result = list(session.events_iter())
        self.assertEqual(len(result), 2)

    def test_events_iter_continues_on_queue_timeout(self) -> None:
        # Lines 587-588: ``Empty`` from queue.get → ``continue`` loop.
        # Mock is_alive to flip from True → False between iterations so the
        # loop enters once (queue Empty), then exits cleanly.
        session = self._build_session()
        alive_responses = iter([True, False, False, False])
        with patch.object(
            type(session), 'is_alive',
            new_callable=unittest.mock.PropertyMock,
            side_effect=lambda: next(alive_responses),
        ):
            result = list(session.events_iter())
        self.assertEqual(result, [])

    def test_build_command_includes_model_max_turns_effort_allowed_tools(self) -> None:
        # Lines 688-694: optional CLI args appear when configured.
        session = StreamingClaudeSession(
            task_id='PROJ-X',
            model='claude-opus-4-7',
            max_turns=10,
            effort='high',
            allowed_tools='Bash,Edit',
        )
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            cmd = session._build_command()
        self.assertIn('--model', cmd)
        self.assertIn('claude-opus-4-7', cmd)
        self.assertIn('--max-turns', cmd)
        self.assertIn('10', cmd)
        self.assertIn('--effort', cmd)
        self.assertIn('high', cmd)
        self.assertIn('--allowedTools', cmd)
        self.assertIn('Bash,Edit', cmd)

    def test_start_raises_when_sandbox_image_prep_fails(self) -> None:
        # Lines 353-354: ensure_image raises SandboxError → wrapped as RuntimeError.
        from sandbox_core_lib.sandbox_core_lib.manager import SandboxError
        session = StreamingClaudeSession(
            task_id='PROJ-1', cwd='/tmp', docker_mode_on=True,
        )
        with patch(
            'sandbox_core_lib.sandbox_core_lib.manager.ensure_image',
            side_effect=SandboxError('image pull failed'),
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            with self.assertRaisesRegex(RuntimeError, 'sandbox image'):
                session.start()

    def test_start_raises_when_sandbox_rate_limited(self) -> None:
        # Lines 361-362: check_spawn_rate raises SandboxError.
        from sandbox_core_lib.sandbox_core_lib.manager import SandboxError
        session = StreamingClaudeSession(
            task_id='PROJ-2', cwd='/tmp', docker_mode_on=True,
        )
        with patch(
            'sandbox_core_lib.sandbox_core_lib.manager.ensure_image',
            return_value=None,
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.manager.check_spawn_rate',
            side_effect=SandboxError('too many spawns'),
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            with self.assertRaisesRegex(RuntimeError, 'rate-limited'):
                session.start()

    def test_start_raises_when_workspace_has_secrets(self) -> None:
        # Lines 374-375: enforce_no_workspace_secrets raises → blocked.
        from sandbox_core_lib.sandbox_core_lib.manager import SandboxError
        session = StreamingClaudeSession(
            task_id='PROJ-3', cwd='/tmp', docker_mode_on=True,
        )
        with patch(
            'sandbox_core_lib.sandbox_core_lib.manager.ensure_image',
            return_value=None,
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.manager.check_spawn_rate',
            return_value=None,
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.manager.enforce_no_workspace_secrets',
            side_effect=SandboxError('found .env with token'),
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            with self.assertRaisesRegex(RuntimeError, 'spawn blocked'):
                session.start()

    def test_start_raises_when_audit_log_fails(self) -> None:
        # Lines 396-397: record_spawn raises → audit log failure.
        from sandbox_core_lib.sandbox_core_lib.manager import SandboxError
        session = StreamingClaudeSession(
            task_id='PROJ-4', cwd='/tmp', docker_mode_on=True,
        )
        with patch.multiple(
            'sandbox_core_lib.sandbox_core_lib.manager',
            ensure_image=MagicMock(return_value=None),
            check_spawn_rate=MagicMock(return_value=None),
            enforce_no_workspace_secrets=MagicMock(return_value=None),
            make_container_name=MagicMock(return_value='container'),
            wrap_command=MagicMock(return_value=['docker', 'run']),
            record_spawn=MagicMock(side_effect=SandboxError('audit log unavailable')),
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            with self.assertRaisesRegex(RuntimeError, 'audit log'):
                session.start()

    def test_start_sends_initial_prompt_when_provided(self) -> None:
        # Line 425: start() with non-empty initial_prompt calls send_user_message.
        fake_proc = _FakeProc(stdout_lines=[
            json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 'live'}),
        ])
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-prompt', cwd='/tmp')
            session.start(initial_prompt='hello world')
        # send_user_message wrote to stdin (the prompt is encoded into an envelope).
        self.assertTrue(fake_proc.stdin.write.called)
        # The envelope payload contains the prompt text.
        written_data = b''.join(
            call.args[0] for call in fake_proc.stdin.write.call_args_list
        )
        self.assertIn(b'hello world', written_data)

    def test_start_raises_when_subprocess_popen_fails(self) -> None:
        # Lines 413-414: OSError/FileNotFoundError from Popen → wrapped.
        session = StreamingClaudeSession(task_id='PROJ-5', cwd='/tmp')
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            side_effect=FileNotFoundError('no such binary'),
        ):
            with self.assertRaisesRegex(RuntimeError, 'failed to launch claude CLI binary'):
                session.start()

    def test_stdout_reader_loop_skips_empty_and_non_event_lines(self) -> None:
        # Line 774, 777: a blank line + a non-JSON line should both be
        # skipped without crashing the reader.
        session = self._build_session()
        session.logger = MagicMock()
        stdout_stream = io.BytesIO(
            b'\n'   # blank line — line 774 ``if not text: continue``
            b'garbage\n'   # non-JSON — _parse_stdout_line returns None
            + json.dumps({'type': 'system'}).encode() + b'\n'
        )
        session._proc = SimpleNamespace(stdout=stdout_stream)
        session._stdout_reader_loop()
        # Only the valid event was published.
        self.assertEqual(len(session._recent_events), 1)
        self.assertEqual(session._recent_events[0].event_type, 'system')

    def test_terminate_no_op_when_proc_missing(self) -> None:
        # Line 535: terminate() early return when no proc.
        session = self._build_session()
        session._proc = None
        session.terminate()  # must not raise

    def test_escalate_to_kill_swallows_exception(self) -> None:
        # Lines 561-571: ``proc.kill()`` raises → swallowed.
        session = self._build_session()
        fake_proc = MagicMock()
        fake_proc.kill.side_effect = ProcessLookupError('already gone')
        # _wait_for_exit needs poll(), wait()
        fake_proc.poll.return_value = 0
        fake_proc.wait.return_value = 0
        session._escalate_to_kill(fake_proc)  # must not raise
        fake_proc.kill.assert_called_once()

    def test_wait_for_new_events_returns_immediately_when_events_present(self) -> None:
        # Lines 633-647: ``wait_for_new_events`` happy path.
        session = self._build_session()
        session._proc = SimpleNamespace(poll=lambda: None)
        # Pre-populate with one event past index 0.
        session._recent_events.append(SessionEvent(raw={'type': 'system'}))
        new_events, idx, alive = session.wait_for_new_events(
            start_index=0, timeout=0.1,
        )
        self.assertEqual(len(new_events), 1)
        self.assertEqual(idx, 1)
        self.assertTrue(alive)

    def test_wait_for_new_events_returns_empty_on_timeout(self) -> None:
        session = self._build_session()
        session._proc = SimpleNamespace(poll=lambda: None)  # alive
        # No events past index 0 → block, then timeout returns empty.
        new_events, idx, alive = session.wait_for_new_events(
            start_index=0, timeout=0.05,
        )
        self.assertEqual(new_events, [])
        self.assertEqual(idx, 0)

    def test_wait_for_new_events_returns_alive_false_when_session_dies(self) -> None:
        session = self._build_session()
        # No proc → not alive → wait_for short-circuits.
        new_events, idx, alive = session.wait_for_new_events(
            start_index=0, timeout=0.05,
        )
        self.assertFalse(alive)

    def test_scan_terminal_for_credentials_no_op_when_result_blank(self) -> None:
        # Line 819: ``if not result_text: return``.
        session = self._build_session()
        session.logger = MagicMock()
        terminal = SessionEvent(raw={
            'type': 'result', 'subtype': 'final',
            'is_error': False, 'result': '',
        })
        session._scan_terminal_for_credentials(terminal)
        session.logger.warning.assert_not_called()

    def test_maybe_fire_done_sentinel_non_list_content(self) -> None:
        # Line 888: ``content`` is not a list → return.
        session = self._build_session()
        callback = MagicMock()
        session._done_callback = callback
        session._maybe_fire_done_sentinel(SessionEvent(raw={
            'type': 'assistant',
            'message': {'content': 'plain string with sentinel'},
        }))
        callback.assert_not_called()

    def test_maybe_fire_done_sentinel_skips_non_text_block(self) -> None:
        # Line 892: tool_use blocks etc. are skipped.
        session = self._build_session()
        callback = MagicMock()
        session._done_callback = callback
        session._maybe_fire_done_sentinel(SessionEvent(raw={
            'type': 'assistant',
            'message': {'content': [
                {'type': 'tool_use', 'name': 'Edit'},
                {'type': 'text', 'text': '<KATO_TASK_DONE>'},
            ]},
        }))
        # Sentinel found in the text block (after non-text skip).
        callback.assert_called_once_with('PROJ-1')

    def test_log_event_for_operator_skips_unmatched_event_types(self) -> None:
        # Lines 966-967: neither permission nor result → no logging.
        session = self._build_session()
        session.logger = MagicMock()
        session._log_event_for_operator(SessionEvent(raw={'type': 'system'}))
        session.logger.info.assert_not_called()
        session.logger.warning.assert_not_called()

    def test_log_event_for_operator_logs_permission_request(self) -> None:
        session = self._build_session()
        session.logger = MagicMock()
        session._log_event_for_operator(SessionEvent(raw={
            'type': 'permission_request',
            'tool_name': 'Bash',
            'request_id': 'r1',
        }))
        session.logger.info.assert_called_once()

    def test_log_event_for_operator_logs_result_with_stderr(self) -> None:
        # Lines 984-989: is_error + stderr_tail → warning log fires too.
        session = self._build_session()
        session.logger = MagicMock()
        session._stderr_lines = ['some error output']
        session._log_event_for_operator(SessionEvent(raw={
            'type': 'result',
            'is_error': True,
            'result': 'something went wrong',
        }))
        session.logger.info.assert_called_once()
        session.logger.warning.assert_called_once()

    def test_log_event_for_operator_silences_stale_resume_error(self) -> None:
        # Line 1001: stale-resume error path → debug log + early return.
        session = self._build_session(resume_session_id='dead-uuid')
        session.logger = MagicMock()
        # Plant stderr line matching the stale-resume marker.
        session._stderr_lines = [
            'No conversation found with session ID: dead-uuid',
        ]
        session._log_event_for_operator(SessionEvent(raw={
            'type': 'result', 'is_error': True, 'result': 'failed',
        }))
        # debug log fired; info log did NOT (silenced).
        session.logger.debug.assert_called_once()
        session.logger.info.assert_not_called()

    def test_stderr_reader_loop_appends_lines(self) -> None:
        # Lines 1019-1026: real reader loop with a fake stderr stream.
        session = self._build_session()
        session.logger = MagicMock()
        # Build a fake stderr that yields three lines then EOF.
        stderr_stream = io.BytesIO(b'line one\nline two\n\nline three\n')
        session._proc = SimpleNamespace(stderr=stderr_stream)
        session._stderr_reader_loop()
        # Empty lines are skipped; we get 3 real lines.
        self.assertEqual(len(session._stderr_lines), 3)
        self.assertEqual(session._stderr_lines[0], 'line one')

    def test_stderr_reader_loop_trims_buffer_when_oversized(self) -> None:
        # Lines 1024-1026: stderr buffer caps at 500 lines.
        session = self._build_session()
        session.logger = MagicMock()
        # Generate 510 stderr lines.
        many = b''.join(f'line {i}\n'.encode() for i in range(510))
        session._proc = SimpleNamespace(stderr=io.BytesIO(many))
        session._stderr_reader_loop()
        self.assertEqual(len(session._stderr_lines), 500)

    def test_stdout_reader_loop_processes_event_and_publishes(self) -> None:
        # Lines 774, 777: terminal event is captured and published.
        session = self._build_session()
        session.logger = MagicMock()
        stdout_stream = io.BytesIO(
            json.dumps({'type': 'system', 'subtype': 'init',
                        'session_id': 'live-1'}).encode() + b'\n'
            + json.dumps({
                'type': 'result', 'is_error': False, 'result': 'done',
            }).encode() + b'\n'
        )
        session._proc = SimpleNamespace(stdout=stdout_stream)
        session._stdout_reader_loop()
        # Terminal event captured.
        self.assertIsNotNone(session.terminal_event)
        # Session id pinned from the init event.
        self.assertEqual(session.agent_session_id, 'live-1')


class StreamingClaudeSessionDockerModeTests(unittest.TestCase):
    """``KATO_CLAUDE_DOCKER`` plumbing for the streaming spawn path.

    Sandbox-wrap on streaming sessions now gates on the new
    ``docker_mode_on`` attribute, not on ``permission_mode ==
    bypassPermissions``. This separates *containment* (docker) from the
    *prompt layer* (bypass), so an operator can run docker=true with
    permission prompts on for the strongest combined posture.
    """

    def test_docker_mode_off_does_not_wrap_spawn_even_when_bypass_permissions(self) -> None:
        fake_proc = _FakeProc()
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ) as mock_popen, patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.manager.wrap_command',
        ) as mock_wrap:
            session = StreamingClaudeSession(
                task_id='PROJ-1',
                cwd='/tmp/repo',
                permission_mode='bypassPermissions',
                docker_mode_on=False,
            )
            session.start()

        mock_wrap.assert_not_called()
        spawn_argv = mock_popen.call_args.args[0]
        # Streaming session resolves the binary via shutil.which.
        self.assertNotEqual(spawn_argv[:2], ['docker', 'run'])

    def test_docker_mode_on_wraps_spawn_in_sandbox(self) -> None:
        fake_proc = _FakeProc()
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ) as mock_popen, patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.manager.ensure_image',
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.manager.check_spawn_rate',
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.manager.enforce_no_workspace_secrets',
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.manager.record_spawn',
        ) as mock_record, patch(
            'sandbox_core_lib.sandbox_core_lib.manager.wrap_command',
            return_value=['docker', 'run', '--rm', 'kato-sandbox', 'claude'],
        ) as mock_wrap, patch(
            'sandbox_core_lib.sandbox_core_lib.manager.make_container_name',
            return_value='kato-sandbox-PROJ-1-abcd1234',
        ):
            session = StreamingClaudeSession(
                task_id='PROJ-1',
                cwd='/tmp/repo',
                docker_mode_on=True,
            )
            session.start()

        mock_wrap.assert_called_once()
        wrap_kwargs = mock_wrap.call_args.kwargs
        self.assertEqual(wrap_kwargs['task_id'], 'PROJ-1')
        self.assertEqual(wrap_kwargs['workspace_path'], '/tmp/repo')
        # Audit log fires before the subprocess starts.
        mock_record.assert_called_once()
        spawn_argv = mock_popen.call_args.args[0]
        self.assertEqual(spawn_argv[:2], ['docker', 'run'])

    def test_docker_mode_default_is_off(self) -> None:
        session = StreamingClaudeSession(task_id='PROJ-1')
        self.assertFalse(session._docker_mode_on)

    def test_docker_mode_off_does_not_append_sandbox_addendum(self) -> None:
        from sandbox_core_lib.sandbox_core_lib.system_prompt import (
            RESUMED_SESSION_ADDENDUM,
            SANDBOX_SYSTEM_PROMPT_ADDENDUM,
            WORKSPACE_SCOPE_ADDENDUM,
        )

        session = StreamingClaudeSession(
            task_id='PROJ-1',
            docker_mode_on=False,
        )
        cmd = session._build_command()
        # Workspace + resumed-session addenda are always appended;
        # sandbox is only added in docker mode.
        self.assertIn('--append-system-prompt', cmd)
        idx = cmd.index('--append-system-prompt')
        self.assertEqual(
            cmd[idx + 1],
            f'{WORKSPACE_SCOPE_ADDENDUM}\n\n{RESUMED_SESSION_ADDENDUM}',
        )
        self.assertNotIn(SANDBOX_SYSTEM_PROMPT_ADDENDUM, cmd[idx + 1])

    def test_docker_mode_on_appends_sandbox_addendum(self) -> None:
        from sandbox_core_lib.sandbox_core_lib.system_prompt import (
            RESUMED_SESSION_ADDENDUM,
            SANDBOX_SYSTEM_PROMPT_ADDENDUM,
            WORKSPACE_SCOPE_ADDENDUM,
        )

        session = StreamingClaudeSession(
            task_id='PROJ-1',
            docker_mode_on=True,
        )
        cmd = session._build_command()
        self.assertIn('--append-system-prompt', cmd)
        idx = cmd.index('--append-system-prompt')
        self.assertEqual(
            cmd[idx + 1],
            (
                f'{WORKSPACE_SCOPE_ADDENDUM}\n\n{RESUMED_SESSION_ADDENDUM}\n\n'
                f'{SANDBOX_SYSTEM_PROMPT_ADDENDUM}'
            ),
        )


class StreamingClaudeSessionCredentialOutputScanTests(unittest.TestCase):
    """Output-side credential scan on the streaming terminal event.

    Closes residual #18 on the streaming spawn path. Mirrors the
    one-shot behavior in
    ``ClaudeCliClient._scan_response_for_credentials`` so both paths
    produce the same audit signal when the agent emits a credential
    pattern in its final response. Detective-only: the response has
    already crossed to Anthropic by the time we see it.
    """

    def test_warning_logged_when_terminal_event_contains_credential(self) -> None:
        fake_aws_key = 'AKIAEXAMPLEFAKE12345'
        terminal_line = json.dumps({
            'type': 'result',
            'subtype': 'success',
            'is_error': False,
            'result': f'Here is the value: {fake_aws_key}',
            'session_id': 'live-1',
        })
        fake_proc = _FakeProc(stdout_lines=[terminal_line])
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ), self.assertLogs('kato.workflow.StreamingClaudeSession', level='WARNING') as cm:
            session = StreamingClaudeSession(task_id='PROJ-CRED')
            session.start()
            # Consume events to drive the reader thread to the terminal.
            for _ in session.events_iter():
                pass

        joined = ' '.join(cm.output)
        self.assertIn('aws_access_key_id', joined)
        self.assertIn('CREDENTIAL PATTERN DETECTED', joined)
        # Full credential value must never be logged.
        self.assertNotIn(fake_aws_key, joined)
        self.assertIn('REDACTED', joined)

    def test_no_warning_when_terminal_event_is_clean(self) -> None:
        terminal_line = json.dumps({
            'type': 'result',
            'subtype': 'success',
            'is_error': False,
            'result': 'Done — edits written.',
            'session_id': 'live-2',
        })
        fake_proc = _FakeProc(stdout_lines=[terminal_line])
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            session = StreamingClaudeSession(task_id='PROJ-CLEAN')
            with self.assertNoLogs('kato.workflow.StreamingClaudeSession', level='WARNING'):
                session.start()
                for _ in session.events_iter():
                    pass

    def test_warning_logged_when_terminal_event_contains_phishing_pattern(self) -> None:
        """Streaming-side detective scan also fires for phishing (#16).

        Mirrors test_warning_logged_when_response_contains_phishing_pattern
        in the one-shot test file. Without this assertion, a regression
        that drops the phishing-detector call from the streaming path
        leaves residual #16 silently undefended on the streaming spawn.
        """
        # Use a code-fenced sudo block — the sudo_command regex anchors
        # to start-of-line / special chars (not bare mid-prose) to keep
        # false positives on words like "pseudo" out. This is the exact
        # phishing shape the addendum tells the agent to NOT generate.
        terminal_line = json.dumps({
            'type': 'result',
            'subtype': 'success',
            'is_error': False,
            'result': 'On your host:\n```bash\nsudo apt install build-essential\n```',
            'session_id': 'live-phish',
        })
        fake_proc = _FakeProc(stdout_lines=[terminal_line])
        with patch(
            'claude_core_lib.claude_core_lib.session.streaming.subprocess.Popen',
            return_value=fake_proc,
        ), patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ), self.assertLogs('kato.workflow.StreamingClaudeSession', level='WARNING') as cm:
            session = StreamingClaudeSession(task_id='PROJ-PHISH')
            session.start()
            for _ in session.events_iter():
                pass

        joined = ' '.join(cm.output)
        # Distinct PHISHING tag, separate from CREDENTIAL.
        self.assertIn('PHISHING PATTERN DETECTED', joined)
        # Pattern name names the shape so operator can review the
        # specific suggestion in the planning UI.
        self.assertIn('sudo_command', joined)
        self.assertIn('residual #16', joined)


if __name__ == '__main__':
    unittest.main()
