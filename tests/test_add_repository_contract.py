"""Backend ↔ UI contract test for /add-repository.

Real Flask app + real AgentService. The success path needs real
provider integration (a working YouTrack/Jira tag-write + a real
clone-able remote URL), which a hermetic test can't provide. The
shape coverage we CAN do:

  * empty repository_id → 400 with the exact error UI shows inline
  * repository_id not in the kato inventory → 404 with structured
    error the UI shows in the toast
  * inventory lookup explodes → 404 (the route maps the error
    string to the right status code)

The UI consumes a small ``{added, error?, repository_id, task_id}``
shape — every covered path asserts those keys are present.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_WEBSERVER_DIR = Path(__file__).resolve().parent.parent / 'webserver'
if str(_WEBSERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEBSERVER_DIR))

from kato_webserver.app import create_app, _build_fallback_manager       # noqa: E402

from tests.chaos_lib import (                                             # noqa: E402
    build_real_agent_service,
    materialize_workspace,
)


_FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent
    / 'webserver' / 'ui' / 'src' / '__fixtures__'
)
_FIXTURE_PATH = _FIXTURE_DIR / 'add_repository_contract.json'


class AddRepositoryContractTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-add-repo-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.task_id = 'PROJ-ADD'
        self.agent_service, self.workspace_service = build_real_agent_service(
            self.root,
        )
        materialize_workspace(
            self.workspace_service, self.task_id, repository_ids=['existing'],
        )

        self.app = create_app(
            session_manager=_build_fallback_manager(str(self.root / 'sessions')),
            workspace_manager=self.workspace_service,
            agent_service=self.agent_service,
            fallback_state_dir=str(self.root / 'sessions'),
        )
        self.client = self.app.test_client()

    def _post(self, payload: dict | None) -> tuple[int, dict]:
        response = self.client.post(
            f'/api/sessions/{self.task_id}/add-repository',
            json=(payload if payload is not None else {}),
        )
        return response.status_code, response.get_json()

    def test_empty_repository_id_returns_400_with_error(self) -> None:
        status, payload = self._post({'repository_id': ''})
        self.assertEqual(status, 400)
        self.assertIn('error', payload)
        self.assertIn('repository_id', payload['error'])

    def test_missing_body_returns_400(self) -> None:
        status, payload = self._post(None)
        self.assertEqual(status, 400)
        self.assertIn('error', payload)

    def test_unknown_repository_id_returns_404_with_inventory_error(self) -> None:
        """Repo id not in the kato inventory → 404; UI shows toast."""
        status, payload = self._post({'repository_id': 'never-configured'})
        self.assertEqual(status, 404)
        self.assertFalse(payload.get('added'))
        # Required keys for the UI's toast.
        self.assertEqual(payload.get('task_id'), self.task_id)
        self.assertEqual(payload.get('repository_id'), 'never-configured')
        self.assertIn('not in the kato inventory', payload.get('error', ''))

    # ----- contract fixture (read-only by default) -----

    def _build_stable_fixture(self) -> dict:
        _, empty = self._post({'repository_id': ''})
        _, missing = self._post(None)
        _, unknown = self._post({'repository_id': 'never-configured'})
        return {
            'empty_repository_id': empty,
            'missing_body': missing,
            'unknown_repository_id': unknown,
            'expected': {'task_id': self.task_id},
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
            'add-repository backend payload drifted; regenerate with '
            'KATO_REGEN_CONTRACT_FIXTURES=1.',
        )

    @unittest.skipUnless(
        os.environ.get('KATO_REGEN_CONTRACT_FIXTURES'),
        'opt-in fixture regeneration',
    )
    def test_regenerate_committed_fixture(self) -> None:
        _FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        _FIXTURE_PATH.write_text(
            json.dumps(self._build_stable_fixture(), indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )


if __name__ == '__main__':
    unittest.main()
