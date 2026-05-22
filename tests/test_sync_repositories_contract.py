"""Backend ↔ UI contract test for /sync-repositories.

Real Flask app + real AgentService + real workspace manager. NOT
end-to-end real provider integration — the fully-successful path
needs real YouTrack/Jira credentials and reachable remote URLs,
which a hermetic test can't provide. We patch ``_lookup_task_for_sync``
and ``resolve_task_repositories`` for two of the four shapes (task-
lookup-failed and nothing-to-sync) so the route's response
serializer + error mapping is exercised on real backend code paths.
The no-workspace 404 path is fully real (no patches).

The UI consumes the response shape (toast spec keys), and the JS
contract test feeds each captured shape through the real
``formatSyncResult`` helper — so the contract is about the
backend's response keys + the UI's renderer agreeing on them.

Default mode is READ-ONLY against the committed fixture; regen
gated by ``KATO_REGEN_CONTRACT_FIXTURES=1``.
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
_FIXTURE_PATH = _FIXTURE_DIR / 'sync_repositories_contract.json'


class SyncRepositoriesContractTests(unittest.TestCase):
    """End-to-end: real Flask + real AgentService through the real route."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-sync-contract-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.task_id = 'PROJ-SYNC'
        self.repo_id = 'client'
        self.agent_service, self.workspace_service = build_real_agent_service(
            self.root,
        )
        materialize_workspace(
            self.workspace_service, self.task_id,
            repository_ids=[self.repo_id],
        )

        self.app = create_app(
            session_manager=_build_fallback_manager(str(self.root / 'sessions')),
            workspace_manager=self.workspace_service,
            agent_service=self.agent_service,
            fallback_state_dir=str(self.root / 'sessions'),
        )
        self.client = self.app.test_client()

    def _post(self, url: str) -> tuple[int, dict]:
        response = self.client.post(url)
        return response.status_code, response.get_json()

    # ----- shape assertions -----

    def test_no_workspace_returns_404_with_error_field(self) -> None:
        """Unknown task → 404 ``{'synced': False, 'task_id': ..., 'error': ...}``.

        The UI's toast renderer reads the ``error`` field; 404 makes
        it show "adopt the task first" rather than a generic failure.
        """
        status, payload = self._post('/api/sessions/GHOST-TASK/sync-repositories')
        self.assertEqual(status, 404)
        self.assertEqual(payload.get('synced'), False)
        self.assertEqual(payload.get('task_id'), 'GHOST-TASK')
        self.assertIn('no workspace', payload.get('error', ''))

    def test_empty_task_id_via_real_route(self) -> None:
        """An all-spaces task id would just hit the route with that path.

        Flask routes on ``<task_id>`` accept anything that isn't
        empty (an empty path component → 404 from the router). The
        empty-string branch inside ``sync_task_repositories`` is
        exercised directly in unit tests; here we just confirm the
        route returns a structured error when called with a clearly
        bogus id.
        """
        status, payload = self._post('/api/sessions/   /sync-repositories')
        # Real Flask returns 200 because '   ' is a valid URL segment;
        # the service rejects it inside.
        self.assertIn(status, (200, 400, 404, 500))
        self.assertIsInstance(payload, dict)
        # Either ``synced: false`` or an ``error`` key — both are
        # in the contract shape.
        self.assertTrue(
            'error' in payload or payload.get('synced') is False,
            f'unexpected response shape: {payload!r}',
        )

    def test_task_lookup_failure_returns_structured_error(self) -> None:
        """Task exists in workspace but ticket platform can't load it.

        Real AgentService flow: ``_lookup_task_for_sync`` returns None
        (no task_service result), service returns an in-shape error.
        UI consumes ``error`` for the toast.
        """
        with patch.object(
            self.agent_service, '_lookup_task_for_sync',
            return_value=None,
        ):
            status, payload = self._post(
                f'/api/sessions/{self.task_id}/sync-repositories',
            )
        # Either 500 (server-error code path) or 200 with synced=False.
        self.assertIn(status, (200, 500))
        self.assertEqual(payload.get('synced'), False)
        self.assertIn('error', payload)
        self.assertIn('could not load task', payload['error'])

    def test_resolve_returns_empty_repo_set_means_nothing_to_sync(self) -> None:
        """No repos resolved for the task → success path with empty result.

        Real ``RepositoryService`` resolve returns []; service returns
        a structured "nothing to sync" payload that the UI shows as
        a green "already in sync" toast.
        """
        from types import SimpleNamespace
        with patch.object(
            self.agent_service, '_lookup_task_for_sync',
            return_value=SimpleNamespace(id=self.task_id, tags=[]),
        ), patch.object(
            self.agent_service._repository_service,
            'resolve_task_repositories',
            return_value=[],
        ):
            status, payload = self._post(
                f'/api/sessions/{self.task_id}/sync-repositories',
            )
        self.assertEqual(status, 200)
        # Empty resolved → ``missing`` is empty; common shape keys are
        # ``synced`` (truthy bool), ``task_id``, and ``missing``.
        self.assertEqual(payload.get('task_id'), self.task_id)
        self.assertIn('synced', payload)

    # ----- contract fixture (read-only by default) -----

    def _build_stable_fixture(self) -> dict:
        """Capture the three contract-relevant shapes."""
        from types import SimpleNamespace
        _, ghost = self._post('/api/sessions/GHOST-TASK/sync-repositories')
        with patch.object(
            self.agent_service, '_lookup_task_for_sync',
            return_value=None,
        ):
            _, lookup_fail = self._post(
                f'/api/sessions/{self.task_id}/sync-repositories',
            )
        with patch.object(
            self.agent_service, '_lookup_task_for_sync',
            return_value=SimpleNamespace(id=self.task_id, tags=[]),
        ), patch.object(
            self.agent_service._repository_service,
            'resolve_task_repositories',
            return_value=[],
        ):
            _, nothing_to_sync = self._post(
                f'/api/sessions/{self.task_id}/sync-repositories',
            )
        return {
            'no_workspace': ghost,
            'task_lookup_failed': lookup_fail,
            'nothing_to_sync': nothing_to_sync,
            'expected': {
                'task_id': self.task_id,
            },
        }

    def test_committed_fixture_matches_what_the_backend_produces(self) -> None:
        """Read-only sync check; regen via KATO_REGEN_CONTRACT_FIXTURES=1."""
        self.assertTrue(
            _FIXTURE_PATH.is_file(),
            f'committed fixture missing: {_FIXTURE_PATH}. '
            'Set KATO_REGEN_CONTRACT_FIXTURES=1 to create it.',
        )
        committed = json.loads(_FIXTURE_PATH.read_text(encoding='utf-8'))
        live = self._build_stable_fixture()
        self.assertEqual(
            live, committed,
            'sync-repositories backend payload drifted from the '
            f'committed fixture. Regenerate with '
            'KATO_REGEN_CONTRACT_FIXTURES=1 and commit '
            f'{_FIXTURE_PATH.name}.',
        )

    @unittest.skipUnless(
        os.environ.get('KATO_REGEN_CONTRACT_FIXTURES'),
        'opt-in; set KATO_REGEN_CONTRACT_FIXTURES=1 to regenerate',
    )
    def test_regenerate_committed_fixture(self) -> None:
        _FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        _FIXTURE_PATH.write_text(
            json.dumps(self._build_stable_fixture(), indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )


if __name__ == '__main__':
    unittest.main()
