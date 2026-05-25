"""AgentService coverage — comment queue / pipeline / remote-sync paths.

The queue / pipeline tests in this file used to mock the comment store
itself (a ``_FakeCommentStore`` stand-in for ``LocalCommentStore``) plus
``_safe_list_workspaces`` / ``_comment_store_for``. That left almost no
real code running — a regression in ``LocalCommentStore`` or the
workspace manager wouldn't have been caught. The first four test
classes now drive a REAL ``WorkspaceService`` on a real tempdir, with
REAL ``LocalCommentStore`` files on disk. Only the would-need-network
collaborators (TaskService, RepositoryService, etc.) stay mocked, and
only ``_run_comment_agent`` (which spawns Claude) is patched out.

Human-style impatient strings come from :mod:`tests.chaos_lib`.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.comment_core_lib import (
    CommentRecord,
    CommentSource,
    KatoCommentStatus,
    LocalCommentStore,
)
from kato_core_lib.data_layers.service.agent_service import AgentService

from tests.chaos_lib import (
    HUGE_BODY,
    build_real_agent_service,
    impatient_body,
    impatient_comment,
    impatient_title,
    materialize_workspace,
    queue_real_comment,
    real_store_for,
)


def _kwargs(**overrides):
    defaults = dict(
        task_service=MagicMock(),
        task_state_service=MagicMock(),
        implementation_service=MagicMock(),
        testing_service=MagicMock(),
        repository_service=MagicMock(),
        notification_service=MagicMock(),
    )
    defaults.update(overrides)
    return defaults


class _FakeCommentStore(object):
    """Minimal stand-in for LocalCommentStore.

    Kept for the legacy test classes further down this file
    (``AdvanceFinishedCommentRunsTests`` and friends) that test the
    session-driven advance path, where the store contention isn't
    the point under test. The queue / drain / requeue / complete
    tests near the top of this file run against a REAL
    ``LocalCommentStore`` via :mod:`tests.chaos_lib`.
    """

    def __init__(self, comments, raise_on_list=False):
        self._comments = comments
        self._raise_on_list = raise_on_list
        self.updated: list[tuple[str, str]] = []
        self.added = []

    def list(self):
        if self._raise_on_list:
            raise RuntimeError('store unreadable')
        return self._comments

    def update_kato_status(
        self, comment_id, *, kato_status, addressed_sha='', failure_reason='',
    ):
        self.updated.append((comment_id, kato_status))

    def add(self, record):
        self.added.append(record)
        return record


class DrainAllQueuedTaskCommentsTests(unittest.TestCase):
    """Real workspace + real comment stores. Was: every collaborator mocked."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-drain-')
        self.addCleanup(self._tmp.cleanup)
        self.service, self.workspace_service = build_real_agent_service(
            Path(self._tmp.name),
        )
        # Don't actually spawn Claude; everything else is real.
        self._run_patcher = patch.object(
            self.service, '_run_comment_agent', return_value=True,
        )
        self._run_patcher.start()
        self.addCleanup(self._run_patcher.stop)

    def test_drains_every_workspace_and_collects_started(self) -> None:
        # Real workspaces, real comment files on disk. The stores get
        # written/read for real; only Claude is stubbed.
        for task_id in ('UNA-1', 'UNA-2', 'UNA-3'):
            materialize_workspace(self.workspace_service, task_id)
        queue_real_comment(self.workspace_service, 'UNA-1',
                           body='fix it. urgent.')
        # UNA-2 stays empty — nothing to drain.
        queue_real_comment(self.workspace_service, 'UNA-3',
                           body=impatient_comment())

        results = self.service.drain_all_queued_task_comments()

        started_ids = [r['task_id'] for r in results]
        self.assertIn('UNA-1', started_ids)
        self.assertIn('UNA-3', started_ids)
        self.assertNotIn('UNA-2', started_ids)  # nothing queued there
        # And the on-disk state actually flipped to IN_PROGRESS.
        for task_id in ('UNA-1', 'UNA-3'):
            statuses = [c.kato_status
                        for c in real_store_for(self.workspace_service,
                                                task_id).list()]
            self.assertIn(KatoCommentStatus.IN_PROGRESS.value, statuses)

    def test_one_task_failing_does_not_abort_the_rest(self) -> None:
        materialize_workspace(self.workspace_service, 'UNA-1')
        materialize_workspace(self.workspace_service, 'UNA-2')
        queue_real_comment(self.workspace_service, 'UNA-1',
                           body='whats wrong with you???')
        queue_real_comment(self.workspace_service, 'UNA-2',
                           body='just fix this already')

        # Make the FIRST drain throw (real workspace iteration order is
        # sorted, so UNA-1 comes first). The second must still complete.
        real_drain = self.service.drain_next_queued_task_comment
        calls = []

        def flaky(task_id):
            calls.append(task_id)
            if task_id == 'UNA-1':
                raise RuntimeError('store exploded')
            return real_drain(task_id)

        with patch.object(self.service, 'drain_next_queued_task_comment',
                          side_effect=flaky):
            results = self.service.drain_all_queued_task_comments()

        self.assertEqual([r['task_id'] for r in results], ['UNA-2'])
        # And both tasks were attempted, not aborted at UNA-1.
        self.assertEqual(calls, ['UNA-1', 'UNA-2'])

    def test_no_workspaces_is_safe_and_blank_task_ids_are_skipped(self) -> None:
        # Real workspace-manager with nothing in it.
        self.assertEqual(self.service.drain_all_queued_task_comments(), [])
        # And once we add a real workspace but it has no comments, no
        # results come back (nothing to drain).
        materialize_workspace(self.workspace_service, 'UNA-9')
        self.assertEqual(self.service.drain_all_queued_task_comments(), [])

    def test_drain_handles_a_huge_comment_body(self) -> None:
        # Chaos: a 100KB body should not break the drain pipeline.
        materialize_workspace(self.workspace_service, 'BIG-1')
        queue_real_comment(self.workspace_service, 'BIG-1', body=HUGE_BODY)
        results = self.service.drain_all_queued_task_comments()
        self.assertEqual([r['task_id'] for r in results], ['BIG-1'])
        # The on-disk record is still readable after the round-trip.
        records = real_store_for(self.workspace_service, 'BIG-1').list()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].body, HUGE_BODY)


class RequeueStuckInProgressCommentsTests(unittest.TestCase):
    """Boot recovery against REAL on-disk comment stores."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-requeue-')
        self.addCleanup(self._tmp.cleanup)
        self.service, self.workspace_service = build_real_agent_service(
            Path(self._tmp.name),
        )

    def _seed(self, task_id: str, statuses: list[str]) -> list[str]:
        """Materialize a workspace and push comments at each status. Returns ids."""
        materialize_workspace(self.workspace_service, task_id)
        store = real_store_for(self.workspace_service, task_id)
        ids: list[str] = []
        for status in statuses:
            record = store.add(CommentRecord(
                repo_id='repo-1',
                body=impatient_comment(),
                author='op',
                source=CommentSource.LOCAL.value,
                kato_status=KatoCommentStatus(status).value,
            ))
            ids.append(record.id)
        return ids

    def test_only_in_progress_is_requeued_other_states_untouched(self) -> None:
        ids_1 = self._seed('UNA-1', ['in_progress', 'queued', 'addressed'])
        ids_2 = self._seed('UNA-2', ['in_progress', 'failed', 'idle'])

        requeued = self.service.requeue_stuck_in_progress_comments()

        # Two in_progress comments flipped to queued; rest untouched.
        flipped_pairs = sorted((r['task_id'], r['comment_id']) for r in requeued)
        self.assertEqual(flipped_pairs,
                         sorted([('UNA-1', ids_1[0]), ('UNA-2', ids_2[0])]))
        # Real on-disk verification.
        store_1 = {c.id: c.kato_status
                   for c in real_store_for(self.workspace_service, 'UNA-1').list()}
        self.assertEqual(store_1[ids_1[0]], KatoCommentStatus.QUEUED.value)
        self.assertEqual(store_1[ids_1[1]], KatoCommentStatus.QUEUED.value)
        self.assertEqual(store_1[ids_1[2]], KatoCommentStatus.ADDRESSED.value)

    def test_unreadable_store_does_not_abort_other_tasks(self) -> None:
        # Real workspace; the comment file is unreadable garbage on disk.
        materialize_workspace(self.workspace_service, 'UNA-1')
        store_path = real_store_for(self.workspace_service, 'UNA-1').storage_path
        store_path.write_text('{ this is not valid json', encoding='utf-8')
        # Second workspace is healthy with a real in_progress comment.
        ids = self._seed('UNA-2', ['in_progress'])

        requeued = self.service.requeue_stuck_in_progress_comments()
        self.assertEqual(requeued, [{'task_id': 'UNA-2', 'comment_id': ids[0]}])

    def test_blank_task_ids_and_no_workspaces_are_safe(self) -> None:
        # Empty workspace root → real path returns [].
        self.assertEqual(self.service.requeue_stuck_in_progress_comments(), [])
        # A workspace whose folder name was sanitized to empty would
        # be impossible in the real path (WorkspaceDataAccess rejects
        # blank task_ids on create), so this is the legit empty case.
        materialize_workspace(self.workspace_service, 'UNA-9')
        # No in_progress comments → no requeues.
        self.assertEqual(self.service.requeue_stuck_in_progress_comments(), [])

    def test_failed_per_comment_update_does_not_abort_the_rest(self) -> None:
        # Build a real store with two in_progress comments, then patch
        # ONLY update_kato_status to fail on the first id. The real
        # list() + iteration code still runs.
        ids = self._seed('UNA-1', ['in_progress', 'in_progress'])
        bad_id = ids[0]
        store = real_store_for(self.workspace_service, 'UNA-1')
        real_update = store.update_kato_status

        def flaky(comment_id, **kwargs):
            if comment_id == bad_id:
                raise RuntimeError('disk full')
            return real_update(comment_id, **kwargs)

        # Patch the store the service constructs lazily — return our
        # patched instance from _comment_store_for instead of mocking
        # the lookup itself.
        patched_store = MagicMock(wraps=store)
        patched_store.list.side_effect = store.list
        patched_store.update_kato_status.side_effect = flaky
        with patch.object(self.service, '_comment_store_for',
                          return_value=patched_store):
            requeued = self.service.requeue_stuck_in_progress_comments()

        self.assertEqual(requeued, [{'task_id': 'UNA-1', 'comment_id': ids[1]}])


class CompleteInProgressTaskCommentsTests(unittest.TestCase):
    """End-of-turn pipeline transition. Real store, real status writes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-complete-')
        self.addCleanup(self._tmp.cleanup)
        self.service, self.workspace_service = build_real_agent_service(
            Path(self._tmp.name),
        )

    def _seed(self, task_id: str, statuses: list[str]) -> tuple[
        list[str], LocalCommentStore,
    ]:
        materialize_workspace(self.workspace_service, task_id)
        store = real_store_for(self.workspace_service, task_id)
        ids: list[str] = []
        for status in statuses:
            record = store.add(CommentRecord(
                repo_id='repo-1',
                file_path='src/file.py',
                line=12,
                body=impatient_comment(),
                author='op',
                source=CommentSource.LOCAL.value,
                kato_status=KatoCommentStatus(status).value,
            ))
            ids.append(record.id)
        return ids, store

    def test_success_writes_real_reply_and_marks_addressed(self) -> None:
        ids, store = self._seed(
            'T1', ['in_progress', 'queued', 'addressed'],
        )
        in_progress_id = ids[0]

        # Real flow: writes a real reply record, real mark_comment_addressed,
        # real KatoStatus update on disk. No mark_comment_addressed mock.
        out = self.service.complete_in_progress_task_comments(
            'T1', success=True, result_text='ok, did it. push pls.',
        )

        # On-disk after the call: original is ADDRESSED, queued + addressed
        # untouched, and a NEW reply record exists pointing back at it.
        live = {c.id: c for c in store.list()}
        self.assertEqual(live[in_progress_id].kato_status,
                         KatoCommentStatus.ADDRESSED.value)
        self.assertEqual(live[ids[1]].kato_status, KatoCommentStatus.QUEUED.value)
        self.assertEqual(live[ids[2]].kato_status, KatoCommentStatus.ADDRESSED.value)
        reply_records = [c for c in store.list()
                         if c.parent_id == in_progress_id]
        self.assertEqual(len(reply_records), 1)
        self.assertEqual(reply_records[0].body, 'ok, did it. push pls.')
        self.assertEqual(reply_records[0].author, 'claude')

        # And the returned summary reports just the addressed one.
        self.assertEqual(
            out,
            [{'task_id': 'T1', 'comment_id': in_progress_id,
              'kato_status': KatoCommentStatus.ADDRESSED.value}],
        )

    def test_errored_turn_marks_in_progress_failed(self) -> None:
        ids, store = self._seed('T1', ['in_progress'])
        out = self.service.complete_in_progress_task_comments(
            'T1', success=False,
        )
        # On-disk now: FAILED with the canned reason from the source.
        live = store.list()
        self.assertEqual(live[0].kato_status, KatoCommentStatus.FAILED.value)
        self.assertEqual(out[0]['kato_status'], KatoCommentStatus.FAILED.value)

    def test_no_in_progress_is_a_noop(self) -> None:
        self._seed('T1', ['queued', 'addressed'])
        self.assertEqual(
            self.service.complete_in_progress_task_comments('T1', success=True),
            [],
        )

    def test_missing_store_is_isolated(self) -> None:
        # No workspace for this task — real _comment_store_for returns None.
        self.assertEqual(
            self.service.complete_in_progress_task_comments(
                'GHOST-TASK', success=True,
            ),
            [],
        )

    def test_per_comment_error_does_not_abort_the_rest(self) -> None:
        ids, store = self._seed('T1', ['in_progress', 'in_progress'])
        bad_id, good_id = ids

        real_mark = self.service.mark_comment_addressed

        def flaky(task_id, comment_id, **kwargs):
            if comment_id == bad_id:
                raise RuntimeError('whoops')
            return real_mark(task_id, comment_id, **kwargs)

        with patch.object(self.service, 'mark_comment_addressed',
                          side_effect=flaky):
            out = self.service.complete_in_progress_task_comments(
                'T1', success=True, result_text='done',
            )

        self.assertEqual(
            out,
            [{'task_id': 'T1', 'comment_id': good_id,
              'kato_status': KatoCommentStatus.ADDRESSED.value}],
        )
        # The good one really did flip on disk.
        live = {c.id: c.kato_status for c in store.list()}
        self.assertEqual(live[good_id], KatoCommentStatus.ADDRESSED.value)


class ResolveTaskCommentRemoteSyncTests(unittest.TestCase):
    def test_includes_remote_sync_when_kato_addressed(self) -> None:
        # Lines 749-754: include_reply=True when kato_status is ADDRESSED.
        from kato_core_lib.comment_core_lib import (
            CommentRecord, CommentSource, KatoCommentStatus,
        )
        service = AgentService(**_kwargs())
        addressed = CommentRecord(
            id='c1', body='b', repo_id='r1', author='a',
            source=CommentSource.REMOTE.value, remote_id='rem-1',
            kato_status=KatoCommentStatus.ADDRESSED.value,
        )
        store = MagicMock()
        store.update_status.return_value = addressed
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_sync_resolve_to_remote',
                          return_value={'attempted': True}) as sync:
            result = service.resolve_task_comment('T1', 'c1')
        sync.assert_called_once()
        # include_reply was True.
        self.assertTrue(sync.call_args.kwargs.get('include_reply'))


class MarkCommentAddressedRemoteSyncTests(unittest.TestCase):
    def test_includes_remote_reply_for_remote_comments(self) -> None:
        # Lines 800-806.
        from kato_core_lib.comment_core_lib import CommentRecord, CommentSource
        service = AgentService(**_kwargs())
        remote = CommentRecord(
            id='c1', body='b', repo_id='r1', author='a',
            source=CommentSource.REMOTE.value, remote_id='rem-1',
        )
        store = MagicMock()
        store.update_kato_status.return_value = remote
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_sync_addressed_reply_to_remote',
                          return_value={'attempted': True}) as sync:
            result = service.mark_comment_addressed('T1', 'c1')
        sync.assert_called_once()
        self.assertTrue(result['ok'])


class SyncRemoteCommentsTests(unittest.TestCase):
    def test_returns_error_when_no_workspace(self) -> None:
        service = AgentService(**_kwargs())
        result = service.sync_remote_comments('T1', 'r1')
        self.assertFalse(result['ok'])

    def test_returns_error_when_blank_repo_id(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        with patch.object(service, '_comment_store_for', return_value=store):
            result = service.sync_remote_comments('T1', '')
        self.assertFalse(result['ok'])

    def test_returns_error_when_workspace_manager_missing(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        with patch.object(service, '_comment_store_for', return_value=store):
            result = service.sync_remote_comments('T1', 'r1')
        self.assertFalse(result['ok'])

    def test_returns_error_on_workspace_path_exception(self) -> None:
        workspace = MagicMock()
        workspace.repository_path.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(workspace_manager=workspace))
        store = MagicMock()
        with patch.object(service, '_comment_store_for', return_value=store):
            result = service.sync_remote_comments('T1', 'r1')
        self.assertIn('no workspace clone', result['error'])

    def test_returns_error_when_clone_lacks_git_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = MagicMock()
            workspace.repository_path.return_value = Path(td)  # no .git
            service = AgentService(**_kwargs(workspace_manager=workspace))
            store = MagicMock()
            with patch.object(service, '_comment_store_for', return_value=store):
                result = service.sync_remote_comments('T1', 'r1')
        self.assertFalse(result['ok'])

    def test_records_pull_failure_in_response(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / '.git').mkdir()
            workspace = MagicMock()
            workspace.repository_path.return_value = Path(td)
            repo = MagicMock()
            repo._run_git = MagicMock(side_effect=RuntimeError('git fail'))
            service = AgentService(**_kwargs(
                workspace_manager=workspace, repository_service=repo,
            ))
            store = MagicMock()
            with patch.object(service, '_comment_store_for', return_value=store):
                result = service.sync_remote_comments('T1', 'r1')
        # Pull failed but the overall call still proceeds. The pull
        # result reflects the failure though.
        self.assertFalse(result['pull']['ok'])

    def test_returns_note_when_list_comments_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / '.git').mkdir()
            workspace = MagicMock()
            workspace.repository_path.return_value = Path(td)
            repo = MagicMock()
            # No list_pull_request_comments method.
            del repo.list_pull_request_comments
            service = AgentService(**_kwargs(
                workspace_manager=workspace, repository_service=repo,
            ))
            store = MagicMock()
            with patch.object(service, '_comment_store_for', return_value=store):
                result = service.sync_remote_comments('T1', 'r1')
        self.assertIn('platform listing unavailable', result.get('note', ''))

    def test_returns_note_when_no_pr_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / '.git').mkdir()
            workspace = MagicMock()
            workspace.repository_path.return_value = Path(td)
            repo = MagicMock()
            repo.list_pull_request_comments = MagicMock()
            service = AgentService(**_kwargs(
                workspace_manager=workspace, repository_service=repo,
            ))
            store = MagicMock()
            with patch.object(service, '_comment_store_for',
                              return_value=store), \
                 patch.object(service, '_task_pull_request_id',
                              return_value=''):
                result = service.sync_remote_comments('T1', 'r1')
        self.assertIn('no pull request', result.get('note', ''))

    def test_upserts_remote_comments_from_pr(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / '.git').mkdir()
            workspace = MagicMock()
            workspace.repository_path.return_value = Path(td)
            repo = MagicMock()
            repo.list_pull_request_comments.return_value = [
                {
                    'id': 'rem-1', 'body': 'fix this', 'file_path': 'a.py',
                    'line': 5, 'author': 'reviewer', 'resolved': False,
                },
                # entry with blank body — skipped.
                {'id': 'rem-2', 'body': ''},
            ]
            service = AgentService(**_kwargs(
                workspace_manager=workspace, repository_service=repo,
            ))
            store = MagicMock()
            with patch.object(service, '_comment_store_for',
                              return_value=store), \
                 patch.object(service, '_task_pull_request_id',
                              return_value='pr-1'):
                result = service.sync_remote_comments('T1', 'r1')
        self.assertEqual(len(result['synced']), 1)
        self.assertEqual(result['synced'][0]['remote_id'], 'rem-1')


class SyncResolveToRemoteTests(unittest.TestCase):
    def test_returns_error_on_repository_lookup_failure(self) -> None:
        repo = MagicMock()
        repo.get_repository.side_effect = RuntimeError('unknown')
        service = AgentService(**_kwargs(repository_service=repo))
        comment = SimpleNamespace(repo_id='r1', remote_id='rem-1')
        result = service._sync_resolve_to_remote('T1', comment, include_reply=False)
        self.assertIn('inventory lookup failed', result['error'])

    def test_returns_error_when_no_pull_request_id(self) -> None:
        service = AgentService(**_kwargs())
        comment = SimpleNamespace(
            repo_id='r1', remote_id='rem-1', kato_status='', kato_addressed_sha='',
        )
        with patch.object(service, '_task_pull_request_id', return_value=''):
            result = service._sync_resolve_to_remote('T1', comment, include_reply=False)
        self.assertIn('no pull request id', result['error'])

    def test_resolves_remote_and_records_success(self) -> None:
        repo = MagicMock()
        repo.get_repository.return_value = SimpleNamespace(id='r1')
        service = AgentService(**_kwargs(repository_service=repo))
        comment = SimpleNamespace(
            repo_id='r1', remote_id='rem-1', kato_status='',
            kato_addressed_sha='abc123',
        )
        with patch.object(service, '_task_pull_request_id', return_value='pr-1'):
            result = service._sync_resolve_to_remote(
                'T1', comment, include_reply=True,
            )
        self.assertTrue(result.get('reply_posted'))
        self.assertTrue(result.get('resolved'))

    def test_records_reply_error_when_reply_fails(self) -> None:
        repo = MagicMock()
        repo.get_repository.return_value = SimpleNamespace(id='r1')
        repo.reply_to_review_comment.side_effect = RuntimeError('reply fail')
        service = AgentService(**_kwargs(repository_service=repo))
        comment = SimpleNamespace(
            repo_id='r1', remote_id='rem-1', kato_status='',
            kato_addressed_sha='abc123',
        )
        with patch.object(service, '_task_pull_request_id', return_value='pr-1'):
            result = service._sync_resolve_to_remote(
                'T1', comment, include_reply=True,
            )
        self.assertIn('reply_error', result)

    def test_records_resolve_error_when_resolve_fails(self) -> None:
        repo = MagicMock()
        repo.get_repository.return_value = SimpleNamespace(id='r1')
        repo.resolve_review_comment.side_effect = RuntimeError('resolve fail')
        service = AgentService(**_kwargs(repository_service=repo))
        comment = SimpleNamespace(
            repo_id='r1', remote_id='rem-1', kato_status='', kato_addressed_sha='',
        )
        with patch.object(service, '_task_pull_request_id', return_value='pr-1'):
            result = service._sync_resolve_to_remote(
                'T1', comment, include_reply=False,
            )
        self.assertIn('resolve_error', result)


class SyncAddressedReplyToRemoteTests(unittest.TestCase):
    def test_returns_error_on_repository_lookup_failure(self) -> None:
        repo = MagicMock()
        repo.get_repository.side_effect = RuntimeError('unknown')
        service = AgentService(**_kwargs(repository_service=repo))
        comment = SimpleNamespace(repo_id='r1', remote_id='rem-1')
        result = service._sync_addressed_reply_to_remote('T1', comment)
        self.assertIn('inventory lookup', result['error'])

    def test_returns_error_when_no_pull_request_id(self) -> None:
        service = AgentService(**_kwargs())
        comment = SimpleNamespace(
            repo_id='r1', remote_id='rem-1', kato_addressed_sha='',
        )
        with patch.object(service, '_task_pull_request_id', return_value=''):
            result = service._sync_addressed_reply_to_remote('T1', comment)
        self.assertIn('no pull request id', result['error'])

    def test_posts_reply_with_commit_sha(self) -> None:
        repo = MagicMock()
        repo.get_repository.return_value = SimpleNamespace(id='r1')
        service = AgentService(**_kwargs(repository_service=repo))
        comment = SimpleNamespace(
            repo_id='r1', remote_id='rem-1', kato_addressed_sha='abc12345',
        )
        with patch.object(service, '_task_pull_request_id', return_value='pr-1'):
            result = service._sync_addressed_reply_to_remote('T1', comment)
        self.assertTrue(result.get('reply_posted'))

    def test_posts_reply_without_commit_sha(self) -> None:
        # ``kato_addressed_sha`` blank → 'Addressed.' fallback text.
        repo = MagicMock()
        repo.get_repository.return_value = SimpleNamespace(id='r1')
        service = AgentService(**_kwargs(repository_service=repo))
        comment = SimpleNamespace(
            repo_id='r1', remote_id='rem-1', kato_addressed_sha='',
        )
        with patch.object(service, '_task_pull_request_id', return_value='pr-1'):
            result = service._sync_addressed_reply_to_remote('T1', comment)
        self.assertTrue(result.get('reply_posted'))

    def test_records_reply_error_when_reply_fails(self) -> None:
        repo = MagicMock()
        repo.get_repository.return_value = SimpleNamespace(id='r1')
        repo.reply_to_review_comment.side_effect = RuntimeError('post fail')
        service = AgentService(**_kwargs(repository_service=repo))
        comment = SimpleNamespace(
            repo_id='r1', remote_id='rem-1', kato_addressed_sha='abc',
        )
        with patch.object(service, '_task_pull_request_id', return_value='pr-1'):
            result = service._sync_addressed_reply_to_remote('T1', comment)
        self.assertIn('reply_error', result)


class MaybeTriggerCommentRunTests(unittest.TestCase):
    """``_maybe_trigger_comment_run`` against REAL on-disk stores.

    Only the actual Claude spawn (``_run_comment_agent``) and the busy-turn
    check (``_task_has_busy_turn`` — needs a live session manager) get
    patched; the store, the workspace listing, and the kato-status writes
    all run through real code.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-trigger-')
        self.addCleanup(self._tmp.cleanup)
        self.service, self.workspace_service = build_real_agent_service(
            Path(self._tmp.name),
        )
        # Default: no live turn for any task. Tests that need busy=True
        # override per-call.
        self._busy_patch = patch.object(
            self.service, '_task_has_busy_turn', return_value=False,
        )
        self._busy_patch.start()
        self.addCleanup(self._busy_patch.stop)

    def _seed_comment(self, task_id: str, body: str | None = None) -> str:
        materialize_workspace(self.workspace_service, task_id)
        record = queue_real_comment(
            self.workspace_service, task_id, body=body or impatient_comment(),
        )
        return record.id

    # ----- drain_next_queued_task_comment -----

    def test_drain_next_queued_returns_error_when_no_workspace(self) -> None:
        # No materialised workspace → real _comment_store_for returns None.
        result = self.service.drain_next_queued_task_comment('GHOST')
        self.assertFalse(result['ok'])
        self.assertFalse(result['started'])

    def test_drain_next_queued_returns_idle_when_queue_empty(self) -> None:
        materialize_workspace(self.workspace_service, 'T1')
        result = self.service.drain_next_queued_task_comment('T1')
        self.assertTrue(result['ok'])
        self.assertFalse(result['started'])
        self.assertEqual(result['comment_id'], '')

    def test_drain_next_queued_triggers_oldest_comment(self) -> None:
        # Two queued comments — drain should pick the oldest.
        first_id = self._seed_comment('T1', body='whats wrong???')
        # ``created_at_epoch`` granularity is float seconds; ensure ordering.
        import time
        time.sleep(0.01)
        second_id = self._seed_comment('T1', body='do it now')
        self.assertNotEqual(first_id, second_id)

        with patch.object(self.service, '_run_comment_agent', return_value=True):
            result = self.service.drain_next_queued_task_comment('T1')

        self.assertTrue(result['ok'])
        self.assertTrue(result['started'])
        self.assertEqual(result['comment_id'], first_id)
        # On-disk: first flipped, second still queued.
        live = {c.id: c.kato_status
                for c in real_store_for(self.workspace_service, 'T1').list()}
        self.assertEqual(live[first_id], KatoCommentStatus.IN_PROGRESS.value)
        self.assertEqual(live[second_id], KatoCommentStatus.QUEUED.value)

    # ----- _maybe_trigger_comment_run -----

    def test_returns_false_when_no_workspace(self) -> None:
        self.assertFalse(self.service._maybe_trigger_comment_run('GHOST', 'c1'))

    def test_returns_false_when_comment_id_missing(self) -> None:
        materialize_workspace(self.workspace_service, 'T1')
        self.assertFalse(self.service._maybe_trigger_comment_run('T1', 'no-such'))

    def test_returns_false_when_turn_busy(self) -> None:
        comment_id = self._seed_comment('T1')
        with patch.object(self.service, '_task_has_busy_turn', return_value=True):
            self.assertFalse(
                self.service._maybe_trigger_comment_run('T1', comment_id),
            )
        # The store was NOT updated — comment stays queued.
        live = real_store_for(self.workspace_service, 'T1').list()[0]
        self.assertEqual(live.kato_status, KatoCommentStatus.QUEUED.value)

    def test_swallows_run_comment_agent_exception_and_logs(self) -> None:
        comment_id = self._seed_comment('T1', body='help me!!!')
        self.service.logger = MagicMock()  # so we can assert exception() fired
        with patch.object(self.service, '_run_comment_agent',
                          side_effect=RuntimeError('agent fail')):
            result = self.service._maybe_trigger_comment_run('T1', comment_id)
        self.assertFalse(result)
        self.service.logger.exception.assert_called()

    def test_returns_true_on_successful_run_and_marks_in_progress_on_disk(self) -> None:
        comment_id = self._seed_comment('T1', body='just make it work')
        with patch.object(self.service, '_run_comment_agent', return_value=True):
            result = self.service._maybe_trigger_comment_run('T1', comment_id)
        self.assertTrue(result)
        live = real_store_for(self.workspace_service, 'T1').list()[0]
        self.assertEqual(live.kato_status, KatoCommentStatus.IN_PROGRESS.value)

    def test_requeues_on_disk_when_comment_run_cannot_start(self) -> None:
        # Agent declines to run (returns False) — comment must go back to
        # QUEUED so the next scan tick retries it.
        comment_id = self._seed_comment('T1', body='do better')
        with patch.object(self.service, '_run_comment_agent', return_value=False):
            result = self.service._maybe_trigger_comment_run('T1', comment_id)
        self.assertFalse(result)
        live = real_store_for(self.workspace_service, 'T1').list()[0]
        # Real round-trip: in_progress → queued, persisted to disk.
        self.assertEqual(live.kato_status, KatoCommentStatus.QUEUED.value)

    def test_concurrent_dispatches_are_serialized_per_task(self) -> None:
        # Regression: two ``_maybe_trigger_comment_run`` calls racing on
        # the same task (scan-tick drain + browser POST landing in the
        # same window) used to BOTH pass the busy check before either
        # had incremented ``user_messages_sent``, flip THEIR comment to
        # IN_PROGRESS, and call ``send_user_message``. Both comments
        # then rode the same RESULT and the FIRST one's result_text was
        # attached to BOTH (visible symptom: kato's reply to a comment
        # was about a completely unrelated change). The per-task
        # dispatch lock now serializes the entire busy-check → flip →
        # send sequence so only one comment can be in flight at a time.
        import threading
        first_id = self._seed_comment('T1', body='comment A')
        import time
        time.sleep(0.01)
        second_id = self._seed_comment('T1', body='comment B')

        dispatch_order: list[str] = []
        dispatch_lock = threading.Lock()
        # Once the first run claims the dispatch lock, simulate a
        # ``send_user_message`` that hasn't yet produced a Claude event
        # by bumping a busy flag the patched ``_task_has_busy_turn``
        # reads. The second concurrent run must see "busy" and bail.
        busy_after_first = {'flag': False}

        def fake_run(task_id, record):
            with dispatch_lock:
                dispatch_order.append(record.id)
                busy_after_first['flag'] = True
            time.sleep(0.05)  # widen the race window
            return True

        def busy_check(task_id):
            return busy_after_first['flag']

        with patch.object(self.service, '_run_comment_agent', side_effect=fake_run), \
             patch.object(self.service, '_task_has_busy_turn', side_effect=busy_check):
            t1 = threading.Thread(
                target=self.service._maybe_trigger_comment_run,
                args=('T1', first_id),
            )
            t2 = threading.Thread(
                target=self.service._maybe_trigger_comment_run,
                args=('T1', second_id),
            )
            t1.start(); t2.start()
            t1.join(); t2.join()

        # Exactly ONE comment got dispatched; the other stayed QUEUED.
        # Without the lock, BOTH would have been flipped IN_PROGRESS.
        live = {c.id: c.kato_status
                for c in real_store_for(self.workspace_service, 'T1').list()}
        in_progress = [cid for cid, s in live.items()
                       if s == KatoCommentStatus.IN_PROGRESS.value]
        queued = [cid for cid, s in live.items()
                  if s == KatoCommentStatus.QUEUED.value]
        self.assertEqual(len(in_progress), 1, live)
        self.assertEqual(len(queued), 1, live)
        self.assertEqual(len(dispatch_order), 1)


class RunCommentAgentTests(unittest.TestCase):
    def test_returns_silently_when_no_session_manager(self) -> None:
        service = AgentService(**_kwargs())
        result = service._run_comment_agent('T1', SimpleNamespace(id='c1'))
        self.assertFalse(result)

    def test_returns_false_when_session_dead_and_no_runner(self) -> None:
        session = MagicMock()
        session.get_session.return_value = SimpleNamespace(is_alive=False)
        service = AgentService(**_kwargs(session_manager=session))
        result = service._run_comment_agent(
            'T1', SimpleNamespace(id='c1', file_path='', line=-1, body=''),
        )
        self.assertFalse(result)

    def test_respawns_session_for_comment_when_session_dead(self) -> None:
        session = MagicMock()
        session.get_session.return_value = SimpleNamespace(is_alive=False)
        runner = MagicMock()
        workspace_manager = MagicMock()
        workspace_manager.repository_path.return_value = Path('/tmp/repo')
        workspace_manager.get.return_value = SimpleNamespace(task_summary='Do it')
        service = AgentService(**_kwargs(
            session_manager=session,
            planning_session_runner=runner,
            workspace_manager=workspace_manager,
        ))
        result = service._run_comment_agent(
            'T1',
            SimpleNamespace(
                id='c1', repo_id='r1', file_path='a.py',
                line=5, body='fix this',
            ),
        )
        self.assertTrue(result)
        runner.resume_session_for_chat.assert_called_once()
        kwargs = runner.resume_session_for_chat.call_args.kwargs
        self.assertEqual(kwargs['task_id'], 'T1')
        self.assertEqual(kwargs['cwd'], '/tmp/repo')
        self.assertIn('fix this', kwargs['message'])

    def test_warns_when_no_planning_runner_so_claude_stays_idle(self) -> None:
        # The "Claude is idle, not working on my comment" case must be
        # loud, not a silent False.
        session = MagicMock()
        session.get_session.return_value = SimpleNamespace(is_alive=False)
        service = AgentService(**_kwargs(session_manager=session))
        service.logger = MagicMock()
        result = service._run_comment_agent(
            'T1', SimpleNamespace(id='c9', file_path='', line=-1, body=''),
        )
        self.assertFalse(result)
        service.logger.warning.assert_called_once()
        self.assertIn('no planning session', service.logger.warning.call_args.args[0])

    def test_logs_when_respawning_claude_for_comment(self) -> None:
        session = MagicMock()
        session.get_session.return_value = SimpleNamespace(is_alive=False)
        runner = MagicMock()
        workspace_manager = MagicMock()
        workspace_manager.repository_path.return_value = Path('/tmp/repo')
        workspace_manager.get.return_value = SimpleNamespace(task_summary='Do it')
        service = AgentService(**_kwargs(
            session_manager=session,
            planning_session_runner=runner,
            workspace_manager=workspace_manager,
        ))
        service.logger = MagicMock()
        service._run_comment_agent(
            'T1',
            SimpleNamespace(id='c1', repo_id='r1', file_path='a.py',
                            line=5, body='fix this'),
        )
        service.logger.info.assert_called_once()
        self.assertIn('respawning Claude', service.logger.info.call_args.args[0])

    def test_sends_prompt_to_live_session(self) -> None:
        session_obj = SimpleNamespace(
            is_alive=True, send_user_message=MagicMock(),
        )
        session = MagicMock()
        session.get_session.return_value = session_obj
        service = AgentService(**_kwargs(session_manager=session))
        service._run_comment_agent(
            'T1',
            SimpleNamespace(
                id='c1', file_path='a.py', line=5, body='fix this',
            ),
        )
        session_obj.send_user_message.assert_called_once()

    def test_no_op_when_session_lacks_send_method(self) -> None:
        # Lines 1180-1181: ``if not callable(send): return``.
        session_obj = SimpleNamespace(is_alive=True)
        session = MagicMock()
        session.get_session.return_value = session_obj
        service = AgentService(**_kwargs(session_manager=session))
        result = service._run_comment_agent(
            'T1',
            SimpleNamespace(id='c1', file_path='a.py', line=5, body='b'),
        )
        self.assertFalse(result)


class TaskPullRequestIdLiveLookupTests(unittest.TestCase):
    def test_uses_repository_service_find_when_registry_empty(self) -> None:
        # Lines 1232-1256: fall back to find_pull_requests when registry
        # has no matching context.
        review = MagicMock()
        review.state_registry.list_pull_request_contexts.return_value = []
        repo = MagicMock()
        inventory = SimpleNamespace(id='r1')
        repo.get_repository.return_value = inventory
        repo.build_branch_name.return_value = 'T1'
        repo.find_pull_requests.return_value = [
            {'id': '17', 'url': 'https://example.com/pr/17'},
        ]
        service = AgentService(**_kwargs(
            review_comment_service=review,
            repository_service=repo,
        ))
        result = service._task_pull_request_id('T1', 'r1')
        self.assertEqual(result, '17')

    def test_returns_empty_when_build_branch_name_fails(self) -> None:
        # Lines 1240-1241.
        review = MagicMock()
        review.state_registry.list_pull_request_contexts.return_value = []
        repo = MagicMock()
        repo.get_repository.return_value = SimpleNamespace(id='r1')
        repo.build_branch_name.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(
            review_comment_service=review,
            repository_service=repo,
        ))
        self.assertEqual(service._task_pull_request_id('T1', 'r1'), '')

    def test_returns_empty_when_find_pull_requests_fails(self) -> None:
        # Lines 1247-1249.
        review = MagicMock()
        review.state_registry.list_pull_request_contexts.return_value = []
        repo = MagicMock()
        repo.get_repository.return_value = SimpleNamespace(id='r1')
        repo.build_branch_name.return_value = 'T1'
        repo.find_pull_requests.side_effect = RuntimeError('api fail')
        service = AgentService(**_kwargs(
            review_comment_service=review,
            repository_service=repo,
        ))
        self.assertEqual(service._task_pull_request_id('T1', 'r1'), '')


class CreatePullRequestForTaskTests(unittest.TestCase):
    def test_returns_error_for_blank_task_id(self) -> None:
        service = AgentService(**_kwargs())
        result = service.create_pull_request_for_task('')
        self.assertFalse(result['created'])

    def test_returns_error_when_no_workspace_context(self) -> None:
        service = AgentService(**_kwargs())
        with patch.object(service, '_resolve_publish_context',
                          return_value=([], '', None)):
            result = service.create_pull_request_for_task('T1')
        self.assertFalse(result['created'])

    def test_skips_existing_pr(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'T1'
        repo.find_pull_requests.return_value = [
            {'url': 'https://example.com/pr/17'},
        ]
        service = AgentService(**_kwargs(repository_service=repo))
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'T1',
                                        SimpleNamespace(id='T1', summary='x'))):
            result = service.create_pull_request_for_task('T1')
        self.assertFalse(result['created'])
        self.assertEqual(len(result['skipped_existing']), 1)

    def test_creates_pr_when_none_exists(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'T1'
        repo.find_pull_requests.return_value = []
        repo.create_pull_request.return_value = {
            'url': 'https://example.com/pr/17',
        }
        service = AgentService(**_kwargs(repository_service=repo))
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'T1',
                                        SimpleNamespace(id='T1', summary='x'))):
            result = service.create_pull_request_for_task('T1')
        self.assertTrue(result['created'])

    def test_swallows_find_pull_requests_exception_and_proceeds(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'T1'
        repo.find_pull_requests.side_effect = RuntimeError('api')
        repo.create_pull_request.return_value = {
            'url': 'https://example.com/pr/17',
        }
        service = AgentService(**_kwargs(repository_service=repo))
        service.logger = MagicMock()
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'T1',
                                        SimpleNamespace(id='T1', summary='x'))):
            result = service.create_pull_request_for_task('T1')
        # find_pull_requests crashed → existing=[] → create_pull_request fires.
        self.assertTrue(result['created'])

    def test_records_expected_exception_as_failure(self) -> None:
        from kato_core_lib.data_layers.service.repository_service import (
            RepositoryHasNoChangesError,
        )
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'T1'
        repo.find_pull_requests.return_value = []
        repo.create_pull_request.side_effect = RepositoryHasNoChangesError('no')
        service = AgentService(**_kwargs(repository_service=repo))
        service.logger = MagicMock()
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'T1',
                                        SimpleNamespace(id='T1', summary='x'))):
            result = service.create_pull_request_for_task('T1')
        self.assertEqual(len(result['failed_repositories']), 1)

    def test_records_workspace_drift_runtime_error_as_failure(self) -> None:
        # Line 2076: 'expected repository' → warn + record.
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'T1'
        repo.find_pull_requests.return_value = []
        repo.create_pull_request.side_effect = RuntimeError(
            'expected repository to be on T1',
        )
        service = AgentService(**_kwargs(repository_service=repo))
        service.logger = MagicMock()
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'T1',
                                        SimpleNamespace(id='T1', summary='x'))):
            result = service.create_pull_request_for_task('T1')
        self.assertEqual(len(result['failed_repositories']), 1)

    def test_records_unexpected_runtime_error_with_stacktrace(self) -> None:
        # Line 2084-2091: RuntimeError that doesn't match the known
        # patterns → log.exception + record.
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'T1'
        repo.find_pull_requests.return_value = []
        repo.create_pull_request.side_effect = RuntimeError('something else')
        service = AgentService(**_kwargs(repository_service=repo))
        service.logger = MagicMock()
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'T1',
                                        SimpleNamespace(id='T1', summary='x'))):
            result = service.create_pull_request_for_task('T1')
        self.assertEqual(len(result['failed_repositories']), 1)
        service.logger.exception.assert_called()

    def test_records_generic_exception(self) -> None:
        # Lines 2092-2099.
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'T1'
        repo.find_pull_requests.return_value = []
        repo.create_pull_request.side_effect = OSError('FS fail')
        service = AgentService(**_kwargs(repository_service=repo))
        service.logger = MagicMock()
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'T1',
                                        SimpleNamespace(id='T1', summary='x'))):
            result = service.create_pull_request_for_task('T1')
        self.assertEqual(len(result['failed_repositories']), 1)


class FinishTaskPlanningSessionTests(unittest.TestCase):
    def test_returns_error_for_blank_task_id(self) -> None:
        service = AgentService(**_kwargs())
        self.assertFalse(service.finish_task_planning_session('')['finished'])

    def test_moves_to_review_on_success(self) -> None:
        task_state = MagicMock()
        service = AgentService(**_kwargs(task_state_service=task_state))
        with patch.object(service, 'push_task',
                          return_value={'pushed': True}), \
             patch.object(service, 'create_pull_request_for_task',
                          return_value={'created': True}), \
             patch.object(service, '_kick_lesson_extraction'):
            result = service.finish_task_planning_session('T1')
        self.assertTrue(result['finished'])
        task_state.move_task_to_review.assert_called_once()

    def test_records_move_error_on_failure(self) -> None:
        task_state = MagicMock()
        task_state.move_task_to_review.side_effect = RuntimeError('move fail')
        service = AgentService(**_kwargs(task_state_service=task_state))
        service.logger = MagicMock()
        with patch.object(service, 'push_task', return_value={}), \
             patch.object(service, 'create_pull_request_for_task',
                          return_value={}), \
             patch.object(service, '_kick_lesson_extraction'):
            result = service.finish_task_planning_session('T1')
        self.assertFalse(result['finished'])
        self.assertIn('move fail', result['move_error'])


class KickLessonExtractionTests(unittest.TestCase):
    def test_returns_silently_when_no_lessons_service(self) -> None:
        service = AgentService(**_kwargs())
        # No raise.
        service._kick_lesson_extraction('T1', {}, {})

    def test_spawns_worker_thread_and_swallows_extract_exception(self) -> None:
        # Lines 2195-2208.
        lessons = MagicMock()
        lessons.extract_and_save.side_effect = RuntimeError('llm fail')
        service = AgentService(**_kwargs(lessons_service=lessons))
        # Task service raises when fetching the task — drives lines
        # 2184-2186 (the fallback to blank summary/description).
        service._task_service.get_task = MagicMock(
            side_effect=RuntimeError('fail'),
        )
        service._kick_lesson_extraction('T1', {}, {})
        # Worker fires async — give it a moment.
        import time
        time.sleep(0.05)


class TaskPublishStateTests(unittest.TestCase):
    def test_returns_default_for_blank_task_id(self) -> None:
        service = AgentService(**_kwargs())
        result = service.task_publish_state('')
        self.assertFalse(result['has_workspace'])

    def test_returns_default_when_no_workspace_context(self) -> None:
        service = AgentService(**_kwargs())
        with patch.object(service, '_resolve_publish_context',
                          return_value=([], '', None)):
            result = service.task_publish_state('T1')
        self.assertFalse(result['has_workspace'])

    def test_reports_changes_to_push(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'T1'
        repo.branch_needs_push.return_value = True
        repo.find_pull_requests.return_value = []
        service = AgentService(**_kwargs(repository_service=repo))
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'T1',
                                        SimpleNamespace(id='T1'))):
            result = service.task_publish_state('T1')
        self.assertTrue(result['has_workspace'])
        self.assertTrue(result['has_changes_to_push'])

    def test_reports_existing_pull_request(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'T1'
        repo.branch_needs_push.return_value = False
        repo.find_pull_requests.return_value = [
            {'url': 'https://example.com/pr/17'},
        ]
        service = AgentService(**_kwargs(repository_service=repo))
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'T1',
                                        SimpleNamespace(id='T1'))):
            result = service.task_publish_state('T1')
        self.assertTrue(result['has_pull_request'])

    def test_swallows_branch_needs_push_exception(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'T1'
        repo.branch_needs_push.side_effect = RuntimeError('fail')
        repo.find_pull_requests.return_value = []
        service = AgentService(**_kwargs(repository_service=repo))
        service.logger = MagicMock()
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'T1',
                                        SimpleNamespace(id='T1'))):
            result = service.task_publish_state('T1')
        # Defaults to False on error.
        self.assertFalse(result['has_changes_to_push'])
        service.logger.exception.assert_called()

    def test_swallows_find_pull_requests_exception(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'T1'
        repo.branch_needs_push.return_value = False
        repo.find_pull_requests.side_effect = RuntimeError('api fail')
        service = AgentService(**_kwargs(repository_service=repo))
        service.logger = MagicMock()
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'T1',
                                        SimpleNamespace(id='T1'))):
            result = service.task_publish_state('T1')
        self.assertFalse(result['has_pull_request'])
        service.logger.exception.assert_called()


class ResolvePublishContextTests(unittest.TestCase):
    def test_returns_empty_when_no_workspace_manager(self) -> None:
        service = AgentService(**_kwargs())
        self.assertEqual(
            service._resolve_publish_context('T1'),
            ([], '', None),
        )

    def test_returns_empty_when_workspace_missing(self) -> None:
        workspace = MagicMock()
        workspace.get.return_value = None
        service = AgentService(**_kwargs(workspace_manager=workspace))
        self.assertEqual(
            service._resolve_publish_context('T1'),
            ([], '', None),
        )

    def test_unknown_repository_gets_stub_from_clone_path(self) -> None:
        # Unknown inventory repos with a valid clone path become stubs so
        # git-only operations (push, branch-check) still work when
        # REPOSITORY_ROOT_PATH is misconfigured.
        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(
            repository_ids=['known', 'unknown'],
            task_summary='x',
        )
        workspace.repository_path.return_value = Path('/clone')
        repo = MagicMock()

        def fake_get(rid):
            if rid == 'unknown':
                raise ValueError('not in inventory')
            return SimpleNamespace(id='known', local_path='/inventory')

        repo.get_repository.side_effect = fake_get
        repo.build_branch_name.return_value = 'T1'
        service = AgentService(**_kwargs(
            workspace_manager=workspace, repository_service=repo,
        ))
        service.logger = MagicMock()
        repos, branch, _ = service._resolve_publish_context('T1')
        # Both repos appear — the known one is a full copy, the unknown
        # one is a stub built from the workspace clone path.
        self.assertEqual(len(repos), 2)
        ids = {r.id for r in repos}
        self.assertEqual(ids, {'known', 'unknown'})
        for r in repos:
            self.assertEqual(r.local_path, '/clone')
        service.logger.warning.assert_not_called()
        service.logger.debug.assert_called()

    def test_unknown_repository_skipped_when_no_clone_path(self) -> None:
        # Unknown repo with no clone path on disk → skip entirely.
        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(
            repository_ids=['noclone'],
            task_summary='x',
        )
        workspace.repository_path.return_value = None
        repo = MagicMock()
        repo.get_repository.side_effect = ValueError('not in inventory')
        service = AgentService(**_kwargs(
            workspace_manager=workspace, repository_service=repo,
        ))
        service.logger = MagicMock()
        result = service._resolve_publish_context('T1')
        self.assertEqual(result, ([], '', None))

    def test_returns_empty_when_all_repos_unknown_and_no_clone_paths(self) -> None:
        # All unknown + no clone paths → empty result.
        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(
            repository_ids=['unknown1', 'unknown2'],
            task_summary='x',
        )
        workspace.repository_path.return_value = None
        repo = MagicMock()
        repo.get_repository.side_effect = ValueError('unknown')
        service = AgentService(**_kwargs(
            workspace_manager=workspace, repository_service=repo,
        ))
        service.logger = MagicMock()
        result = service._resolve_publish_context('T1')
        self.assertEqual(result, ([], '', None))


class StartTaskProcessingTests(unittest.TestCase):
    def test_returns_true_on_successful_move(self) -> None:
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
        from kato_core_lib.data_layers.data.task import Task
        publisher = MagicMock()
        service = AgentService(**_kwargs(task_publisher=publisher))
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        result = service._start_task_processing(Task(id='T1'), prepared)
        self.assertTrue(result)

    def test_returns_false_on_move_failure(self) -> None:
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
        from kato_core_lib.data_layers.data.task import Task
        task_state = MagicMock()
        task_state.move_task_to_in_progress.side_effect = RuntimeError('fail')
        handler = MagicMock()
        service = AgentService(**_kwargs(
            task_state_service=task_state,
            task_failure_handler=handler,
        ))
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        result = service._start_task_processing(Task(id='T1'), prepared)
        self.assertFalse(result)
        handler.handle_task_failure.assert_called_once()


class RunTaskImplementationTests(unittest.TestCase):
    def test_uses_planning_runner_when_wired(self) -> None:
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
        from kato_core_lib.data_layers.data.task import Task

        runner = MagicMock()
        runner.implement_task.return_value = {'success': True}
        service = AgentService(**_kwargs(planning_session_runner=runner))
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        execution = service._run_task_implementation(Task(id='T1'), prepared)
        self.assertEqual(execution['success'], True)
        runner.implement_task.assert_called_once()

    def test_returns_none_on_implementation_exception(self) -> None:
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
        from kato_core_lib.data_layers.data.task import Task

        impl = MagicMock()
        impl.implement_task.side_effect = RuntimeError('impl fail')
        handler = MagicMock()
        service = AgentService(**_kwargs(
            implementation_service=impl,
            task_failure_handler=handler,
        ))
        service.logger = MagicMock()
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        execution = service._run_task_implementation(Task(id='T1'), prepared)
        self.assertIsNone(execution)
        handler.handle_started_task_failure.assert_called_once()

    def test_returns_none_without_calling_failure_handler_when_user_stopped(self) -> None:
        # SessionStoppedByUserError means the user clicked Stop — the task
        # must NOT be moved back to Open (which would cause an immediate
        # re-spawn). Failure handler must not be called.
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
        from kato_core_lib.data_layers.data.task import Task
        from kato_core_lib.data_layers.service.planning_session_runner import SessionStoppedByUserError

        runner = MagicMock()
        runner.implement_task.side_effect = SessionStoppedByUserError('stopped by user')
        handler = MagicMock()
        service = AgentService(**_kwargs(
            planning_session_runner=runner,
            task_failure_handler=handler,
        ))
        service.logger = MagicMock()
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        execution = service._run_task_implementation(Task(id='T1'), prepared)
        self.assertIsNone(execution)
        handler.handle_started_task_failure.assert_not_called()

    def test_returns_none_when_implementation_failed(self) -> None:
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
        from kato_core_lib.data_layers.data.task import Task

        impl = MagicMock()
        impl.implement_task.return_value = {'success': False}
        handler = MagicMock()
        service = AgentService(**_kwargs(
            implementation_service=impl,
            task_failure_handler=handler,
        ))
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        execution = service._run_task_implementation(Task(id='T1'), prepared)
        self.assertIsNone(execution)
        handler.handle_implementation_failure.assert_called_once()


class RunTaskTestingValidationTests(unittest.TestCase):
    def test_skips_when_skip_testing_set(self) -> None:
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
        from kato_core_lib.data_layers.data.task import Task
        service = AgentService(**_kwargs(skip_testing=True))
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        ok, result, execution = service._run_task_testing_validation(
            Task(id='T1'), prepared, {'success': True},
        )
        self.assertTrue(ok)
        self.assertIsNone(result)

    def test_returns_false_when_publishability_fails(self) -> None:
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
        from kato_core_lib.data_layers.data.task import Task

        preflight = MagicMock()
        preflight.validate_task_branch_publishability.return_value = False
        service = AgentService(**_kwargs(task_preflight_service=preflight))
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        ok, result, _ = service._run_task_testing_validation(
            Task(id='T1'), prepared, {},
        )
        self.assertFalse(ok)

    def test_returns_false_when_testing_request_returns_none(self) -> None:
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
        from kato_core_lib.data_layers.data.task import Task

        preflight = MagicMock()
        preflight.validate_task_branch_publishability.return_value = True
        testing = MagicMock()
        testing.test_task.side_effect = RuntimeError('testing fail')
        handler = MagicMock()
        service = AgentService(**_kwargs(
            task_preflight_service=preflight,
            testing_service=testing,
            task_failure_handler=handler,
        ))
        service.logger = MagicMock()
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        ok, _, _ = service._run_task_testing_validation(
            Task(id='T1'), prepared, {},
        )
        self.assertFalse(ok)

    def test_returns_failure_when_testing_unsuccessful(self) -> None:
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
        from kato_core_lib.data_layers.data.task import Task

        preflight = MagicMock()
        preflight.validate_task_branch_publishability.return_value = True
        testing = MagicMock()
        testing.test_task.return_value = {'success': False}
        handler = MagicMock()
        service = AgentService(**_kwargs(
            task_preflight_service=preflight,
            testing_service=testing,
            task_failure_handler=handler,
        ))
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        ok, result, _ = service._run_task_testing_validation(
            Task(id='T1'), prepared, {'success': True},
        )
        self.assertFalse(ok)
        self.assertIsNotNone(result)
        handler.handle_testing_failure.assert_called_once()

    def test_returns_success_with_message_applied(self) -> None:
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
        from kato_core_lib.data_layers.data.task import Task

        preflight = MagicMock()
        preflight.validate_task_branch_publishability.return_value = True
        testing = MagicMock()
        testing.test_task.return_value = {'success': True, 'message': 'all good'}
        service = AgentService(**_kwargs(
            task_preflight_service=preflight,
            testing_service=testing,
        ))
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        ok, _, execution = service._run_task_testing_validation(
            Task(id='T1'), prepared, {'success': True},
        )
        self.assertTrue(ok)


class FinalEdgeCaseTests(unittest.TestCase):
    """Final close-out coverage for residual defensive lines."""

    def test_update_workspace_status_returns_early_for_unknown_status(
        self,
    ) -> None:
        # Line 440: status not in READY_FOR_REVIEW / PARTIAL_FAILURE → return.
        workspace = MagicMock()
        service = AgentService(**_kwargs(workspace_manager=workspace))
        service._update_workspace_status_after_publish(
            'T1', {'status': 'something_else'},
        )
        workspace.update_status.assert_not_called()

    def test_process_assigned_task_pauses_when_wait_before_push_tag_present(
        self,
    ) -> None:
        # Line 521: ``_pause_for_push_approval`` invoked.
        from kato_core_lib.data_layers.data.fields import TaskTags
        from kato_core_lib.data_layers.data.task import Task

        preflight = MagicMock()
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
        prepared = PreparedTaskContext(
            repositories=[], repository_branches={}, branch_name='b',
        )
        preflight.prepare_task_execution_context.return_value = prepared
        preflight.validate_task_branch_publishability.return_value = True
        impl = MagicMock()
        impl.implement_task.return_value = {'success': True}
        testing = MagicMock()
        testing.test_task.return_value = {'success': True}
        publisher = MagicMock()
        service = AgentService(**_kwargs(
            task_preflight_service=preflight,
            implementation_service=impl,
            testing_service=testing,
            task_publisher=publisher,
        ))
        result = service.process_assigned_task(
            Task(id='T1', tags=[TaskTags.WAIT_BEFORE_GIT_PUSH]),
        )
        self.assertEqual(result['status'], 'awaiting_push_approval')
        # publish_task_execution was NOT called — pause intercepted.
        publisher.publish_task_execution.assert_not_called()

    def test_sync_remote_comments_handles_inventory_lookup_failure(self) -> None:
        # Lines 886-887: inventory_repo lookup raises → set inventory_repo
        # to None. Subsequent listing branch then returns the
        # "platform listing unavailable" note (line 906 detects
        # inventory_repo is None).
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / '.git').mkdir()
            workspace = MagicMock()
            workspace.repository_path.return_value = Path(td)
            repo = MagicMock()
            repo.get_repository.side_effect = RuntimeError('unknown repo')
            repo.list_pull_request_comments = MagicMock()
            service = AgentService(**_kwargs(
                workspace_manager=workspace, repository_service=repo,
            ))
            store = MagicMock()
            with patch.object(service, '_comment_store_for', return_value=store):
                result = service.sync_remote_comments('T1', 'r1')
        # The "no inventory repo" path returns the platform-listing
        # note (line 906 sees inventory_repo is None).
        self.assertIn('platform listing unavailable', result.get('note', ''))

    def test_sync_remote_comments_records_exception_during_listing(self) -> None:
        # Lines 945-950: outer try/except wrapping the listing loop.
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / '.git').mkdir()
            workspace = MagicMock()
            workspace.repository_path.return_value = Path(td)
            repo = MagicMock()
            repo.list_pull_request_comments.side_effect = RuntimeError('fail')
            service = AgentService(**_kwargs(
                workspace_manager=workspace, repository_service=repo,
            ))
            service.logger = MagicMock()
            store = MagicMock()
            with patch.object(service, '_comment_store_for',
                              return_value=store), \
                 patch.object(service, '_task_pull_request_id',
                              return_value='pr-1'):
                result = service.sync_remote_comments('T1', 'r1')
        self.assertFalse(result['ok'])
        service.logger.exception.assert_called()

    def test_task_pull_request_id_skips_non_dict_contexts(self) -> None:
        # Line 1218: ``if not isinstance(context, dict): continue``.
        review = MagicMock()
        review.state_registry.list_pull_request_contexts.return_value = [
            'not a dict',  # skipped
            42,            # skipped
            {'task_id': 'T1', 'repository_id': 'r1', 'pull_request_id': '17'},
        ]
        service = AgentService(**_kwargs(review_comment_service=review))
        self.assertEqual(service._task_pull_request_id('T1', 'r1'), '17')

    def test_adopt_task_swallows_approval_service_exception(self) -> None:
        # Lines 1510-1518.
        task = SimpleNamespace(id='T1', summary='x', description='', tags=[])
        repo = MagicMock()
        repo.resolve_task_repositories.return_value = [
            SimpleNamespace(id='r1'),
        ]
        service = AgentService(**_kwargs(
            workspace_manager=MagicMock(), repository_service=repo,
        ))
        service.logger = MagicMock()
        with patch.object(service, '_lookup_assigned_or_review_task',
                          return_value=task), \
             patch(
                 'kato_core_lib.data_layers.service.repository_approval_service.'
                 'RepositoryApprovalService',
                 side_effect=RuntimeError('approval boom'),
             ), \
             patch(
                 'kato_core_lib.data_layers.service.workspace_provisioning_service.'
                 'provision_task_workspace_clones',
                 return_value=[SimpleNamespace(id='r1')],
             ):
            result = service.adopt_task('T1')
        self.assertTrue(result['adopted'])
        service.logger.exception.assert_called()

    def test_lookup_assigned_or_review_task_skips_non_callable_helper(
        self,
    ) -> None:
        # Line 1562: ``if not callable(fetch): continue``.
        # Build a task_service whose list_all_assigned_tasks attribute is
        # not callable.
        task_service = SimpleNamespace(
            list_all_assigned_tasks='not callable',
            get_assigned_tasks=lambda: [SimpleNamespace(id='T1')],
        )
        service = AgentService(**_kwargs(task_service=task_service))
        self.assertEqual(
            service._lookup_assigned_or_review_task('T1').id, 'T1',
        )

    def test_add_task_repository_swallows_repositories_property_exception(
        self,
    ) -> None:
        # Lines 1635-1636: ``except Exception: inventory_ids = set()``.
        repo = MagicMock()
        type(repo).repositories = property(
            lambda self: (_ for _ in ()).throw(RuntimeError('fail')),
        )
        service = AgentService(**_kwargs(repository_service=repo))
        result = service.add_task_repository('T1', 'r1')
        # Repository not in inventory (set is empty) → returns error.
        self.assertFalse(result['added'])

    def test_add_task_repository_extracts_tags_from_object_with_name_attr(
        self,
    ) -> None:
        # Lines 1661-1665 (the else branch — non-dict tag entry).
        from kato_core_lib.data_layers.data.fields import RepositoryFields
        repo = MagicMock()
        type(repo).repositories = property(
            lambda self: [SimpleNamespace(id='r1')],
        )
        task_service = MagicMock()
        service = AgentService(**_kwargs(
            repository_service=repo, task_service=task_service,
            workspace_manager=MagicMock(),
        ))
        # Tags are object instances with a .name attribute.
        existing = SimpleNamespace(
            id='T1',
            tags=[SimpleNamespace(
                name=f'{RepositoryFields.REPOSITORY_TAG_PREFIX}r1',
            )],
        )
        with patch.object(service, '_lookup_task_for_sync',
                          return_value=existing), \
             patch.object(service, 'sync_task_repositories',
                          return_value={'synced': True}):
            result = service.add_task_repository('T1', 'r1')
        # Already tagged via object-with-name path.
        self.assertFalse(result['tag_added'])

    def test_add_task_repository_extracts_tag_from_dict_entry(self) -> None:
        # Line 1661: dict tag entries are normalized via ``.get('name')``.
        from kato_core_lib.data_layers.data.fields import RepositoryFields
        repo = MagicMock()
        type(repo).repositories = property(
            lambda self: [SimpleNamespace(id='r1')],
        )
        task_service = MagicMock()
        service = AgentService(**_kwargs(
            repository_service=repo, task_service=task_service,
            workspace_manager=MagicMock(),
        ))
        # Tags are dicts (YouTrack-style).
        existing = SimpleNamespace(
            id='T1',
            tags=[{'name': f'{RepositoryFields.REPOSITORY_TAG_PREFIX}r1'}],
        )
        with patch.object(service, '_lookup_task_for_sync',
                          return_value=existing), \
             patch.object(service, 'sync_task_repositories',
                          return_value={'synced': True}):
            result = service.add_task_repository('T1', 'r1')
        # Already tagged via dict path.
        self.assertFalse(result['tag_added'])
        task_service.add_tag.assert_not_called()

    def test_add_task_repository_actually_adds_tag_when_not_present(self) -> None:
        # Line 1672: ``tag_added = True`` when add_tag fires.
        repo = MagicMock()
        type(repo).repositories = property(
            lambda self: [SimpleNamespace(id='r1')],
        )
        task_service = MagicMock()
        service = AgentService(**_kwargs(
            repository_service=repo, task_service=task_service,
            workspace_manager=MagicMock(),
        ))
        existing = SimpleNamespace(id='T1', tags=[])  # no tags yet
        with patch.object(service, '_lookup_task_for_sync',
                          return_value=existing), \
             patch.object(service, 'sync_task_repositories',
                          return_value={'synced': True}):
            result = service.add_task_repository('T1', 'r1')
        self.assertTrue(result['tag_added'])
        task_service.add_tag.assert_called_once()

    def test_lookup_task_for_sync_returns_none_when_not_in_any_queue(self) -> None:
        # Line 1832: loop completes without match → return None.
        task_service = MagicMock()
        task_service.get_assigned_tasks.return_value = []
        task_service.get_review_tasks.return_value = []
        service = AgentService(**_kwargs(task_service=task_service))
        self.assertIsNone(service._lookup_task_for_sync('T1'))

    def test_kick_lesson_extraction_uses_task_summary_when_get_task_succeeds(
        self,
    ) -> None:
        # Lines 2181-2183 + 2190: get_task returns a real task with
        # description set.
        task_service = MagicMock()
        task_service.get_task.return_value = SimpleNamespace(
            id='T1', summary='Fix things', description='Long body',
        )
        lessons = MagicMock()
        service = AgentService(**_kwargs(
            lessons_service=lessons, task_service=task_service,
        ))
        service._kick_lesson_extraction('T1', {}, {})
        # Worker thread fires async; give it a moment.
        import time
        time.sleep(0.05)
        # Extract was called (best-effort).
        lessons.extract_and_save.assert_called()


class AdvanceFinishedCommentRunsTests(unittest.TestCase):
    """advance_finished_comment_runs: scan-loop fallback that advances
    IN_PROGRESS comments when the SSE subscriber missed the RESULT event."""

    def _comment(self, comment_id, status):
        from kato_core_lib.comment_core_lib import KatoCommentStatus
        return SimpleNamespace(
            id=comment_id, kato_status=KatoCommentStatus(status).value,
        )

    def _fake_event(self, event_type, is_error=False, result=''):
        return SimpleNamespace(
            event_type=event_type,
            raw={'type': event_type, 'is_error': is_error, 'result': result},
        )

    def test_advances_when_alive_session_has_result_event(self) -> None:
        """Alive session with a RESULT → advance even though subprocess is up."""
        from kato_core_lib.comment_core_lib import KatoCommentStatus
        service = AgentService(**_kwargs())
        store = _FakeCommentStore([self._comment('c1', 'in_progress')])
        session = SimpleNamespace(
            is_alive=True,
            recent_events=lambda: [
                self._fake_event('system'),
                self._fake_event('assistant'),
                self._fake_event('result', is_error=False, result='Done.'),
            ],
        )
        mgr = MagicMock()
        mgr.get_session.return_value = session
        service._session_manager = mgr

        with patch.object(service, '_safe_list_workspaces',
                          return_value=[SimpleNamespace(task_id='T1')]), \
             patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_task_has_busy_turn', return_value=False), \
             patch.object(service, 'complete_in_progress_task_comments',
                          return_value=[{'task_id': 'T1', 'comment_id': 'c1',
                                         'kato_status': 'addressed'}]) as complete:
            results = service.advance_finished_comment_runs()

        complete.assert_called_once_with(
            'T1', success=True, result_text='Done.',
        )
        self.assertEqual(len(results), 1)

    def test_skips_alive_session_with_no_result_yet(self) -> None:
        """Alive session with only system events → not done yet; do nothing."""
        service = AgentService(**_kwargs())
        store = _FakeCommentStore([self._comment('c1', 'in_progress')])
        session = SimpleNamespace(
            is_alive=True,
            recent_events=lambda: [self._fake_event('system')],
        )
        mgr = MagicMock()
        mgr.get_session.return_value = session
        service._session_manager = mgr

        with patch.object(service, '_safe_list_workspaces',
                          return_value=[SimpleNamespace(task_id='T1')]), \
             patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_task_has_busy_turn', return_value=False), \
             patch.object(service, 'complete_in_progress_task_comments') as complete:
            results = service.advance_finished_comment_runs()

        complete.assert_not_called()
        self.assertEqual(results, [])

    def test_advances_with_is_error_true_from_alive_session(self) -> None:
        """Alive session RESULT is_error=True → advance with success=False."""
        service = AgentService(**_kwargs())
        store = _FakeCommentStore([self._comment('c1', 'in_progress')])
        session = SimpleNamespace(
            is_alive=True,
            recent_events=lambda: [
                self._fake_event('result', is_error=True),
            ],
        )
        mgr = MagicMock()
        mgr.get_session.return_value = session
        service._session_manager = mgr

        with patch.object(service, '_safe_list_workspaces',
                          return_value=[SimpleNamespace(task_id='T1')]), \
             patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_task_has_busy_turn', return_value=False), \
             patch.object(service, 'complete_in_progress_task_comments',
                          return_value=[]) as complete:
            service.advance_finished_comment_runs()

        complete.assert_called_once_with(
            'T1', success=False, result_text='',
        )

    def test_skips_mid_turn_sessions(self) -> None:
        """_task_has_busy_turn=True → never advance."""
        service = AgentService(**_kwargs())
        store = _FakeCommentStore([self._comment('c1', 'in_progress')])

        with patch.object(service, '_safe_list_workspaces',
                          return_value=[SimpleNamespace(task_id='T1')]), \
             patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_task_has_busy_turn', return_value=True), \
             patch.object(service, 'complete_in_progress_task_comments') as complete:
            results = service.advance_finished_comment_runs()

        complete.assert_not_called()
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# Coverage for remaining defensive branches in agent_service.py
# ---------------------------------------------------------------------------


class CompleteInProgressTaskCommentsDefensiveBranches(unittest.TestCase):
    """Lines 950-954, 1014-1015: defensive exception paths in
    ``complete_in_progress_task_comments``."""

    def test_store_list_exception_returns_empty(self) -> None:
        # Lines 950-954: when ``store.list()`` raises, log and return [].
        service = AgentService(**_kwargs())
        store = MagicMock()
        store.list.side_effect = RuntimeError('store boom')
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, 'logger', MagicMock()) as mock_logger:
            result = service.complete_in_progress_task_comments(
                'T1', success=True, result_text='ok',
            )
        self.assertEqual(result, [])
        mock_logger.exception.assert_called_once()


class AdvanceFinishedCommentRunsDefensiveBranches(unittest.TestCase):
    """Lines 1037, 1040, 1043-1044, 1050, 1058-1059, 1069-1070, 1081-1109:
    every branch + exception path in ``advance_finished_comment_runs``."""

    def _comment(self, comment_id, status):
        from kato_core_lib.comment_core_lib import KatoCommentStatus
        return SimpleNamespace(
            id=comment_id, kato_status=KatoCommentStatus(status).value,
        )

    def test_blank_task_id_skipped(self) -> None:
        # Line 1037: ``if not task_id: continue``.
        service = AgentService(**_kwargs())
        with patch.object(service, '_safe_list_workspaces',
                          return_value=[SimpleNamespace(task_id='   ')]):
            result = service.advance_finished_comment_runs()
        self.assertEqual(result, [])

    def test_no_comment_store_skipped(self) -> None:
        # Line 1040: ``store is None → continue``.
        service = AgentService(**_kwargs())
        with patch.object(service, '_safe_list_workspaces',
                          return_value=[SimpleNamespace(task_id='T1')]), \
             patch.object(service, '_comment_store_for', return_value=None):
            result = service.advance_finished_comment_runs()
        self.assertEqual(result, [])

    def test_store_list_exception_skipped(self) -> None:
        # Lines 1043-1044: ``store.list()`` exception → continue.
        service = AgentService(**_kwargs())
        store = MagicMock()
        store.list.side_effect = RuntimeError('store boom')
        with patch.object(service, '_safe_list_workspaces',
                          return_value=[SimpleNamespace(task_id='T1')]), \
             patch.object(service, '_comment_store_for', return_value=store):
            result = service.advance_finished_comment_runs()
        self.assertEqual(result, [])

    def test_no_in_progress_comments_skipped(self) -> None:
        # Line 1050: ``if not in_progress: continue``.
        service = AgentService(**_kwargs())
        store = _FakeCommentStore([self._comment('c1', 'queued')])
        with patch.object(service, '_safe_list_workspaces',
                          return_value=[SimpleNamespace(task_id='T1')]), \
             patch.object(service, '_comment_store_for', return_value=store):
            result = service.advance_finished_comment_runs()
        self.assertEqual(result, [])

    def test_get_session_exception_swallowed(self) -> None:
        # Lines 1058-1059: ``session_manager.get_session`` raises.
        # Session ends up None — fall through to terminal_event branch
        # (which is also None → no advance / no requeue).
        service = AgentService(**_kwargs())
        store = _FakeCommentStore([self._comment('c1', 'in_progress')])
        mgr = MagicMock()
        mgr.get_session.side_effect = RuntimeError('mgr down')
        service._session_manager = mgr
        with patch.object(service, '_safe_list_workspaces',
                          return_value=[SimpleNamespace(task_id='T1')]), \
             patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_task_has_busy_turn', return_value=False):
            # Must not raise even though get_session does.
            result = service.advance_finished_comment_runs()
        # No session → fall to terminal_event branch (None) → requeue path.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['action'], 'requeued')

    def test_recent_events_exception_treats_as_no_result_yet(self) -> None:
        # Lines 1069-1070: ``session.recent_events()`` raises → last_result
        # stays None → ``continue`` (no advance for this comment).
        service = AgentService(**_kwargs())
        store = _FakeCommentStore([self._comment('c1', 'in_progress')])
        session = SimpleNamespace(
            is_alive=True,
            recent_events=MagicMock(side_effect=RuntimeError('events failed')),
        )
        mgr = MagicMock()
        mgr.get_session.return_value = session
        service._session_manager = mgr
        with patch.object(service, '_safe_list_workspaces',
                          return_value=[SimpleNamespace(task_id='T1')]), \
             patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_task_has_busy_turn', return_value=False), \
             patch.object(service, 'complete_in_progress_task_comments') as complete:
            service.advance_finished_comment_runs()
        complete.assert_not_called()

    def test_dead_session_with_terminal_event_advances(self) -> None:
        # Lines 1081-1089: dead session with terminal event → advance.
        service = AgentService(**_kwargs())
        store = _FakeCommentStore([self._comment('c1', 'in_progress')])
        session = SimpleNamespace(
            is_alive=False,
            terminal_event=SimpleNamespace(
                raw={'is_error': False, 'result': 'finished'},
            ),
        )
        mgr = MagicMock()
        mgr.get_session.return_value = session
        service._session_manager = mgr
        with patch.object(service, '_safe_list_workspaces',
                          return_value=[SimpleNamespace(task_id='T1')]), \
             patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_task_has_busy_turn', return_value=False), \
             patch.object(service, 'complete_in_progress_task_comments',
                          return_value=[{'comment_id': 'c1'}]) as complete:
            results = service.advance_finished_comment_runs()
        complete.assert_called_once_with(
            'T1', success=True, result_text='finished',
        )
        self.assertEqual(len(results), 1)

    def test_session_gone_no_terminal_event_requeues_in_progress(self) -> None:
        # Lines 1091-1107: dead session, no terminal event → requeue all
        # in_progress comments.
        service = AgentService(**_kwargs())
        store = _FakeCommentStore([self._comment('c1', 'in_progress')])
        session = SimpleNamespace(
            is_alive=False,
            terminal_event=None,
        )
        mgr = MagicMock()
        mgr.get_session.return_value = session
        service._session_manager = mgr
        with patch.object(service, '_safe_list_workspaces',
                          return_value=[SimpleNamespace(task_id='T1')]), \
             patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_task_has_busy_turn', return_value=False):
            results = service.advance_finished_comment_runs()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['action'], 'requeued')

    def test_requeue_exception_logged_swallowed(self) -> None:
        # Lines 1108-1112: ``store.update_kato_status`` raises during
        # requeue — exception logged, loop continues.
        service = AgentService(**_kwargs())
        bad_store = MagicMock()
        bad_store.list.return_value = [self._comment('c1', 'in_progress')]
        bad_store.update_kato_status.side_effect = RuntimeError('update boom')
        session = SimpleNamespace(is_alive=False, terminal_event=None)
        mgr = MagicMock()
        mgr.get_session.return_value = session
        service._session_manager = mgr
        with patch.object(service, '_safe_list_workspaces',
                          return_value=[SimpleNamespace(task_id='T1')]), \
             patch.object(service, '_comment_store_for', return_value=bad_store), \
             patch.object(service, '_task_has_busy_turn', return_value=False), \
             patch.object(service, 'logger', MagicMock()) as mock_logger:
            results = service.advance_finished_comment_runs()
        # Exception logged, requeue skipped for this comment.
        mock_logger.exception.assert_called_once()
        self.assertEqual(results, [])


class CommentAgentCwdBranchTests(unittest.TestCase):
    """Lines 1633, 1638-1643: ``_comment_agent_cwd`` fallbacks when
    ``repository_path`` raises or no repo_id is set."""

    def test_returns_empty_when_no_workspace_manager(self) -> None:
        service = AgentService(**_kwargs())
        service._workspace_manager = None
        record = SimpleNamespace(repo_id='r')
        self.assertEqual(service._comment_agent_cwd('T1', record), '')

    def test_falls_back_to_workspace_path_when_repository_path_raises(self) -> None:
        # Line 1638-1639: repository_path raises → fall through.
        service = AgentService(**_kwargs())
        wm = MagicMock()
        wm.repository_path.side_effect = RuntimeError('repo not found')
        wm.workspace_path.return_value = '/work/T1'
        service._workspace_manager = wm
        record = SimpleNamespace(repo_id='r')
        self.assertEqual(service._comment_agent_cwd('T1', record), '/work/T1')

    def test_returns_empty_when_workspace_path_also_raises(self) -> None:
        # Line 1642-1643: even workspace_path raises → return ''.
        service = AgentService(**_kwargs())
        wm = MagicMock()
        wm.repository_path.side_effect = RuntimeError('no repo')
        wm.workspace_path.side_effect = RuntimeError('no workspace')
        service._workspace_manager = wm
        record = SimpleNamespace(repo_id='r')
        self.assertEqual(service._comment_agent_cwd('T1', record), '')

    def test_no_repo_id_falls_back_directly_to_workspace_path(self) -> None:
        # Lines 1633-1634: ``repo_id`` blank → skip the repo branch
        # and use workspace_path.
        service = AgentService(**_kwargs())
        wm = MagicMock()
        wm.workspace_path.return_value = '/work/T1'
        service._workspace_manager = wm
        record = SimpleNamespace(repo_id='   ')
        self.assertEqual(service._comment_agent_cwd('T1', record), '/work/T1')


class UpdateSourceWorkspaceHasChangesBranchTests(unittest.TestCase):
    """Lines 1803-1809, 1817-1821 in ``update_source_for_task``:
    workspace_has_task_changes exception (logged) + no-changes skip."""

    def test_workspace_has_changes_exception_treats_as_changed(self) -> None:
        # Lines 1803-1809: workspace_has_task_changes raises → fail-open
        # (treat as has_changes=True so we still try the update rather
        # than silently swallow real work).
        service = AgentService(**_kwargs())
        repo = SimpleNamespace(id='r1', local_path='/x')
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo], 'feat/x',
                                        SimpleNamespace(id='T1'))), \
             patch.object(service, 'push_task',
                          return_value={'pushed': True}), \
             patch.object(service._repository_service,
                          'build_branch_name', return_value='feat/x'), \
             patch.object(service._repository_service,
                          'workspace_has_task_changes',
                          side_effect=RuntimeError('precheck boom')), \
             patch.object(service._repository_service, 'get_repository',
                          side_effect=ValueError('unknown')), \
             patch.object(service, 'logger', MagicMock()) as mock_logger:
            result = service.update_source_for_task('T1')
        # Pre-check exception was logged.
        log_calls = [c[0][0] for c in mock_logger.exception.call_args_list]
        self.assertTrue(any(
            'workspace-has-changes pre-check failed' in msg
            for msg in log_calls
        ))
        # ValueError from get_repository ended up in skipped_repositories.
        self.assertEqual(len(result['skipped_repositories']), 1)

    def test_workspace_no_changes_skips_repo_with_reason(self) -> None:
        # Lines 1817-1821: ``has_changes=False`` → repo added to
        # skipped_repositories with reason 'no changes in workspace clone'.
        service = AgentService(**_kwargs())
        repo = SimpleNamespace(id='r1', local_path='/x')
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo], 'feat/x',
                                        SimpleNamespace(id='T1'))), \
             patch.object(service, 'push_task',
                          return_value={'pushed': True}), \
             patch.object(service._repository_service,
                          'build_branch_name', return_value='feat/x'), \
             patch.object(service._repository_service,
                          'workspace_has_task_changes', return_value=False):
            result = service.update_source_for_task('T1')
        skipped = result['skipped_repositories']
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]['reason'], 'no changes in workspace clone')


class AddCommentAgentReplyBranchTests(unittest.TestCase):
    """Lines 1014-1015 in ``_add_comment_agent_reply``: when
    ``store.add(...)`` raises (DB write failure, schema mismatch),
    the exception is logged but not propagated — Claude's reply was
    already delivered via SSE; failing to mirror it into the
    comment thread mustn't crash the post-result pipeline."""

    def test_empty_body_short_circuits(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        comment = SimpleNamespace(id='c1')
        service._add_comment_agent_reply(store, comment, '')
        store.add.assert_not_called()

    def test_store_add_exception_logged_swallowed(self) -> None:
        # Lines 1014-1015: try/except around ``store.add``.
        service = AgentService(**_kwargs())
        store = MagicMock()
        store.add.side_effect = RuntimeError('add failed')
        comment = SimpleNamespace(
            id='c1', repo_id='r', file_path='f.py', line=10,
        )
        with patch.object(service, 'logger', MagicMock()) as mock_logger:
            # Must NOT raise — exception is swallowed.
            service._add_comment_agent_reply(store, comment, 'reply text')
        mock_logger.exception.assert_called_once()
        msg = mock_logger.exception.call_args[0][0]
        self.assertIn('failed to add Claude reply', msg)


if __name__ == '__main__':
    unittest.main()
