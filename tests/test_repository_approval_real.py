"""RepositoryApprovalService against a REAL JSON sidecar on disk.

``RepositoryApprovalService`` reads / writes
``~/.kato/approved-repositories.json`` (or ``KATO_APPROVED_REPOSITORIES_PATH``).
The existing edge tests cover defensive single-line paths. This file
exercises the actual approve → list → revoke → re-approve lifecycle
through a real file on a real tempdir, plus concurrency on the same
sidecar — exactly the multi-thread case the per-process ``Lock``
inside the service is supposed to defend.

No file-mocking. No JSON-mocking. Real round-trips.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from kato_core_lib.data_layers.data.repository_approval import (
    ApprovalMode,
)
from kato_core_lib.data_layers.service.repository_approval_service import (
    APPROVED_REPOSITORIES_PATH_ENV_KEY,
    OPERATOR_EMAIL_ENV_KEY,
    RepositoryApprovalService,
    default_storage_path,
    operator_identity,
)

from tests.chaos_lib import (
    CHAOS_TASK_IDS_SAFE,
    impatient_comment,
)


class RepositoryApprovalLifecycleTests(unittest.TestCase):
    """Round-trip approve / list / revoke / re-approve through a real sidecar."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-approval-real-')
        self.addCleanup(self._tmp.cleanup)
        self.sidecar_path = Path(self._tmp.name) / 'approvals.json'
        self.service = RepositoryApprovalService(self.sidecar_path)

    def _file_payload(self) -> dict:
        # Read the raw on-disk JSON — bypasses the in-memory cache.
        return json.loads(self.sidecar_path.read_text(encoding='utf-8'))

    def test_approve_then_lookup_then_revoke_round_trip(self) -> None:
        # No file yet.
        self.assertFalse(self.sidecar_path.exists())

        # Approve writes the real file.
        approval = self.service.approve(
            'repo-a', 'https://git.example/repo-a.git',
            mode=ApprovalMode.RESTRICTED, approved_by='alice',
        )
        self.assertEqual(approval.repository_id, 'repo-a')
        self.assertTrue(self.sidecar_path.is_file())

        # is_approved reads back through the cache.
        self.assertEqual(
            self.service.is_approved('repo-a'), ApprovalMode.RESTRICTED,
        )
        # And a FRESH service instance (no cache) sees the same record.
        fresh = RepositoryApprovalService(self.sidecar_path)
        self.assertEqual(fresh.is_approved('repo-a'), ApprovalMode.RESTRICTED)

        # Revoke removes the on-disk entry.
        self.assertTrue(self.service.revoke('repo-a'))
        self.assertIsNone(self.service.is_approved('repo-a'))
        # The file still exists (now empty), revoking again returns False.
        self.assertFalse(self.service.revoke('repo-a'))

        on_disk = self._file_payload()
        self.assertEqual(on_disk.get('approved', []), [])

    def test_re_approve_with_same_mode_is_idempotent(self) -> None:
        first = self.service.approve(
            'repo-x', 'https://git/repo-x.git',
            mode=ApprovalMode.TRUSTED, approved_by='bob',
        )
        second = self.service.approve(
            'repo-x', 'https://git/repo-x.git',
            mode=ApprovalMode.TRUSTED, approved_by='bob',
        )
        self.assertEqual(first.approved_at_epoch, second.approved_at_epoch)

    def test_re_approve_upgrades_mode(self) -> None:
        self.service.approve(
            'repo-y', 'https://git/repo-y.git',
            mode=ApprovalMode.RESTRICTED, approved_by='carol',
        )
        self.service.approve(
            'repo-y', 'https://git/repo-y.git',
            mode=ApprovalMode.TRUSTED, approved_by='carol',
        )
        self.assertEqual(
            self.service.is_approved('repo-y'), ApprovalMode.TRUSTED,
        )
        # Real on-disk payload reflects the upgrade.
        on_disk = self._file_payload()
        repo_y = [e for e in on_disk['approved']
                  if e['repository_id'] == 'repo-y'][0]
        self.assertEqual(repo_y['approval_mode'], ApprovalMode.TRUSTED.value)

    def test_normalises_repository_id_to_lower(self) -> None:
        self.service.approve('REPO-CASEY', 'https://git/repo-casey.git')
        # All variants resolve.
        self.assertIsNotNone(self.service.is_approved('repo-casey'))
        self.assertIsNotNone(self.service.is_approved('Repo-Casey'))
        # On disk, stored lower-cased.
        on_disk = self._file_payload()
        self.assertEqual(on_disk['approved'][0]['repository_id'], 'repo-casey')

    def test_approve_rejects_blank_repository_id(self) -> None:
        with self.assertRaisesRegex(ValueError, 'repository_id must be non-empty'):
            self.service.approve('', 'https://git/x.git')
        with self.assertRaisesRegex(ValueError, 'repository_id must be non-empty'):
            self.service.approve('   ', 'https://git/x.git')

    def test_lookup_returns_full_record(self) -> None:
        self.service.approve(
            'repo-q', 'https://git/repo-q.git',
            mode=ApprovalMode.TRUSTED, approved_by='dan',
        )
        record = self.service.lookup('repo-q')
        self.assertIsNotNone(record)
        self.assertEqual(record.approval_mode, ApprovalMode.TRUSTED)
        self.assertEqual(record.approved_by, 'dan')
        self.assertEqual(record.remote_url, 'https://git/repo-q.git')


class RepositoryApprovalUnapprovedFilterTests(unittest.TestCase):
    """``unapproved_repository_ids`` and ``restricted_mode_repository_ids``
    against a real sidecar."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-approval-filter-')
        self.addCleanup(self._tmp.cleanup)
        self.service = RepositoryApprovalService(
            Path(self._tmp.name) / 'approvals.json',
        )
        self.service.approve('approved-restricted', 'https://x/r.git',
                             mode=ApprovalMode.RESTRICTED)
        self.service.approve('approved-trusted', 'https://x/t.git',
                             mode=ApprovalMode.TRUSTED)

    def _repos(self, *ids):
        return [type('R', (), {'id': i})() for i in ids]

    def test_unapproved_subset_returns_only_unapproved_ids(self) -> None:
        result = self.service.unapproved_repository_ids(self._repos(
            'approved-restricted', 'unknown-1', 'approved-trusted', 'unknown-2',
        ))
        self.assertEqual(result, ['unknown-1', 'unknown-2'])

    def test_restricted_mode_subset_excludes_trusted(self) -> None:
        result = self.service.restricted_mode_repository_ids(self._repos(
            'approved-restricted', 'approved-trusted', 'unknown-1',
        ))
        self.assertEqual(result, ['approved-restricted'])

    def test_blank_id_in_repository_list_is_silently_skipped(self) -> None:
        result = self.service.unapproved_repository_ids(self._repos(
            '', 'unknown-1',
        ))
        self.assertEqual(result, ['unknown-1'])


class RepositoryApprovalCorruptionToleranceTests(unittest.TestCase):
    """A torn / malformed sidecar must not crash the service."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-approval-corrupt-')
        self.addCleanup(self._tmp.cleanup)
        self.sidecar_path = Path(self._tmp.name) / 'approvals.json'

    def test_corrupt_json_treated_as_no_approvals(self) -> None:
        self.sidecar_path.write_text('{ not valid json', encoding='utf-8')
        service = RepositoryApprovalService(self.sidecar_path)
        # No crash; just returns empty.
        self.assertIsNone(service.is_approved('any-repo'))
        self.assertEqual(service.list_approvals(), ())

    def test_non_dict_payload_treated_as_no_approvals(self) -> None:
        # A previous version might have written a list at the top level.
        self.sidecar_path.write_text('["nope"]', encoding='utf-8')
        service = RepositoryApprovalService(self.sidecar_path)
        self.assertEqual(service.list_approvals(), ())

    def test_after_corrupt_read_an_approve_overwrites_with_valid_json(self) -> None:
        self.sidecar_path.write_text('garbage', encoding='utf-8')
        service = RepositoryApprovalService(self.sidecar_path)
        service.approve('repo-recover', 'https://x/r.git',
                        mode=ApprovalMode.RESTRICTED)
        # Now valid JSON again.
        on_disk = json.loads(self.sidecar_path.read_text(encoding='utf-8'))
        ids = [e['repository_id'] for e in on_disk['approved']]
        self.assertEqual(ids, ['repo-recover'])

    def test_blank_repository_id_lookup_returns_none(self) -> None:
        self.sidecar_path.write_text(
            json.dumps({'approved': []}), encoding='utf-8',
        )
        service = RepositoryApprovalService(self.sidecar_path)
        self.assertIsNone(service.is_approved(''))
        self.assertIsNone(service.is_approved('   '))


class RepositoryApprovalConcurrentApprovalsTests(unittest.TestCase):
    """The internal Lock must keep concurrent approve() from losing entries.

    Production case: webserver thread approves repo-a while the scan
    thread approves repo-b at the same moment. Both must end up in the
    on-disk sidecar.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-approval-conc-')
        self.addCleanup(self._tmp.cleanup)
        self.service = RepositoryApprovalService(
            Path(self._tmp.name) / 'approvals.json',
        )

    def test_20_concurrent_approve_calls_all_persist(self) -> None:
        # 20 different repos, approved in parallel. After all threads
        # join, every single one is on disk.
        ids = [f'concurrent-repo-{i}' for i in range(20)]

        def worker(repo_id):
            return self.service.approve(
                repo_id, f'https://git/{repo_id}.git',
                mode=ApprovalMode.RESTRICTED,
                approved_by='concurrent-test',
            )

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = [f.result() for f in as_completed(
                [pool.submit(worker, rid) for rid in ids],
            )]
        self.assertEqual(len(results), 20)

        # Real on-disk check: every id is there.
        approved_ids = sorted(e.repository_id for e in self.service.list_approvals())
        self.assertEqual(approved_ids, sorted(ids))

    def test_interleaved_approve_and_revoke_converge_to_consistent_state(
        self,
    ) -> None:
        # One thread approves N repos; another thread revokes them as
        # they appear. Result: stable end state, no exceptions, no
        # spurious entries.
        N = 30
        ids = [f'race-repo-{i}' for i in range(N)]
        stop = threading.Event()
        errors: list[BaseException] = []

        def approver():
            try:
                for rid in ids:
                    self.service.approve(rid, f'https://git/{rid}.git')
                    time.sleep(0.0005)
            except BaseException as exc:        # pragma: no cover
                errors.append(exc)
            finally:
                stop.set()

        def revoker():
            seen: set[str] = set()
            while not stop.is_set() or len(seen) < N:
                for entry in self.service.list_approvals():
                    if entry.repository_id in seen:
                        continue
                    seen.add(entry.repository_id)
                    self.service.revoke(entry.repository_id)
                if stop.is_set() and len(seen) >= N:
                    return
                time.sleep(0.0005)

        t_a = threading.Thread(target=approver)
        t_r = threading.Thread(target=revoker)
        t_a.start(); t_r.start()
        t_a.join(timeout=10.0); t_r.join(timeout=10.0)

        self.assertFalse(t_a.is_alive())
        self.assertFalse(t_r.is_alive())
        self.assertEqual(errors, [])
        # All N got approved AND revoked — final state is empty.
        self.assertEqual(self.service.list_approvals(), ())


class RepositoryApprovalChaosInputTests(unittest.TestCase):
    """Chaos-flavoured inputs through the real sidecar."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-approval-chaos-')
        self.addCleanup(self._tmp.cleanup)
        self.service = RepositoryApprovalService(
            Path(self._tmp.name) / 'approvals.json',
        )

    def test_chaos_repository_ids_round_trip_through_sidecar(self) -> None:
        for rid in CHAOS_TASK_IDS_SAFE:
            # Reusing the task-id corpus as repo ids — these are the
            # weird-but-valid identifiers operators actually pick.
            self.service.approve(
                rid, f'https://git/{rid}.git', approved_by='chaos',
            )
        approved = sorted(e.repository_id for e in self.service.list_approvals())
        expected = sorted(rid.lower() for rid in CHAOS_TASK_IDS_SAFE)
        self.assertEqual(approved, expected)

    def test_chaos_remote_url_with_weird_chars_is_preserved(self) -> None:
        # A real operator sometimes pastes a URL with spaces, query
        # strings, etc. The service stores it as-is (after trim).
        weird_url = 'https://git.example/  weird repo  ?ref=feat/x#anchor'
        self.service.approve('weird-url-repo', weird_url)
        approval = self.service.lookup('weird-url-repo')
        # ``normalized_text`` only strips outer whitespace; everything
        # in the middle survives.
        self.assertEqual(approval.remote_url, weird_url.strip())

    def test_corrupt_then_recover_then_revoke_works_end_to_end(self) -> None:
        # Write garbage, instantiate service, approve, revoke, list.
        # This is the full operator-recovery path in one go.
        sidecar = self.service.storage_path
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text('}}}}', encoding='utf-8')
        # Treat as empty, then approve normally.
        self.service.approve('after-corrupt', 'https://x/r.git')
        # File is now valid JSON again.
        json.loads(sidecar.read_text(encoding='utf-8'))
        # Revoke works.
        self.assertTrue(self.service.revoke('after-corrupt'))
        self.assertEqual(self.service.list_approvals(), ())


class RepositoryApprovalModuleHelpersTests(unittest.TestCase):
    """``default_storage_path`` and ``operator_identity`` end-to-end."""

    def test_default_storage_path_respects_env_override(self) -> None:
        import os
        target = '/tmp/kato-override-approvals.json'
        with self._env_override(APPROVED_REPOSITORIES_PATH_ENV_KEY, target):
            self.assertEqual(default_storage_path(), Path(target))

    def test_default_storage_path_expands_tilde(self) -> None:
        with self._env_override(APPROVED_REPOSITORIES_PATH_ENV_KEY,
                                '~/.kato-test/approvals.json'):
            resolved = default_storage_path()
            self.assertFalse(str(resolved).startswith('~'))

    def test_operator_identity_prefers_explicit_env(self) -> None:
        env = {OPERATOR_EMAIL_ENV_KEY: 'shay@example.com', 'USER': 'fallback'}
        self.assertEqual(operator_identity(env=env), 'shay@example.com')

    def test_operator_identity_falls_back_to_username(self) -> None:
        env = {'USER': 'fallback-user'}
        self.assertEqual(operator_identity(env=env), 'fallback-user')

    def test_operator_identity_uses_unknown_when_nothing_configured(self) -> None:
        self.assertEqual(operator_identity(env={}), 'unknown')

    def _env_override(self, key, value):
        import os
        from contextlib import contextmanager

        @contextmanager
        def ctx():
            prior = os.environ.get(key)
            os.environ[key] = value
            try:
                yield
            finally:
                if prior is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prior

        return ctx()


if __name__ == '__main__':
    unittest.main()
