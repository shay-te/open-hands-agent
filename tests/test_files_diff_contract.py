"""Backend ↔ UI contract test for /files, /diff, /commits, /file (NO MOCKS).

Boots the REAL Flask app, points it at a REAL ``WorkspaceService``
backed by a real on-disk tempdir holding REAL local git repos with
real commits. Hits the actual routes via Flask's test client
(in-process WSGI dispatch, no TCP — but the route handler, the
workspace manager, the git subprocess calls, and the JSON
serialization are all real).

The captured payload is written to
``webserver/ui/src/__fixtures__/files_diff_contract.json``. The UI
contract test (``webserver/ui/src/FilesTab.contract.test.jsx``)
imports that same file and renders FilesTab against it — so the
fixture is the single artifact both sides agree on.

If the backend shape drifts (a field renamed, a list dropped, a
new mandatory key added), this test fails first: the asserted
shape no longer matches. If the UI grows a new expectation, the
JS contract test fails against the same fixture. Either way, both
sides have to stay in sync.

Nothing is mocked. The Flask app gets a real fallback session
manager, a real WorkspaceService, real git on the path.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from kato_webserver.app import create_app, _build_fallback_manager

from tests.chaos_lib import build_real_workspace_service


_FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent
    / 'webserver' / 'ui' / 'src' / '__fixtures__'
)
_FIXTURE_PATH = _FIXTURE_DIR / 'files_diff_contract.json'


def _git(cwd: Path, *args: str) -> None:
    """Run a git command with a hermetic identity (so the test doesn't
    depend on the operator's global git config)."""
    env = {
        **os.environ,
        'GIT_AUTHOR_NAME': 'contract-test',
        'GIT_AUTHOR_EMAIL': 'contract@test.local',
        'GIT_COMMITTER_NAME': 'contract-test',
        'GIT_COMMITTER_EMAIL': 'contract@test.local',
    }
    subprocess.check_call(
        ['git', *args], cwd=str(cwd), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _build_local_repo_with_task_branch(
    repo_root: Path, *, base_branch: str, task_branch: str,
) -> None:
    """Set up: a bare 'origin' + a working clone with a task branch.

    Layout:
      ``<repo_root>/origin.git``  bare repo (the remote)
      ``<repo_root>/work``         working clone — what kato uses

    On the working clone we commit a baseline file on ``base_branch``,
    push it to origin, then branch off, commit a real change, and
    push that branch too — so the diff endpoint can resolve
    ``origin/<base_branch>`` and produce a real unified diff.
    """
    origin = repo_root / 'origin.git'
    origin.mkdir()
    _git(origin, 'init', '--bare', '--initial-branch', base_branch)

    work = repo_root / 'work'
    work.mkdir()
    _git(work, 'init', '--initial-branch', base_branch)
    _git(work, 'remote', 'add', 'origin', str(origin))
    # Baseline commit on the base branch.
    (work / 'README.md').write_text(
        'Project README — initial content.\n', encoding='utf-8',
    )
    (work / 'src').mkdir()
    (work / 'src' / 'app.py').write_text(
        "def main():\n    print('hello')\n", encoding='utf-8',
    )
    _git(work, 'add', 'README.md', 'src/app.py')
    _git(work, 'commit', '-m', 'initial commit')
    _git(work, 'push', '-u', 'origin', base_branch)

    # Task branch: edit a tracked file + add a new file. Both diff
    # AND files-tree endpoints will surface these.
    _git(work, 'checkout', '-b', task_branch)
    (work / 'src' / 'app.py').write_text(
        "def main():\n    print('hello, fixed')\n    return 0\n",
        encoding='utf-8',
    )
    (work / 'src' / 'helper.py').write_text(
        "def helper():\n    return 42\n", encoding='utf-8',
    )
    _git(work, 'add', 'src/app.py', 'src/helper.py')
    _git(work, 'commit', '-m', 'task: real edit + new helper')
    _git(work, 'push', '-u', 'origin', task_branch)


class FilesDiffContractTests(unittest.TestCase):
    """End-to-end: real Flask + real workspace + real git → real JSON."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-contract-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        # Real workspace_manager — pinned to kato's metadata filename
        # so the on-disk record is in the same shape kato produces.
        # build_real_workspace_service uses the workspace_core_lib
        # default; the recovery service reads whichever filename the
        # data_access reports, so either filename works here.
        self.workspace_service = build_real_workspace_service(self.root)

        # Real workspace: one task, one repo. The "workspace_path"
        # returned by the service is where we materialize the clone.
        self.task_id = 'PROJ-CONTRACT'
        self.repo_id = 'client'
        self.workspace_service.create(
            task_id=self.task_id,
            task_summary='whats wrong with you please fix it',
            repository_ids=[self.repo_id],
        )
        workspace_dir = self.workspace_service.workspace_path(self.task_id)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        _build_local_repo_with_task_branch(
            workspace_dir, base_branch='main', task_branch=self.task_id,
        )
        # Rename the working clone to <repo_id>/ so the workspace
        # layout matches what kato uses: <workspace>/<repo_id>/.
        (workspace_dir / 'work').rename(workspace_dir / self.repo_id)

        # Mark it active so the recovery service / cleanup doesn't
        # touch it during the test.
        self.workspace_service.update_status(self.task_id, 'active')

        # Real Flask app with real fallback session manager. No mocks.
        self.app = create_app(
            session_manager=_build_fallback_manager(str(self.root / 'sessions')),
            workspace_manager=self.workspace_service,
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

    def test_files_payload_has_the_shape_the_ui_consumes(self) -> None:
        """``/files`` payload matches ``normalizeTrees`` expectations.

        The UI's ``normalizeTrees`` (in FilesTabHelpers.js) reads:
          payload.trees[*].repo_id : string
          payload.trees[*].cwd     : string
          payload.trees[*].tree    : array
          payload.trees[*].conflicted_files : array<string>
          payload.trees[*].changed_files    : array<string>

        Plus the legacy/back-compat scalars at the top level
        (cwd, tree, conflicted_files, changed_files).
        """
        payload = self._get_json(f'/api/sessions/{self.task_id}/files')

        self.assertIn('trees', payload)
        trees = payload['trees']
        self.assertIsInstance(trees, list)
        self.assertEqual(len(trees), 1, 'one repo configured')

        entry = trees[0]
        # Required keys for normalizeTrees().
        for key in ('repo_id', 'cwd', 'tree',
                    'conflicted_files', 'changed_files'):
            self.assertIn(key, entry, f'missing {key} in trees[0]')
        self.assertEqual(entry['repo_id'], self.repo_id)
        self.assertTrue(entry['cwd'].endswith(self.repo_id))
        self.assertIsInstance(entry['tree'], list)
        self.assertIsInstance(entry['conflicted_files'], list)
        self.assertIsInstance(entry['changed_files'], list)

        # The real edits we made on the task branch ARE in the
        # changed-files list — proves git diff actually ran and
        # the response carries real data, not empty placeholders.
        changed = set(entry['changed_files'])
        self.assertIn('src/app.py', changed)
        self.assertIn('src/helper.py', changed)

        # Back-compat scalars at the top level.
        for key in ('cwd', 'tree'):
            self.assertIn(key, payload)

    def test_diff_payload_has_the_shape_the_ui_consumes(self) -> None:
        """``/diff`` payload matches what FilesTab/ChangesTab expect."""
        payload = self._get_json(f'/api/sessions/{self.task_id}/diff')

        self.assertIn('diffs', payload)
        diffs = payload['diffs']
        self.assertIsInstance(diffs, list)
        self.assertEqual(len(diffs), 1)

        diff_entry = diffs[0]
        for key in ('repo_id', 'cwd', 'base', 'head', 'diff'):
            self.assertIn(key, diff_entry, f'missing {key} in diffs[0]')
        self.assertEqual(diff_entry['repo_id'], self.repo_id)
        self.assertEqual(diff_entry['base'], 'main')
        self.assertEqual(diff_entry['head'], self.task_id)
        # The real unified diff text has our edits in it.
        diff_text = diff_entry['diff']
        self.assertIn('src/app.py', diff_text)
        self.assertIn('src/helper.py', diff_text)
        self.assertIn('hello, fixed', diff_text)

        # Back-compat scalars at the top level.
        for key in ('repo_id', 'base', 'head', 'diff'):
            self.assertIn(key, payload)

    def test_commits_payload_has_the_shape_the_ui_consumes(self) -> None:
        """``/commits`` payload matches what the Files-tab commits dropdown reads."""
        payload = self._get_json(
            f'/api/sessions/{self.task_id}/commits?repo={self.repo_id}',
        )
        for key in ('repo_id', 'base', 'head', 'commits'):
            self.assertIn(key, payload)
        self.assertEqual(payload['repo_id'], self.repo_id)
        self.assertEqual(payload['base'], 'main')
        self.assertEqual(payload['head'], self.task_id)
        commits = payload['commits']
        self.assertIsInstance(commits, list)
        self.assertGreaterEqual(len(commits), 1)
        # Each commit has the keys the UI needs to render a list row.
        first = commits[0]
        for key in ('sha', 'subject', 'author', 'epoch'):
            self.assertIn(key, first, f'missing {key} in commits[0]')
        # The commit message we put on the task branch IS present.
        subjects = [c['subject'] for c in commits]
        self.assertIn('task: real edit + new helper', subjects)

    def test_commits_endpoint_rejects_missing_repo_param(self) -> None:
        """No ``repo`` query → 400, UI shows the error inline."""
        response = self.client.get(f'/api/sessions/{self.task_id}/commits')
        self.assertEqual(response.status_code, 400)
        self.assertIn('repo', response.get_json().get('error', ''))

    def test_file_payload_returns_real_file_contents(self) -> None:
        """``/file`` serves the actual bytes of a tracked workspace file."""
        # The path the UI receives from the file-tree endpoint is the
        # absolute clone path; the route also accepts a repo-relative
        # path which is what real users hit on copy-paste.
        relative_path = 'src/app.py'
        absolute = self.workspace_service.workspace_path(
            self.task_id,
        ) / self.repo_id / relative_path
        # Try absolute first — matches what the file-tree handler emits.
        response = self.client.get(
            f'/api/sessions/{self.task_id}/file?path={absolute}',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        # Required keys for the UI's Monaco editor.
        for key in ('content',):
            self.assertIn(key, payload, f'missing {key} in /file response')
        self.assertIn('hello, fixed', payload['content'])

    def test_file_endpoint_refuses_path_traversal_outside_workspace(self) -> None:
        """A path that escapes the workspace must be refused, not served."""
        evil = '/etc/passwd'
        response = self.client.get(
            f'/api/sessions/{self.task_id}/file?path={evil}',
        )
        # Either 403 (explicit refusal) or 404 (not in the resolved
        # candidate set). Both are acceptable; what's NOT acceptable
        # is 200 with /etc/passwd contents.
        self.assertIn(response.status_code, (400, 403, 404))
        body = response.get_data(as_text=True)
        self.assertNotIn('root:', body, 'leaked /etc/passwd contents')

    def test_file_endpoint_returns_binary_marker_for_nul_bytes(self) -> None:
        """A NUL-byte-containing file is flagged ``binary: true`` not served as text."""
        repo_path = self.workspace_service.workspace_path(
            self.task_id,
        ) / self.repo_id
        binary_file = repo_path / 'src' / 'data.bin'
        binary_file.write_bytes(b'\x00\x01\x02not text')
        _git(repo_path, 'add', 'src/data.bin')
        _git(repo_path, 'commit', '-m', 'add binary')

        response = self.client.get(
            f'/api/sessions/{self.task_id}/file?path={binary_file}',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(
            payload.get('binary'),
            f'binary file served as text: {payload!r}',
        )

    def _build_stable_fixture(self) -> dict:
        """Capture every endpoint's payload and normalise machine-specific bits.

        Tempdir-prefixed ``cwd`` paths and any absolute paths inside
        the unified-diff body get rewritten to
        ``/__fixture__/<repo_id>/...`` so the resulting JSON is
        byte-stable across machines / runs.
        """
        files_payload = _stabilize_cwd_payload(
            self._get_json(f'/api/sessions/{self.task_id}/files'),
            repo_id=self.repo_id,
        )
        diff_payload = _stabilize_cwd_payload(
            self._get_json(f'/api/sessions/{self.task_id}/diff'),
            repo_id=self.repo_id,
        )
        _stabilize_diff_text(diff_payload, tmp_prefix=str(self.root))
        commits_payload = self._get_json(
            f'/api/sessions/{self.task_id}/commits?repo={self.repo_id}',
        )
        # Stabilize all per-commit volatile fields. SHAs depend on
        # commit timestamps so they change every run; epoch is the
        # commit time itself.
        for c in commits_payload.get('commits', []):
            c['sha'] = '__FIXTURE_SHA__'
            c['epoch'] = 0
            if 'short_sha' in c:
                c['short_sha'] = '__FIX__'
        # /file: hit an actual tracked file and capture its response.
        absolute_path = self.workspace_service.workspace_path(
            self.task_id,
        ) / self.repo_id / 'src' / 'app.py'
        file_payload = self._get_json(
            f'/api/sessions/{self.task_id}/file?path={absolute_path}',
        )
        # /file echoes the absolute path back — tempdir-specific.
        if isinstance(file_payload.get('path'), str):
            file_payload['path'] = '/__fixture__/' + self.repo_id + '/src/app.py'
        return {
            'files': files_payload,
            'diff': diff_payload,
            'commits': commits_payload,
            'file': file_payload,
            'expected': {
                'task_id': self.task_id,
                'repo_id': self.repo_id,
                'changed_basenames': ['app.py', 'helper.py'],
                'commit_subjects_include': ['task: real edit + new helper'],
                'file_text_includes': 'hello, fixed',
            },
        }

    def test_committed_fixture_matches_what_the_backend_produces(self) -> None:
        """The committed fixture is in sync with the real backend output.

        Read-only on the default test path — DOES NOT touch the
        fixture file, so a normal ``kato test`` run leaves the
        worktree clean. If this fails, the backend has drifted from
        the committed bytes the UI contract test reads — regenerate
        by re-running with ``KATO_REGEN_CONTRACT_FIXTURES=1`` (see
        ``test_regenerate_committed_fixture``) and commit the diff.
        """
        self.assertTrue(
            _FIXTURE_PATH.is_file(),
            f'committed fixture missing: {_FIXTURE_PATH}. '
            'Run with KATO_REGEN_CONTRACT_FIXTURES=1 to create it.',
        )
        committed = json.loads(_FIXTURE_PATH.read_text(encoding='utf-8'))
        live = self._build_stable_fixture()
        self.assertEqual(
            live, committed,
            'backend payload no longer matches the committed UI contract '
            'fixture. Re-run this test file with '
            'KATO_REGEN_CONTRACT_FIXTURES=1 and commit the regenerated '
            f'{_FIXTURE_PATH.name} so the UI contract test stays in sync.',
        )

    @unittest.skipUnless(
        os.environ.get('KATO_REGEN_CONTRACT_FIXTURES'),
        'opt-in fixture regeneration; set KATO_REGEN_CONTRACT_FIXTURES=1 '
        'to write a fresh fixture from the real backend',
    )
    def test_regenerate_committed_fixture(self) -> None:
        """Opt-in: write the live backend payload to the committed fixture.

        Skipped by default so a normal ``kato test`` run never dirties
        the worktree. Trigger manually after a backend shape change:

            KATO_REGEN_CONTRACT_FIXTURES=1 \\
                python -m unittest tests.test_files_diff_contract
        """
        _FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        _FIXTURE_PATH.write_text(
            json.dumps(self._build_stable_fixture(), indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
        # Sanity: the file we just wrote round-trips through JSON.
        roundtrip = json.loads(_FIXTURE_PATH.read_text(encoding='utf-8'))
        self.assertEqual(roundtrip['expected']['repo_id'], self.repo_id)


def _stabilize_cwd_payload(payload: dict, *, repo_id: str) -> dict:
    """Rewrite tempdir ``cwd`` fields + tree paths to the stable token."""
    token = f'/__fixture__/{repo_id}'
    for tree in payload.get('trees', []):
        if tree.get('cwd'):
            tree['cwd'] = token
        _stabilize_tree_paths(tree.get('tree', []), repo_id=repo_id)
    for diff in payload.get('diffs', []):
        if diff.get('cwd'):
            diff['cwd'] = token
    if payload.get('cwd'):
        payload['cwd'] = token
    _stabilize_tree_paths(payload.get('tree', []), repo_id=repo_id)
    return payload


def _stabilize_diff_text(payload: dict, *, tmp_prefix: str) -> None:
    """Strip the absolute tempdir prefix out of unified-diff bodies."""
    for diff in payload.get('diffs', []):
        if isinstance(diff.get('diff'), str):
            diff['diff'] = diff['diff'].replace(tmp_prefix, '/__fixture__')
    if isinstance(payload.get('diff'), str):
        payload['diff'] = payload['diff'].replace(tmp_prefix, '/__fixture__')


def _stabilize_tree_paths(nodes: list, *, repo_id: str) -> None:
    """Recursively rewrite absolute ``path`` fields to the stable token."""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        path = node.get('path', '')
        if isinstance(path, str) and '/' in path:
            # Keep only the portion after the repo_id segment so the
            # path is stable across machines.
            idx = path.find('/' + repo_id + '/')
            if idx != -1:
                tail = path[idx + len(repo_id) + 2:]
                node['path'] = f'/__fixture__/{repo_id}/{tail}'
            elif path.endswith('/' + repo_id):
                node['path'] = f'/__fixture__/{repo_id}'
        if isinstance(node.get('children'), list):
            _stabilize_tree_paths(node['children'], repo_id=repo_id)


if __name__ == '__main__':
    unittest.main()
