"""Backend ↔ UI contract test for /comments (NO MOCKS).

Boots the REAL Flask app, points it at a REAL AgentService whose
``_comment_store_for`` returns a REAL ``LocalCommentStore`` on a
real tempdir. Hits ``GET /api/sessions/<task>/comments`` and
``POST /api/sessions/<task>/comments`` via Flask's test client and
captures both response shapes for the UI contract test.

The captured payloads are written to
``webserver/ui/src/__fixtures__/comments_contract.json`` and consumed
by ``webserver/ui/src/comments.contract.test.jsx``. The fixture is
the single artifact both sides agree on — if the backend response
shape drifts, the Python test fails first; if the UI grows a new
expectation, the JS test fails against the same fixture.

Default mode is READ-ONLY (asserts the committed fixture matches
what the live backend produces). Regeneration is opt-in via
``KATO_REGEN_CONTRACT_FIXTURES=1``.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_webserver.app import create_app, _build_fallback_manager

from tests.chaos_lib import (
    build_real_agent_service,
    materialize_workspace,
)


_FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent
    / 'webserver' / 'ui' / 'src' / '__fixtures__'
)
_FIXTURE_PATH = _FIXTURE_DIR / 'comments_contract.json'


class CommentsContractTests(unittest.TestCase):
    """End-to-end: real Flask + real AgentService + real LocalCommentStore."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-comments-contract-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.task_id = 'PROJ-COMMENTS'
        self.repo_id = 'client'
        self.agent_service, self.workspace_service = build_real_agent_service(
            self.root,
        )
        materialize_workspace(
            self.workspace_service, self.task_id,
            repository_ids=[self.repo_id],
        )
        # Stub only the agent spawn — the route would otherwise try
        # to launch Claude. Everything else (store, workspace,
        # serialization, validation) is real.
        self._run_patch = patch.object(
            self.agent_service, '_run_comment_agent', return_value=True,
        )
        self._run_patch.start()
        self.addCleanup(self._run_patch.stop)

        self.app = create_app(
            session_manager=_build_fallback_manager(str(self.root / 'sessions')),
            workspace_manager=self.workspace_service,
            agent_service=self.agent_service,
            fallback_state_dir=str(self.root / 'sessions'),
        )
        self.client = self.app.test_client()

    def _get_json(self, url: str) -> dict:
        response = self.client.get(url)
        self.assertEqual(
            response.status_code, 200,
            f'{url} returned {response.status_code}: '
            f'{response.get_data(as_text=True)[:200]}',
        )
        return response.get_json()

    def _post_json(self, url: str, payload: dict) -> tuple[int, dict]:
        response = self.client.post(url, json=payload)
        return response.status_code, response.get_json()

    # ----- shape assertions (default, read-only) -----

    def test_list_comments_empty_payload_shape(self) -> None:
        """Empty workspace → ``{"comments": []}`` — required by the UI."""
        payload = self._get_json(f'/api/sessions/{self.task_id}/comments')
        self.assertIn('comments', payload)
        self.assertEqual(payload['comments'], [])

    def test_create_comment_response_shape(self) -> None:
        """``POST /comments`` returns ``{ok, comment, triggered_immediately}``.

        The UI's optimistic-update path consumes ``comment.id`` to
        link the on-screen pending row to the persisted record.
        """
        status, payload = self._post_json(
            f'/api/sessions/{self.task_id}/comments',
            {
                'repo': self.repo_id,
                'file_path': 'src/app.py',
                'line': 12,
                'body': 'fix it pls',
                'author': 'operator',
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload.get('ok'))
        self.assertIn('comment', payload)
        comment = payload['comment']
        for key in ('id', 'repo_id', 'file_path', 'line', 'body',
                    'author', 'source', 'status', 'kato_status'):
            self.assertIn(key, comment, f'missing {key} in created comment')
        self.assertEqual(comment['body'], 'fix it pls')
        self.assertEqual(comment['repo_id'], self.repo_id)
        self.assertEqual(comment['file_path'], 'src/app.py')
        self.assertEqual(comment['line'], 12)

    def test_create_then_list_reflects_real_on_disk_state(self) -> None:
        """End-to-end: POST one, POST another, GET sees both — via real disk."""
        for body in ('fix it pls', 'whats wrong with you'):
            status, _ = self._post_json(
                f'/api/sessions/{self.task_id}/comments',
                {
                    'repo': self.repo_id, 'file_path': 'src/app.py',
                    'line': 1, 'body': body,
                },
            )
            self.assertEqual(status, 200)
        listed = self._get_json(
            f'/api/sessions/{self.task_id}/comments',
        )['comments']
        bodies = sorted(c['body'] for c in listed)
        self.assertEqual(bodies, ['fix it pls', 'whats wrong with you'])

    def test_create_rejects_blank_body(self) -> None:
        """Validation surfaces as 400 (UI shows the error inline)."""
        status, payload = self._post_json(
            f'/api/sessions/{self.task_id}/comments',
            {'repo': self.repo_id, 'file_path': 'src/app.py', 'body': '   '},
        )
        self.assertEqual(status, 400)
        self.assertFalse(payload.get('ok'))
        self.assertIn('non-empty', payload.get('error', ''))

    def test_create_for_unknown_task_returns_404(self) -> None:
        """No workspace → 404 (UI shows "adopt this task first")."""
        status, payload = self._post_json(
            '/api/sessions/GHOST-TASK/comments',
            {'repo': self.repo_id, 'file_path': 'x.py', 'body': 'hi'},
        )
        self.assertEqual(status, 404)
        self.assertFalse(payload.get('ok'))

    # ----- contract fixture (read-only by default) -----

    def _build_stable_fixture(self) -> dict:
        """Hit both shapes (empty list + create), normalise volatile bits."""
        empty_list = self._get_json(
            f'/api/sessions/{self.task_id}/comments',
        )
        status, created = self._post_json(
            f'/api/sessions/{self.task_id}/comments',
            {
                'repo': self.repo_id,
                'file_path': 'src/app.py',
                'line': 12,
                'body': 'fix it pls',
                'author': 'operator',
            },
        )
        self.assertEqual(status, 200)
        listed_after = self._get_json(
            f'/api/sessions/{self.task_id}/comments',
        )
        # Stabilize the auto-generated id + timestamp so the
        # committed fixture is byte-stable across runs.
        for record in listed_after['comments']:
            record['id'] = '__FIXTURE_ID__'
            record['created_at_epoch'] = 0.0
        created['comment']['id'] = '__FIXTURE_ID__'
        created['comment']['created_at_epoch'] = 0.0
        return {
            'list_empty': empty_list,
            'list_after_create': listed_after,
            'create': created,
            'expected': {
                'task_id': self.task_id,
                'repo_id': self.repo_id,
                'body': 'fix it pls',
                'file_path': 'src/app.py',
                'line': 12,
            },
        }

    def test_committed_fixture_matches_what_the_backend_produces(self) -> None:
        """The committed comments fixture is in sync with the real backend.

        Read-only on the default path. Regenerate by setting
        ``KATO_REGEN_CONTRACT_FIXTURES=1``.
        """
        self.assertTrue(
            _FIXTURE_PATH.is_file(),
            f'committed fixture missing: {_FIXTURE_PATH}. '
            'Set KATO_REGEN_CONTRACT_FIXTURES=1 to create it.',
        )
        committed = json.loads(_FIXTURE_PATH.read_text(encoding='utf-8'))
        live = self._build_stable_fixture()
        self.assertEqual(
            live, committed,
            'comments backend payload no longer matches the committed '
            'UI contract fixture. Re-run this file with '
            'KATO_REGEN_CONTRACT_FIXTURES=1 and commit the regenerated '
            f'{_FIXTURE_PATH.name}.',
        )

    @unittest.skipUnless(
        os.environ.get('KATO_REGEN_CONTRACT_FIXTURES'),
        'opt-in fixture regeneration; set KATO_REGEN_CONTRACT_FIXTURES=1 '
        'to write a fresh fixture from the real backend',
    )
    def test_regenerate_committed_fixture(self) -> None:
        _FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        _FIXTURE_PATH.write_text(
            json.dumps(self._build_stable_fixture(), indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
        roundtrip = json.loads(_FIXTURE_PATH.read_text(encoding='utf-8'))
        self.assertEqual(roundtrip['expected']['repo_id'], self.repo_id)


if __name__ == '__main__':
    unittest.main()
