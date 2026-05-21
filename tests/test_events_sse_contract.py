"""Backend ↔ UI contract test for SSE /api/sessions/<task>/events.

Real Flask app + concrete session manager. Hits the streaming
endpoint, parses the first SSE frame(s) using only the W3C SSE
format (``event:`` + ``data:`` lines), and asserts the frame
shape matches what ``webserver/ui/src/hooks/useSessionStream.js``
parses on the client side.

We focus on the lifecycle-outcome frames the UI keys on:

  * ``session_missing`` — no record AND no workspace
  * ``session_idle``    — workspace exists, no live subprocess

Live-streaming events from a real subprocess (the ``message``
frames the agent emits) require booting a real Claude session and
are out of scope; the lifecycle frames are what the UI uses to
decide what to render in the chat tab.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_WEBSERVER_DIR = Path(__file__).resolve().parent.parent / 'webserver'
if str(_WEBSERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBSERVER_DIR))

from kato_webserver.app import create_app                                # noqa: E402

from tests.chaos_lib import (                                             # noqa: E402
    build_real_workspace_service,
    materialize_workspace,
)


class _EmptySessionManager(object):
    """Concrete manager with no live sessions (the ``session_missing``
    or ``session_idle`` path)."""

    def get_session(self, task_id):
        return None

    def get_record(self, task_id):
        return None

    def list_records(self):
        return []


def _parse_sse_frames(raw: bytes) -> list[dict]:
    """Parse the raw SSE byte stream into a list of {event, data} frames."""
    frames: list[dict] = []
    text = raw.decode('utf-8', 'replace')
    for block in text.split('\n\n'):
        if not block.strip():
            continue
        event_type = None
        data_lines: list[str] = []
        for line in block.split('\n'):
            if line.startswith('event:'):
                event_type = line[len('event:'):].strip()
            elif line.startswith('data:'):
                data_lines.append(line[len('data:'):].strip())
        if event_type is None:
            continue
        try:
            payload = json.loads(''.join(data_lines))
        except json.JSONDecodeError:
            payload = None
        frames.append({'event': event_type, 'data': payload})
    return frames


class EventsSSEContractTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-sse-contract-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.manager = _EmptySessionManager()
        self.workspace_service = build_real_workspace_service(self.root)
        self.app = create_app(
            session_manager=self.manager,
            workspace_manager=self.workspace_service,
            fallback_state_dir=str(self.root / 'sessions'),
        )
        self.client = self.app.test_client()

    def _stream(self, task_id: str, *, byte_limit: int = 4096) -> bytes:
        """Read the SSE stream until we have at least one full frame or hit the limit."""
        with self.client.get(
            f'/api/sessions/{task_id}/events', buffered=False,
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn(
                'text/event-stream',
                response.headers.get('Content-Type', ''),
                'SSE response missing event-stream content type',
            )
            chunks: list[bytes] = []
            total = 0
            for chunk in response.response:
                chunks.append(chunk)
                total += len(chunk)
                # Stop once we've seen at least one complete frame
                # (two consecutive newlines) or hit the byte cap.
                if b'\n\n' in b''.join(chunks) or total >= byte_limit:
                    break
            return b''.join(chunks)

    # ----- session_missing -----

    def test_session_missing_frame_shape(self) -> None:
        """No workspace AND no record → first frame is ``session_missing``."""
        raw = self._stream('GHOST-TASK')
        frames = _parse_sse_frames(raw)
        self.assertGreaterEqual(len(frames), 1, f'no SSE frames parsed: {raw!r}')
        first = frames[0]
        self.assertEqual(first['event'], 'session_missing')
        self.assertIsInstance(first['data'], dict)
        # Every frame carries ``type`` matching the event name —
        # ``_sse_message`` injects it server-side.
        self.assertEqual(first['data'].get('type'), 'session_missing')

    # ----- session_idle -----

    def test_session_idle_frame_shape_when_workspace_exists_but_no_session(self) -> None:
        """Workspace exists, no live subprocess → ``session_idle`` frame."""
        materialize_workspace(self.workspace_service, 'IDLE-TASK')
        raw = self._stream('IDLE-TASK')
        frames = _parse_sse_frames(raw)
        # The idle path may emit preflight / history replay events
        # first, then session_idle. Find the idle frame in the set.
        idle_frames = [f for f in frames if f['event'] == 'session_idle']
        self.assertGreaterEqual(
            len(idle_frames), 1,
            f'session_idle not found in frames: {[f["event"] for f in frames]}',
        )
        idle = idle_frames[0]
        self.assertIsInstance(idle['data'], dict)
        self.assertEqual(idle['data'].get('type'), 'session_idle')

    # ----- SSE frame format -----

    def test_response_is_valid_sse_format(self) -> None:
        """Frames follow the W3C SSE format the EventSource client parses."""
        raw = self._stream('FORMAT-TASK')
        text = raw.decode('utf-8')
        # Every frame starts with ``event: <type>`` and has a ``data: ``
        # JSON payload, terminated by a blank line.
        self.assertRegex(text, r'event:\s+\w+\n')
        self.assertRegex(text, r'data:\s+\{')

    def test_payload_is_valid_json(self) -> None:
        """The ``data:`` line on every frame is JSON-parseable."""
        raw = self._stream('JSON-TASK')
        frames = _parse_sse_frames(raw)
        for f in frames:
            self.assertIsNotNone(
                f['data'],
                f'frame {f["event"]} had non-JSON data',
            )


if __name__ == '__main__':
    unittest.main()
