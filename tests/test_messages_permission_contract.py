"""Backend ↔ UI contract test for /messages and /permission.

Real Flask app. Concrete recording session manager (NOT MagicMock —
a real Python class implementing the same interface the production
``ClaudeSessionManager`` exposes, with calls captured in instance
state so assertions don't need patches).

The two routes both flow through the session manager:

  POST /messages    → manager.get_session(task_id).send_user_message(...)
  POST /permission  → manager.get_session(task_id).send_permission_response(...)

We assert the routes' response shapes and the session manager's
``send_*`` methods receive the right arguments — both via real code
paths, no patches of either side.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from kato_webserver.app import create_app

from tests.chaos_lib import build_real_workspace_service


_FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent
    / 'webserver' / 'ui' / 'src' / '__fixtures__'
)
_FIXTURE_PATH = _FIXTURE_DIR / 'messages_permission_contract.json'


class _RecordingSession(object):
    """Concrete session — captures every call. No MagicMock."""

    def __init__(self, *, task_id: str, alive: bool = True) -> None:
        self.task_id = task_id
        self.is_alive = alive
        self.messages_sent: list[dict] = []
        self.permissions_sent: list[dict] = []

    def send_user_message(self, text: str, images: list | None = None) -> None:
        self.messages_sent.append({
            'text': text,
            'images': list(images or []),
        })

    def send_permission_response(self, *, request_id: str, allow: bool,
                                  rationale: str = '') -> None:
        self.permissions_sent.append({
            'request_id': request_id,
            'allow': allow,
            'rationale': rationale,
        })


class _RecordingSessionManager(object):
    """Concrete session manager — same interface as ClaudeSessionManager."""

    def __init__(self) -> None:
        self._sessions: dict[str, _RecordingSession] = {}
        self._records: dict[str, object] = {}

    def add_session(self, task_id: str, alive: bool = True) -> _RecordingSession:
        s = _RecordingSession(task_id=task_id, alive=alive)
        self._sessions[task_id] = s
        # The route helpers also check get_record() — a non-None return
        # indicates the task has historical state. Use a simple
        # namespace so this is real shape, not Mock.
        from types import SimpleNamespace
        self._records[task_id] = SimpleNamespace(task_id=task_id, cwd='')
        return s

    def get_session(self, task_id: str):
        return self._sessions.get(task_id)

    def get_record(self, task_id: str):
        return self._records.get(task_id)

    def list_records(self):
        return list(self._records.values())

    def terminate_session(self, task_id: str) -> None:
        s = self._sessions.pop(task_id, None)
        if s is not None:
            s.is_alive = False
        self._records.pop(task_id, None)


class MessagesAndPermissionContractTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-msg-contract-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.task_id = 'PROJ-MSG'
        self.manager = _RecordingSessionManager()
        self.session = self.manager.add_session(self.task_id, alive=True)

        self.workspace_service = build_real_workspace_service(self.root)

        self.app = create_app(
            session_manager=self.manager,
            workspace_manager=self.workspace_service,
            fallback_state_dir=str(self.root / 'sessions'),
        )
        self.client = self.app.test_client()

    def _post_json(self, url: str, payload: dict) -> tuple[int, dict]:
        response = self.client.post(url, json=payload)
        return response.status_code, response.get_json()

    # ----- /messages -----

    def test_messages_delivers_text_to_live_session(self) -> None:
        status, payload = self._post_json(
            f'/api/sessions/{self.task_id}/messages',
            {'text': 'fix it pls'},
        )
        self.assertEqual(status, 200)
        # Response shape the UI reads.
        for key in ('status', 'text', 'image_count'):
            self.assertIn(key, payload)
        self.assertEqual(payload['status'], 'delivered')
        self.assertEqual(payload['text'], 'fix it pls')
        self.assertEqual(payload['image_count'], 0)
        # And the real session actually received the message.
        self.assertEqual(len(self.session.messages_sent), 1)
        self.assertEqual(self.session.messages_sent[0]['text'], 'fix it pls')

    def test_messages_forwards_images_to_session(self) -> None:
        images = [{'media_type': 'image/png', 'data': 'base64-blob'}]
        status, payload = self._post_json(
            f'/api/sessions/{self.task_id}/messages',
            {'text': 'see screenshot', 'images': images},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload['image_count'], 1)
        self.assertEqual(self.session.messages_sent[0]['images'], images)

    def test_messages_rejects_empty_payload(self) -> None:
        status, payload = self._post_json(
            f'/api/sessions/{self.task_id}/messages',
            {},
        )
        self.assertEqual(status, 400)
        self.assertIn('error', payload)
        self.assertIn('text or images', payload['error'])
        # Session was NOT called.
        self.assertEqual(self.session.messages_sent, [])

    def test_messages_to_dead_session_falls_through_to_respawn_path(self) -> None:
        # Mark session dead — route should attempt the respawn path.
        # Without a planning_session_runner wired, _spawn_or_reject_chat_session
        # returns 409 (no runner). That's the contract shape the UI handles.
        self.session.is_alive = False
        status, payload = self._post_json(
            f'/api/sessions/{self.task_id}/messages',
            {'text': 'hi'},
        )
        self.assertIn(status, (409, 500, 503))
        self.assertIsInstance(payload, dict)

    # ----- /permission -----

    def test_permission_forwards_allow_decision_to_session(self) -> None:
        status, payload = self._post_json(
            f'/api/sessions/{self.task_id}/permission',
            {
                'request_id': 'req-1',
                'allow': True,
                'rationale': 'looks safe',
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(self.session.permissions_sent), 1)
        decision = self.session.permissions_sent[0]
        self.assertEqual(decision['request_id'], 'req-1')
        self.assertTrue(decision['allow'])
        self.assertEqual(decision['rationale'], 'looks safe')

    def test_permission_forwards_deny_decision_unchanged(self) -> None:
        status, payload = self._post_json(
            f'/api/sessions/{self.task_id}/permission',
            {
                'request_id': 'req-2',
                'allow': False,
                'rationale': 'no thanks',
            },
        )
        self.assertEqual(status, 200)
        decision = self.session.permissions_sent[0]
        self.assertFalse(decision['allow'])
        # Pre-tool-use hook short-circuits BEFORE the hook check when
        # the operator denies — so the rationale survives unchanged.
        self.assertEqual(decision['rationale'], 'no thanks')

    def test_permission_rejects_missing_request_id(self) -> None:
        status, payload = self._post_json(
            f'/api/sessions/{self.task_id}/permission',
            {'allow': True},
        )
        self.assertEqual(status, 400)
        self.assertIn('request_id', payload.get('error', ''))
        self.assertEqual(self.session.permissions_sent, [])

    def test_permission_returns_409_when_session_not_running(self) -> None:
        # Simulate session having terminated since the operator last
        # saw the UI — _resolve_writable_session returns 409.
        self.manager.terminate_session(self.task_id)
        status, payload = self._post_json(
            f'/api/sessions/{self.task_id}/permission',
            {'request_id': 'req-3', 'allow': True},
        )
        self.assertEqual(status, 409)
        self.assertIn('error', payload)

    # ----- contract fixture (read-only by default) -----

    def _build_stable_fixture(self) -> dict:
        """Capture both routes' response shapes for the UI side."""
        _, msg_delivered = self._post_json(
            f'/api/sessions/{self.task_id}/messages', {'text': 'fix it pls'},
        )
        _, msg_with_images = self._post_json(
            f'/api/sessions/{self.task_id}/messages',
            {'text': 'shot', 'images': [
                {'media_type': 'image/png', 'data': 'b'},
            ]},
        )
        _, msg_rejected = self._post_json(
            f'/api/sessions/{self.task_id}/messages', {},
        )
        _, perm_allow = self._post_json(
            f'/api/sessions/{self.task_id}/permission',
            {'request_id': 'req-1', 'allow': True, 'rationale': 'ok'},
        )
        _, perm_missing_id = self._post_json(
            f'/api/sessions/{self.task_id}/permission',
            {'allow': True},
        )
        # 409 path — terminate then attempt.
        self.manager.terminate_session(self.task_id)
        _, perm_409 = self._post_json(
            f'/api/sessions/{self.task_id}/permission',
            {'request_id': 'req-2', 'allow': True},
        )
        # Restore for any other test in the suite (setUp re-seeds, but
        # also keep the manager clean between fixture builds).
        self.session = self.manager.add_session(self.task_id, alive=True)
        return {
            'messages_delivered': msg_delivered,
            'messages_with_images': msg_with_images,
            'messages_rejected': msg_rejected,
            'permission_allow': perm_allow,
            'permission_missing_id': perm_missing_id,
            'permission_session_gone': perm_409,
            'expected': {
                'task_id': self.task_id,
            },
        }

    def test_committed_fixture_matches_what_the_backend_produces(self) -> None:
        self.assertTrue(
            _FIXTURE_PATH.is_file(),
            f'committed fixture missing: {_FIXTURE_PATH}. '
            'Set KATO_REGEN_CONTRACT_FIXTURES=1 to create it.',
        )
        committed = json.loads(_FIXTURE_PATH.read_text(encoding='utf-8'))
        live = self._build_stable_fixture()
        self.assertEqual(
            live, committed,
            'messages/permission backend payload drifted from the '
            'committed fixture; regenerate with '
            'KATO_REGEN_CONTRACT_FIXTURES=1.',
        )

    @unittest.skipUnless(
        os.environ.get('KATO_REGEN_CONTRACT_FIXTURES'),
        'opt-in fixture regeneration; set KATO_REGEN_CONTRACT_FIXTURES=1',
    )
    def test_regenerate_committed_fixture(self) -> None:
        _FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        _FIXTURE_PATH.write_text(
            json.dumps(self._build_stable_fixture(), indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )


if __name__ == '__main__':
    unittest.main()
