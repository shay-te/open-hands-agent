"""Additional coverage for ``AgentService`` remote-sync + publish flow."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.service.agent_service import AgentService


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


class DrainAllQueuedTaskCommentsTests(unittest.TestCase):
    """Server-side, browser-independent queue drain (scan-loop driven)."""

    def test_drains_every_workspace_and_collects_started(self) -> None:
        service = AgentService(**_kwargs())
        workspaces = [
            SimpleNamespace(task_id='UNA-1'),
            SimpleNamespace(task_id='UNA-2'),
            SimpleNamespace(task_id='UNA-3'),
        ]
        outcomes = {
            'UNA-1': {'ok': True, 'started': True, 'comment_id': 'c1'},
            'UNA-2': {'ok': True, 'started': False, 'comment_id': ''},
            'UNA-3': {'ok': True, 'started': True, 'comment_id': 'c3'},
        }
        with patch.object(service, '_safe_list_workspaces',
                          return_value=workspaces), \
             patch.object(service, 'drain_next_queued_task_comment',
                          side_effect=lambda tid: outcomes[tid]) as drain:
            results = service.drain_all_queued_task_comments()

        self.assertEqual(
            [c.args[0] for c in drain.call_args_list],
            ['UNA-1', 'UNA-2', 'UNA-3'],
        )
        # Only the ones that actually STARTED a run are reported.
        self.assertEqual(
            results,
            [
                {'task_id': 'UNA-1', 'ok': True, 'started': True,
                 'comment_id': 'c1'},
                {'task_id': 'UNA-3', 'ok': True, 'started': True,
                 'comment_id': 'c3'},
            ],
        )

    def test_one_task_failing_does_not_abort_the_rest(self) -> None:
        service = AgentService(**_kwargs())
        workspaces = [
            SimpleNamespace(task_id='UNA-1'),
            SimpleNamespace(task_id='UNA-2'),
        ]

        def _drain(tid):
            if tid == 'UNA-1':
                raise RuntimeError('store exploded')
            return {'ok': True, 'started': True, 'comment_id': 'c2'}

        with patch.object(service, '_safe_list_workspaces',
                          return_value=workspaces), \
             patch.object(service, 'drain_next_queued_task_comment',
                          side_effect=_drain):
            results = service.drain_all_queued_task_comments()

        self.assertEqual(
            results,
            [{'task_id': 'UNA-2', 'ok': True, 'started': True,
              'comment_id': 'c2'}],
        )

    def test_blank_task_ids_and_no_workspaces_are_safe(self) -> None:
        service = AgentService(**_kwargs())
        with patch.object(service, '_safe_list_workspaces', return_value=[]):
            self.assertEqual(service.drain_all_queued_task_comments(), [])
        with patch.object(service, '_safe_list_workspaces',
                          return_value=[SimpleNamespace(task_id='')]), \
             patch.object(service, 'drain_next_queued_task_comment') as drain:
            self.assertEqual(service.drain_all_queued_task_comments(), [])
            drain.assert_not_called()


class _FakeCommentStore(object):
    """Minimal stand-in for LocalCommentStore (its own lib has full tests)."""

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
        # Mirrors LocalCommentStore.update_kato_status' real signature.
        self.updated.append((comment_id, kato_status))

    def add(self, record):
        self.added.append(record)
        return record


class RequeueStuckInProgressCommentsTests(unittest.TestCase):
    """Boot-time recovery: a comment orphaned IN_PROGRESS by a kato
    restart must go back to QUEUED so the scan-loop drain re-dispatches
    it and respawns the (lazily-resumed) chat session."""

    def _comment(self, comment_id, status):
        from kato_core_lib.comment_core_lib import KatoCommentStatus
        return SimpleNamespace(
            id=comment_id, kato_status=KatoCommentStatus(status).value,
        )

    def test_only_in_progress_is_requeued_other_states_untouched(self) -> None:
        service = AgentService(**_kwargs())
        store1 = _FakeCommentStore([
            self._comment('c1', 'in_progress'),
            self._comment('c2', 'queued'),
            self._comment('c3', 'addressed'),
        ])
        store2 = _FakeCommentStore([
            self._comment('c4', 'in_progress'),
            self._comment('c5', 'failed'),
            self._comment('c6', 'idle'),
        ])
        stores = {'UNA-1': store1, 'UNA-2': store2}
        with patch.object(service, '_safe_list_workspaces', return_value=[
            SimpleNamespace(task_id='UNA-1'),
            SimpleNamespace(task_id='UNA-2'),
        ]), patch.object(service, '_comment_store_for',
                         side_effect=lambda tid: stores[tid]):
            requeued = service.requeue_stuck_in_progress_comments()

        self.assertEqual(store1.updated, [('c1', 'queued')])
        self.assertEqual(store2.updated, [('c4', 'queued')])
        self.assertEqual(requeued, [
            {'task_id': 'UNA-1', 'comment_id': 'c1'},
            {'task_id': 'UNA-2', 'comment_id': 'c4'},
        ])

    def test_unreadable_store_does_not_abort_other_tasks(self) -> None:
        service = AgentService(**_kwargs())
        good = _FakeCommentStore([self._comment('c9', 'in_progress')])
        stores = {
            'UNA-1': _FakeCommentStore([], raise_on_list=True),
            'UNA-2': good,
        }
        with patch.object(service, '_safe_list_workspaces', return_value=[
            SimpleNamespace(task_id='UNA-1'),
            SimpleNamespace(task_id='UNA-2'),
        ]), patch.object(service, '_comment_store_for',
                         side_effect=lambda tid: stores[tid]):
            requeued = service.requeue_stuck_in_progress_comments()

        self.assertEqual(requeued, [{'task_id': 'UNA-2', 'comment_id': 'c9'}])

    def test_failed_status_update_is_skipped_not_fatal(self) -> None:
        service = AgentService(**_kwargs())
        store = _FakeCommentStore([
            self._comment('bad', 'in_progress'),
            self._comment('ok', 'in_progress'),
        ])

        def _update(comment_id, *, kato_status):
            if comment_id == 'bad':
                raise RuntimeError('disk full')
            store.updated.append((comment_id, kato_status))

        with patch.object(service, '_safe_list_workspaces', return_value=[
            SimpleNamespace(task_id='UNA-1'),
        ]), patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(store, 'update_kato_status', side_effect=_update):
            requeued = service.requeue_stuck_in_progress_comments()

        self.assertEqual(requeued, [{'task_id': 'UNA-1', 'comment_id': 'ok'}])

    def test_blank_task_ids_missing_store_and_no_workspaces_are_safe(self) -> None:
        service = AgentService(**_kwargs())
        with patch.object(service, '_safe_list_workspaces', return_value=[]):
            self.assertEqual(service.requeue_stuck_in_progress_comments(), [])
        with patch.object(service, '_safe_list_workspaces', return_value=[
            SimpleNamespace(task_id=''),
            SimpleNamespace(task_id='UNA-9'),
        ]), patch.object(service, '_comment_store_for',
                         return_value=None) as store_for:
            self.assertEqual(service.requeue_stuck_in_progress_comments(), [])
            store_for.assert_called_once_with('UNA-9')


class CompleteInProgressTaskCommentsTests(unittest.TestCase):
    """A finished agent turn must move the comment off "kato working"
    — ADDRESSED on success, FAILED on an errored turn — instead of
    leaving it IN_PROGRESS forever."""

    def _comment(self, comment_id, status):
        from kato_core_lib.comment_core_lib import KatoCommentStatus
        return SimpleNamespace(
            id=comment_id,
            repo_id='repo-1',
            file_path='src/file.py',
            line=12,
            kato_status=KatoCommentStatus(status).value,
        )

    def test_success_replies_then_marks_in_progress_addressed(self) -> None:
        service = AgentService(**_kwargs())
        store = _FakeCommentStore([
            self._comment('c1', 'in_progress'),
            self._comment('c2', 'queued'),      # untouched
            self._comment('c3', 'addressed'),   # untouched
        ])
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, 'mark_comment_addressed') as mark:
            out = service.complete_in_progress_task_comments(
                'T1', success=True, result_text='Fixed the issue.',
            )
        mark.assert_called_once_with('T1', 'c1', post_remote_reply=False)
        self.assertEqual(len(store.added), 1)
        self.assertEqual(store.added[0].parent_id, 'c1')
        self.assertEqual(store.added[0].body, 'Fixed the issue.')
        self.assertEqual(
            out, [{'task_id': 'T1', 'comment_id': 'c1',
                   'kato_status': 'addressed'}],
        )

    def test_errored_turn_marks_in_progress_failed(self) -> None:
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        service = AgentService(**_kwargs())
        store = _FakeCommentStore([self._comment('c1', 'in_progress')])
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, 'mark_comment_addressed') as mark:
            out = service.complete_in_progress_task_comments(
                'T1', success=False,
            )
        mark.assert_not_called()
        self.assertEqual(store.updated, [('c1', KatoCommentStatus.FAILED.value)])
        self.assertEqual(out[0]['kato_status'], KatoCommentStatus.FAILED.value)

    def test_no_in_progress_is_a_noop(self) -> None:
        service = AgentService(**_kwargs())
        store = _FakeCommentStore([self._comment('c1', 'queued')])
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, 'mark_comment_addressed') as mark:
            self.assertEqual(
                service.complete_in_progress_task_comments('T1', success=True),
                [],
            )
        mark.assert_not_called()

    def test_missing_store_and_per_comment_error_are_isolated(self) -> None:
        service = AgentService(**_kwargs())
        with patch.object(service, '_comment_store_for', return_value=None):
            self.assertEqual(
                service.complete_in_progress_task_comments('T1', success=True),
                [],
            )
        store = _FakeCommentStore([
            self._comment('bad', 'in_progress'),
            self._comment('ok', 'in_progress'),
        ])
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, 'mark_comment_addressed',
                          side_effect=[RuntimeError('boom'), None]):
            out = service.complete_in_progress_task_comments(
                'T1', success=True,
            )
        self.assertEqual(
            out, [{'task_id': 'T1', 'comment_id': 'ok',
                   'kato_status': 'addressed'}],
        )


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
    def test_drain_next_queued_returns_error_when_no_store(self) -> None:
        service = AgentService(**_kwargs())
        result = service.drain_next_queued_task_comment('T1')
        self.assertFalse(result['ok'])
        self.assertFalse(result['started'])

    def test_drain_next_queued_returns_idle_when_queue_empty(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        store.next_queued.return_value = None
        with patch.object(service, '_comment_store_for', return_value=store):
            result = service.drain_next_queued_task_comment('T1')
        self.assertTrue(result['ok'])
        self.assertFalse(result['started'])
        self.assertEqual(result['comment_id'], '')

    def test_drain_next_queued_triggers_oldest_comment(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        store.next_queued.return_value = SimpleNamespace(id='c1')
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(
                 service, '_maybe_trigger_comment_run', return_value=True,
             ) as trigger:
            result = service.drain_next_queued_task_comment('T1')
        self.assertTrue(result['ok'])
        self.assertTrue(result['started'])
        self.assertEqual(result['comment_id'], 'c1')
        trigger.assert_called_once_with('T1', 'c1')

    def test_returns_false_when_no_store(self) -> None:
        service = AgentService(**_kwargs())
        self.assertFalse(service._maybe_trigger_comment_run('T1', 'c1'))

    def test_returns_false_when_record_missing(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        store.get.return_value = None
        with patch.object(service, '_comment_store_for', return_value=store):
            self.assertFalse(service._maybe_trigger_comment_run('T1', 'c1'))

    def test_returns_false_when_turn_busy(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        store.get.return_value = MagicMock()
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_task_has_busy_turn', return_value=True):
            self.assertFalse(service._maybe_trigger_comment_run('T1', 'c1'))

    def test_swallows_run_comment_agent_exception(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        record = MagicMock(id='c1')
        store.get.return_value = record
        service.logger = MagicMock()
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_task_has_busy_turn', return_value=False), \
             patch.object(service, '_run_comment_agent',
                          side_effect=RuntimeError('agent fail')):
            result = service._maybe_trigger_comment_run('T1', 'c1')
        self.assertFalse(result)
        service.logger.exception.assert_called()

    def test_returns_true_on_successful_run(self) -> None:
        service = AgentService(**_kwargs())
        store = MagicMock()
        record = MagicMock(id='c1')
        store.get.return_value = record
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_task_has_busy_turn', return_value=False), \
             patch.object(service, '_run_comment_agent'):
            result = service._maybe_trigger_comment_run('T1', 'c1')
        self.assertTrue(result)

    def test_requeues_when_comment_run_cannot_start(self) -> None:
        from kato_core_lib.comment_core_lib import KatoCommentStatus

        service = AgentService(**_kwargs())
        store = MagicMock()
        record = MagicMock(id='c1')
        store.get.return_value = record
        with patch.object(service, '_comment_store_for', return_value=store), \
             patch.object(service, '_task_has_busy_turn', return_value=False), \
             patch.object(service, '_run_comment_agent', return_value=False):
            result = service._maybe_trigger_comment_run('T1', 'c1')
        self.assertFalse(result)
        self.assertEqual(
            store.update_kato_status.call_args_list[-1].kwargs['kato_status'],
            KatoCommentStatus.QUEUED.value,
        )


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
