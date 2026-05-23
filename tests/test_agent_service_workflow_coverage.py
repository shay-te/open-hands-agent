"""Coverage for ``AgentService`` workflow methods (push/pull/sync/adopt)."""

from __future__ import annotations

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


class ConfiguredDestinationBranchTests(unittest.TestCase):
    def test_returns_empty_for_blank_repository_id(self) -> None:
        service = AgentService(**_kwargs())
        self.assertEqual(service.configured_destination_branch(''), '')

    def test_returns_empty_when_repo_not_in_inventory(self) -> None:
        repo = MagicMock()
        repo.get_repository.side_effect = RuntimeError('unknown')
        service = AgentService(**_kwargs(repository_service=repo))
        self.assertEqual(service.configured_destination_branch('r1'), '')

    def test_returns_empty_when_destination_branch_raises(self) -> None:
        repo = MagicMock()
        repo.destination_branch.side_effect = ValueError('cannot infer')
        service = AgentService(**_kwargs(repository_service=repo))
        self.assertEqual(service.configured_destination_branch('r1'), '')

    def test_returns_destination_branch_on_success(self) -> None:
        repo = MagicMock()
        repo.destination_branch.return_value = 'main'
        service = AgentService(**_kwargs(repository_service=repo))
        self.assertEqual(service.configured_destination_branch('r1'), 'main')


class ListAllAssignedTasksTests(unittest.TestCase):
    def test_returns_empty_on_task_service_exception(self) -> None:
        task_service = MagicMock()
        task_service.list_all_assigned_tasks.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(task_service=task_service))
        service.logger = MagicMock()
        self.assertEqual(service.list_all_assigned_tasks(), [])
        service.logger.exception.assert_called()

    def test_returns_formatted_task_dicts(self) -> None:
        task_service = MagicMock()
        task_service.list_all_assigned_tasks.return_value = [
            SimpleNamespace(
                id='PROJ-1', summary='fix it already', state='Open',
                description='Long body ' + 'x' * 1000,
            ),
        ]
        service = AgentService(**_kwargs(task_service=task_service))
        result = service.list_all_assigned_tasks()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['id'], 'PROJ-1')
        # Description is truncated to 500 chars.
        self.assertLessEqual(len(result[0]['description']), 500)


class AdoptTaskTests(unittest.TestCase):
    def test_returns_error_for_blank_task_id(self) -> None:
        service = AgentService(**_kwargs())
        result = service.adopt_task('')
        self.assertFalse(result['adopted'])

    def test_returns_error_when_no_workspace_manager(self) -> None:
        service = AgentService(**_kwargs())
        result = service.adopt_task('T1')
        self.assertFalse(result['adopted'])
        self.assertIn('workspace manager', result['error'])

    def test_returns_error_when_task_not_found(self) -> None:
        service = AgentService(**_kwargs(workspace_manager=MagicMock()))
        with patch.object(service, '_lookup_assigned_or_review_task',
                          return_value=None):
            result = service.adopt_task('T1')
        self.assertFalse(result['adopted'])
        self.assertIn('not assigned', result['error'])

    def test_returns_error_when_resolve_repositories_fails(self) -> None:
        task = SimpleNamespace(id='T1', summary='x', description='', tags=[])
        repo = MagicMock()
        repo.resolve_task_repositories.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(
            workspace_manager=MagicMock(),
            repository_service=repo,
        ))
        with patch.object(service, '_lookup_assigned_or_review_task',
                          return_value=task):
            result = service.adopt_task('T1')
        self.assertFalse(result['adopted'])
        self.assertIn('failed to resolve', result['error'])

    def test_returns_unapproved_when_rep_refuses(self) -> None:
        task = SimpleNamespace(id='T1', summary='x', description='', tags=[])
        repo = MagicMock()
        repo.resolve_task_repositories.return_value = [
            SimpleNamespace(id='unapproved-repo'),
        ]
        service = AgentService(**_kwargs(
            workspace_manager=MagicMock(),
            repository_service=repo,
        ))
        with patch.object(service, '_lookup_assigned_or_review_task',
                          return_value=task), \
             patch(
                 'kato_core_lib.data_layers.service.repository_approval_service.'
                 'RepositoryApprovalService',
             ) as approval_cls:
            instance = approval_cls.return_value
            instance.is_approved.return_value = None  # not approved
            result = service.adopt_task('T1')
        self.assertFalse(result['adopted'])
        self.assertIn('unapproved-repo', result['unapproved_repositories'])

    def test_provisioning_failure_returns_error(self) -> None:
        task = SimpleNamespace(id='T1', summary='x', description='', tags=[])
        repo = MagicMock()
        repo.resolve_task_repositories.return_value = [
            SimpleNamespace(id='approved-repo'),
        ]
        service = AgentService(**_kwargs(
            workspace_manager=MagicMock(),
            repository_service=repo,
        ))
        with patch.object(service, '_lookup_assigned_or_review_task',
                          return_value=task), \
             patch(
                 'kato_core_lib.data_layers.service.repository_approval_service.'
                 'RepositoryApprovalService',
             ) as approval_cls, \
             patch(
                 'kato_core_lib.data_layers.service.workspace_provisioning_service.'
                 'provision_task_workspace_clones',
                 side_effect=RuntimeError('provisioning fail'),
             ):
            approval_cls.return_value.is_approved.return_value = 'restricted'
            service.logger = MagicMock()
            result = service.adopt_task('T1')
        self.assertFalse(result['adopted'])
        self.assertIn('provisioning', result['error'])

    def test_successful_adoption(self) -> None:
        task = SimpleNamespace(id='T1', summary='x', description='', tags=[])
        repo = MagicMock()
        repo.resolve_task_repositories.return_value = [
            SimpleNamespace(id='approved-repo'),
        ]
        service = AgentService(**_kwargs(
            workspace_manager=MagicMock(),
            repository_service=repo,
        ))
        with patch.object(service, '_lookup_assigned_or_review_task',
                          return_value=task), \
             patch(
                 'kato_core_lib.data_layers.service.repository_approval_service.'
                 'RepositoryApprovalService',
             ) as approval_cls, \
             patch(
                 'kato_core_lib.data_layers.service.workspace_provisioning_service.'
                 'provision_task_workspace_clones',
                 return_value=[SimpleNamespace(id='approved-repo')],
             ):
            approval_cls.return_value.is_approved.return_value = 'trusted'
            result = service.adopt_task('T1')
        self.assertTrue(result['adopted'])
        self.assertEqual(result['cloned_repositories'], ['approved-repo'])


class LookupAssignedOrReviewTaskTests(unittest.TestCase):
    def test_returns_first_match_across_queues(self) -> None:
        task = SimpleNamespace(id='T1')
        task_service = MagicMock()
        # Configure list_all_assigned_tasks first as that's the priority.
        task_service.list_all_assigned_tasks.return_value = [task]
        service = AgentService(**_kwargs(task_service=task_service))
        self.assertIs(service._lookup_assigned_or_review_task('T1'), task)

    def test_swallows_per_queue_exception(self) -> None:
        # When one queue raises, fall through to the next.
        task = SimpleNamespace(id='T1')
        task_service = MagicMock()
        task_service.list_all_assigned_tasks.side_effect = RuntimeError('fail')
        task_service.get_assigned_tasks.return_value = [task]
        service = AgentService(**_kwargs(task_service=task_service))
        self.assertIs(service._lookup_assigned_or_review_task('T1'), task)

    def test_returns_none_when_no_match(self) -> None:
        task_service = MagicMock()
        task_service.list_all_assigned_tasks.return_value = []
        task_service.get_assigned_tasks.return_value = []
        task_service.get_review_tasks.return_value = []
        service = AgentService(**_kwargs(task_service=task_service))
        self.assertIsNone(service._lookup_assigned_or_review_task('T1'))


class ListInventoryRepositoriesTests(unittest.TestCase):
    def test_returns_empty_on_exception(self) -> None:
        repo = MagicMock()
        # Configure the .repositories property to raise.
        type(repo).repositories = property(
            lambda self: (_ for _ in ()).throw(RuntimeError('fail')),
        )
        service = AgentService(**_kwargs(repository_service=repo))
        service.logger = MagicMock()
        self.assertEqual(service.list_inventory_repositories(), [])

    def test_returns_inventory_dicts(self) -> None:
        repo = MagicMock()
        type(repo).repositories = property(
            lambda self: [
                SimpleNamespace(id='r1', owner='o', repo_slug='s', local_path='/x'),
            ],
        )
        service = AgentService(**_kwargs(repository_service=repo))
        result = service.list_inventory_repositories()
        self.assertEqual(result[0]['id'], 'r1')


class AddTaskRepositoryTests(unittest.TestCase):
    def test_returns_error_for_blank_inputs(self) -> None:
        service = AgentService(**_kwargs())
        self.assertFalse(service.add_task_repository('', 'r1')['added'])
        self.assertFalse(service.add_task_repository('T1', '')['added'])

    def test_returns_error_for_unknown_repository_id(self) -> None:
        repo = MagicMock()
        type(repo).repositories = property(
            lambda self: [SimpleNamespace(id='r1')],
        )
        service = AgentService(**_kwargs(repository_service=repo))
        result = service.add_task_repository('T1', 'unknown')
        self.assertFalse(result['added'])
        self.assertIn('not in the kato', result['error'])

    def test_returns_error_when_tag_fails(self) -> None:
        repo = MagicMock()
        type(repo).repositories = property(
            lambda self: [SimpleNamespace(id='r1')],
        )
        task_service = MagicMock()
        task_service.add_tag.side_effect = RuntimeError('platform fail')
        service = AgentService(**_kwargs(
            repository_service=repo, task_service=task_service,
        ))
        service.logger = MagicMock()
        with patch.object(service, '_lookup_task_for_sync',
                          return_value=SimpleNamespace(id='T1', tags=[])):
            result = service.add_task_repository('T1', 'r1')
        self.assertFalse(result['added'])

    def test_skips_tag_when_already_tagged(self) -> None:
        # The "already tagged" path doesn't call add_tag.
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
        existing = SimpleNamespace(
            id='T1',
            tags=[f'{RepositoryFields.REPOSITORY_TAG_PREFIX}r1'],
        )
        with patch.object(service, '_lookup_task_for_sync',
                          return_value=existing), \
             patch.object(service, 'sync_task_repositories',
                          return_value={'synced': True}):
            result = service.add_task_repository('T1', 'r1')
        task_service.add_tag.assert_not_called()
        self.assertFalse(result['tag_added'])


class SyncTaskRepositoriesTests(unittest.TestCase):
    def test_returns_error_for_blank_task_id(self) -> None:
        service = AgentService(**_kwargs())
        self.assertFalse(service.sync_task_repositories('')['synced'])

    def test_returns_error_when_no_workspace_manager(self) -> None:
        service = AgentService(**_kwargs())
        result = service.sync_task_repositories('T1')
        self.assertFalse(result['synced'])
        self.assertIn('workspace manager', result['error'])

    def test_returns_error_when_workspace_missing(self) -> None:
        workspace = MagicMock()
        workspace.get.return_value = None
        service = AgentService(**_kwargs(workspace_manager=workspace))
        result = service.sync_task_repositories('T1')
        self.assertIn('no workspace exists', result['error'])

    def test_returns_error_when_task_lookup_fails(self) -> None:
        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(repository_ids=[])
        service = AgentService(**_kwargs(workspace_manager=workspace))
        with patch.object(service, '_lookup_task_for_sync', return_value=None):
            result = service.sync_task_repositories('T1')
        self.assertIn('could not load task', result['error'])

    def test_returns_error_when_resolve_fails(self) -> None:
        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(repository_ids=[])
        task = SimpleNamespace(id='T1', tags=[], description='')
        repo = MagicMock()
        repo.resolve_task_repositories.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(
            workspace_manager=workspace, repository_service=repo,
        ))
        with patch.object(service, '_lookup_task_for_sync', return_value=task):
            result = service.sync_task_repositories('T1')
        self.assertIn('failed to resolve', result['error'])

    def test_returns_already_synced_when_no_missing(self) -> None:
        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(repository_ids=['r1'])
        task = SimpleNamespace(id='T1', tags=[], description='')
        repo = MagicMock()
        repo.resolve_task_repositories.return_value = [
            SimpleNamespace(id='r1'),
        ]
        service = AgentService(**_kwargs(
            workspace_manager=workspace, repository_service=repo,
        ))
        with patch.object(service, '_lookup_task_for_sync', return_value=task):
            result = service.sync_task_repositories('T1')
        self.assertTrue(result['synced'])
        self.assertEqual(result['already_present'], ['r1'])

    def test_provisioning_failure_returns_failed_list(self) -> None:
        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(repository_ids=[])
        task = SimpleNamespace(id='T1', tags=[], description='')
        repo = MagicMock()
        repo.resolve_task_repositories.return_value = [
            SimpleNamespace(id='new-repo'),
        ]
        service = AgentService(**_kwargs(
            workspace_manager=workspace, repository_service=repo,
        ))
        service.logger = MagicMock()
        with patch.object(service, '_lookup_task_for_sync', return_value=task), \
             patch(
                 'kato_core_lib.data_layers.service.workspace_provisioning_service.'
                 'provision_task_workspace_clones',
                 side_effect=RuntimeError('clone fail'),
             ):
            result = service.sync_task_repositories('T1')
        self.assertFalse(result['synced'])
        self.assertEqual(len(result['failed_repositories']), 1)

    def test_success_path_adds_missing_repositories(self) -> None:
        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(repository_ids=[])
        task = SimpleNamespace(id='T1', tags=[], description='')
        repo = MagicMock()
        repo.resolve_task_repositories.return_value = [
            SimpleNamespace(id='new-repo'),
        ]
        service = AgentService(**_kwargs(
            workspace_manager=workspace, repository_service=repo,
        ))
        with patch.object(service, '_lookup_task_for_sync', return_value=task), \
             patch(
                 'kato_core_lib.data_layers.service.workspace_provisioning_service.'
                 'provision_task_workspace_clones',
                 return_value=[SimpleNamespace(id='new-repo')],
             ):
            result = service.sync_task_repositories('T1')
        self.assertTrue(result['synced'])
        self.assertEqual(result['added_repositories'], ['new-repo'])
        # No live session manager wired → conservative False.
        self.assertFalse(result['requires_session_restart'])


class _RecordingSessionManager(object):
    """Concrete stand-in: maps task_id → session-or-None.

    Replaces a MagicMock so the production code paths (``getattr``
    callable checks) hit a real object whose attributes mean exactly
    what they look like.
    """

    def __init__(self, sessions=None) -> None:
        self._sessions = dict(sessions or {})
        self.lookups: list[str] = []

    def get_session(self, task_id):
        self.lookups.append(task_id)
        return self._sessions.get(task_id)


class _FakeLiveSession(object):
    """Concrete stand-in for ``StreamingClaudeSession``.

    Exposes only what ``_sync_requires_session_restart`` reads:
    ``is_alive``, ``cwd``, ``allowed_additional_dirs()``.
    """

    def __init__(self, *, alive=True, cwd='', dirs=()) -> None:
        self.is_alive = alive
        self.cwd = cwd
        self._dirs = tuple(dirs)
        self.dirs_calls = 0

    def allowed_additional_dirs(self):
        self.dirs_calls += 1
        return self._dirs


class _BoomDirsSession(_FakeLiveSession):
    """Live session whose ``allowed_additional_dirs`` raises.

    Models a broken accessor / future refactor regression — the
    helper must NOT propagate; it returns False (conservative).
    """

    def allowed_additional_dirs(self):
        self.dirs_calls += 1
        raise RuntimeError('accessor blew up')


class SyncRequiresSessionRestartTests(unittest.TestCase):
    """Backend signal for "live Claude subprocess can't see the new repos".

    The Claude CLI bakes ``--add-dir`` into the subprocess at spawn
    time; new clones on disk are invisible to a session that started
    earlier. ``sync_task_repositories`` returns
    ``requires_session_restart=True`` so the UI can prompt the
    operator to restart the chat tab. NO MagicMock on the inputs to
    ``_sync_requires_session_restart`` — every stand-in here is a
    concrete class with explicit attributes.
    """

    def _service_with(self, session_manager=None):
        return AgentService(**_kwargs(
            session_manager=session_manager,
            workspace_manager=MagicMock(),
        ))

    def test_no_session_manager_returns_false(self) -> None:
        service = self._service_with(session_manager=None)
        self.assertFalse(service._sync_requires_session_restart(
            'T1', provisioned=[SimpleNamespace(id='r1', local_path='/x/r1')],
            missing_repos=[SimpleNamespace(id='r1')],
        ))

    def test_empty_provisioned_returns_false(self) -> None:
        # Nothing was actually added → no need to restart anything.
        mgr = _RecordingSessionManager({
            'T1': _FakeLiveSession(dirs=('/y',)),
        })
        service = self._service_with(session_manager=mgr)
        self.assertFalse(service._sync_requires_session_restart(
            'T1', provisioned=[], missing_repos=[SimpleNamespace(id='r1')],
        ))
        # Provisioned-empty short-circuits BEFORE looking up the session.
        self.assertEqual(mgr.lookups, [])

    def test_no_live_session_for_task_returns_false(self) -> None:
        mgr = _RecordingSessionManager()  # empty registry
        service = self._service_with(session_manager=mgr)
        self.assertFalse(service._sync_requires_session_restart(
            'T1', provisioned=[SimpleNamespace(id='r1', local_path='/x/r1')],
            missing_repos=[SimpleNamespace(id='r1')],
        ))
        self.assertEqual(mgr.lookups, ['T1'])

    def test_dead_session_returns_false(self) -> None:
        # Dead subprocess → operator's next message will spawn a
        # fresh one with the full repo set; nothing to warn about.
        mgr = _RecordingSessionManager({
            'T1': _FakeLiveSession(alive=False, dirs=()),
        })
        service = self._service_with(session_manager=mgr)
        self.assertFalse(service._sync_requires_session_restart(
            'T1', provisioned=[SimpleNamespace(id='r1', local_path='/x/r1')],
            missing_repos=[SimpleNamespace(id='r1')],
        ))

    def test_session_without_accessor_returns_false(self) -> None:
        # Older session class without ``allowed_additional_dirs`` —
        # conservative: don't claim a restart is required because we
        # can't actually tell.
        class _OldSession(object):
            is_alive = True
            cwd = '/x'
            # no allowed_additional_dirs method at all.
        mgr = _RecordingSessionManager({'T1': _OldSession()})
        service = self._service_with(session_manager=mgr)
        self.assertFalse(service._sync_requires_session_restart(
            'T1', provisioned=[SimpleNamespace(id='r1', local_path='/x/r1')],
            missing_repos=[SimpleNamespace(id='r1')],
        ))

    def test_accessor_exception_returns_false(self) -> None:
        mgr = _RecordingSessionManager({'T1': _BoomDirsSession(dirs=())})
        service = self._service_with(session_manager=mgr)
        self.assertFalse(service._sync_requires_session_restart(
            'T1', provisioned=[SimpleNamespace(id='r1', local_path='/x/r1')],
            missing_repos=[SimpleNamespace(id='r1')],
        ))

    def test_new_repo_outside_sandbox_returns_true(self) -> None:
        # The core happy-bug-path: a freshly-cloned repo at
        # /x/workspaces/T1/new is NOT in the live session's allowed
        # set. Operator MUST restart the tab.
        session = _FakeLiveSession(
            cwd='/x/workspaces/T1/client',
            dirs=('/x/workspaces/T1/backend',),
        )
        mgr = _RecordingSessionManager({'T1': session})
        service = self._service_with(session_manager=mgr)
        self.assertTrue(service._sync_requires_session_restart(
            'T1',
            provisioned=[
                SimpleNamespace(id='client', local_path='/x/workspaces/T1/client'),
                SimpleNamespace(id='backend', local_path='/x/workspaces/T1/backend'),
                SimpleNamespace(id='new', local_path='/x/workspaces/T1/new'),
            ],
            missing_repos=[SimpleNamespace(id='new')],
        ))

    def test_new_repo_already_inside_sandbox_returns_false(self) -> None:
        # Unusual but legal: the operator's "Sync" resulted in a path
        # that happened to already be in the session's sandbox (e.g.
        # the spawn passed it pre-emptively). No restart needed.
        session = _FakeLiveSession(
            cwd='/x/workspaces/T1/client',
            dirs=(
                '/x/workspaces/T1/backend',
                '/x/workspaces/T1/new',
            ),
        )
        mgr = _RecordingSessionManager({'T1': session})
        service = self._service_with(session_manager=mgr)
        self.assertFalse(service._sync_requires_session_restart(
            'T1',
            provisioned=[
                SimpleNamespace(id='new', local_path='/x/workspaces/T1/new'),
            ],
            missing_repos=[SimpleNamespace(id='new')],
        ))

    def test_cwd_counts_as_sandbox_member(self) -> None:
        # When the freshly-cloned repo path matches the session's
        # cwd (rare but theoretically possible — operator
        # re-syncs the cwd repo), no restart required.
        session = _FakeLiveSession(
            cwd='/x/workspaces/T1/client',
            dirs=(),
        )
        mgr = _RecordingSessionManager({'T1': session})
        service = self._service_with(session_manager=mgr)
        self.assertFalse(service._sync_requires_session_restart(
            'T1',
            provisioned=[
                SimpleNamespace(id='client', local_path='/x/workspaces/T1/client'),
            ],
            missing_repos=[SimpleNamespace(id='client')],
        ))

    def test_repo_not_in_missing_set_is_ignored(self) -> None:
        # ``provisioned`` includes ALL task repos (the already-cloned
        # ones plus the newly-cloned ones). Only the NEWLY-added ones
        # should drive the signal — pre-existing repos that already
        # live outside the sandbox would be a different bug.
        session = _FakeLiveSession(
            cwd='/x/workspaces/T1/client',
            dirs=(),
        )
        mgr = _RecordingSessionManager({'T1': session})
        service = self._service_with(session_manager=mgr)
        # Backend is in provisioned but NOT in missing → ignored.
        self.assertFalse(service._sync_requires_session_restart(
            'T1',
            provisioned=[
                SimpleNamespace(id='client', local_path='/x/workspaces/T1/client'),
                SimpleNamespace(id='backend', local_path='/x/workspaces/T1/backend'),
            ],
            missing_repos=[],  # nothing newly added
        ))

    def test_blank_local_path_is_skipped(self) -> None:
        # Repo entry with no local_path can't be compared → skip it
        # rather than blast a false "restart required" signal.
        session = _FakeLiveSession(cwd='/x/cwd', dirs=())
        mgr = _RecordingSessionManager({'T1': session})
        service = self._service_with(session_manager=mgr)
        self.assertFalse(service._sync_requires_session_restart(
            'T1',
            provisioned=[SimpleNamespace(id='new', local_path='')],
            missing_repos=[SimpleNamespace(id='new')],
        ))

    def test_trailing_slash_path_normalised(self) -> None:
        # Sandbox path has trailing slash; new repo path doesn't.
        # Path normalization must make these compare equal.
        session = _FakeLiveSession(
            cwd='/x/workspaces/T1/client',
            dirs=('/x/workspaces/T1/new/',),
        )
        mgr = _RecordingSessionManager({'T1': session})
        service = self._service_with(session_manager=mgr)
        self.assertFalse(service._sync_requires_session_restart(
            'T1',
            provisioned=[
                SimpleNamespace(id='new', local_path='/x/workspaces/T1/new'),
            ],
            missing_repos=[SimpleNamespace(id='new')],
        ))


class SyncTaskRepositoriesRestartIntegrationTests(unittest.TestCase):
    """End-to-end through ``sync_task_repositories``: signal lands in payload.

    Exercises the full path: live session + provisioning + the new
    field appearing in the response shape the UI will read.
    """

    def test_payload_includes_requires_session_restart_true_when_live_session_is_stale(self) -> None:
        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(repository_ids=['existing'])
        task = SimpleNamespace(id='T1', tags=[], description='')
        repo = MagicMock()
        repo.resolve_task_repositories.return_value = [
            SimpleNamespace(id='existing'),
            SimpleNamespace(id='added'),
        ]
        session = _FakeLiveSession(
            cwd='/x/workspaces/T1/existing',
            dirs=(),  # session was spawned with no extra dirs
        )
        mgr = _RecordingSessionManager({'T1': session})
        service = AgentService(**_kwargs(
            workspace_manager=workspace, repository_service=repo,
            session_manager=mgr,
        ))
        with patch.object(service, '_lookup_task_for_sync', return_value=task), \
             patch(
                 'kato_core_lib.data_layers.service.workspace_provisioning_service.'
                 'provision_task_workspace_clones',
                 return_value=[
                     SimpleNamespace(id='existing', local_path='/x/workspaces/T1/existing'),
                     SimpleNamespace(id='added', local_path='/x/workspaces/T1/added'),
                 ],
             ):
            result = service.sync_task_repositories('T1')
        self.assertTrue(result['synced'])
        self.assertEqual(result['added_repositories'], ['added'])
        self.assertTrue(result['requires_session_restart'])

    def test_payload_requires_session_restart_false_when_no_live_session(self) -> None:
        workspace = MagicMock()
        workspace.get.return_value = SimpleNamespace(repository_ids=[])
        task = SimpleNamespace(id='T1', tags=[], description='')
        repo = MagicMock()
        repo.resolve_task_repositories.return_value = [
            SimpleNamespace(id='added'),
        ]
        service = AgentService(**_kwargs(
            workspace_manager=workspace, repository_service=repo,
            session_manager=_RecordingSessionManager(),  # no session for T1
        ))
        with patch.object(service, '_lookup_task_for_sync', return_value=task), \
             patch(
                 'kato_core_lib.data_layers.service.workspace_provisioning_service.'
                 'provision_task_workspace_clones',
                 return_value=[
                     SimpleNamespace(id='added', local_path='/x/workspaces/T1/added'),
                 ],
             ):
            result = service.sync_task_repositories('T1')
        self.assertTrue(result['synced'])
        self.assertFalse(result['requires_session_restart'])


class LookupTaskForSyncTests(unittest.TestCase):
    def test_returns_none_on_task_service_exception(self) -> None:
        task_service = MagicMock()
        task_service.get_assigned_tasks.side_effect = RuntimeError('fail')
        service = AgentService(**_kwargs(task_service=task_service))
        service.logger = MagicMock()
        self.assertIsNone(service._lookup_task_for_sync('T1'))

    def test_returns_match_from_review_queue(self) -> None:
        task = SimpleNamespace(id='T1')
        task_service = MagicMock()
        task_service.get_assigned_tasks.return_value = []
        task_service.get_review_tasks.return_value = [task]
        service = AgentService(**_kwargs(task_service=task_service))
        self.assertIs(service._lookup_task_for_sync('T1'), task)


class PushTaskTests(unittest.TestCase):
    def test_returns_error_for_blank_task_id(self) -> None:
        service = AgentService(**_kwargs())
        self.assertFalse(service.push_task('')['pushed'])

    def test_returns_error_when_no_workspace_context(self) -> None:
        service = AgentService(**_kwargs())
        with patch.object(service, '_resolve_publish_context',
                          return_value=([], '', None)):
            result = service.push_task('T1')
        self.assertFalse(result['pushed'])

    def test_skips_repository_when_no_push_needed(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'feat/x'
        repo.branch_needs_push.return_value = False
        service = AgentService(**_kwargs(repository_service=repo))
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'feat/x',
                                        SimpleNamespace(id='T1'))):
            result = service.push_task('T1')
        self.assertFalse(result['pushed'])
        self.assertEqual(len(result['skipped_repositories']), 1)

    def test_pushes_repository_when_needed(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'feat/x'
        repo.branch_needs_push.return_value = True
        service = AgentService(**_kwargs(repository_service=repo))
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'feat/x',
                                        SimpleNamespace(id='T1'))):
            result = service.push_task('T1')
        self.assertTrue(result['pushed'])
        self.assertEqual(result['pushed_repositories'], ['r1'])

    def test_swallows_branch_needs_push_exception(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'feat/x'
        repo.branch_needs_push.side_effect = RuntimeError('git fail')
        service = AgentService(**_kwargs(repository_service=repo))
        service.logger = MagicMock()
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'feat/x',
                                        SimpleNamespace(id='T1'))):
            result = service.push_task('T1')
        # Pre-check defaults to "no push needed" on error.
        self.assertFalse(result['pushed'])
        service.logger.exception.assert_called()

    def test_handles_repository_has_no_changes_error(self) -> None:
        from kato_core_lib.data_layers.service.repository_service import (
            RepositoryHasNoChangesError,
        )
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'feat/x'
        repo.branch_needs_push.return_value = True
        repo.publish_review_fix.side_effect = RepositoryHasNoChangesError(
            'race condition',
        )
        service = AgentService(**_kwargs(repository_service=repo))
        service.logger = MagicMock()
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'feat/x',
                                        SimpleNamespace(id='T1'))):
            result = service.push_task('T1')
        self.assertEqual(len(result['failed_repositories']), 1)

    def test_swallows_generic_publish_exception(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'feat/x'
        repo.branch_needs_push.return_value = True
        repo.publish_review_fix.side_effect = RuntimeError('git error')
        service = AgentService(**_kwargs(repository_service=repo))
        service.logger = MagicMock()
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'feat/x',
                                        SimpleNamespace(id='T1'))):
            result = service.push_task('T1')
        self.assertEqual(len(result['failed_repositories']), 1)


class PullTaskTests(unittest.TestCase):
    def test_returns_error_for_blank_task_id(self) -> None:
        service = AgentService(**_kwargs())
        self.assertFalse(service.pull_task('')['pulled'])

    def test_returns_error_when_no_workspace_context(self) -> None:
        service = AgentService(**_kwargs())
        with patch.object(service, '_resolve_publish_context',
                          return_value=([], '', None)):
            result = service.pull_task('T1')
        self.assertFalse(result['pulled'])

    def test_records_successful_pull(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'feat/x'
        repo.pull_workspace_clone.return_value = {
            'pulled': True, 'updated': True, 'commits_pulled': 3,
        }
        service = AgentService(**_kwargs(repository_service=repo))
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'feat/x',
                                        SimpleNamespace(id='T1'))):
            result = service.pull_task('T1')
        self.assertTrue(result['pulled'])
        self.assertEqual(result['pulled_repositories'][0]['commits_pulled'], 3)

    def test_records_already_in_sync(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'feat/x'
        repo.pull_workspace_clone.return_value = {
            'pulled': True, 'updated': False, 'commits_pulled': 0,
        }
        service = AgentService(**_kwargs(repository_service=repo))
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'feat/x',
                                        SimpleNamespace(id='T1'))):
            result = service.pull_task('T1')
        self.assertFalse(result['pulled'])
        self.assertEqual(len(result['skipped_repositories']), 1)

    def test_records_failed_pull(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'feat/x'
        repo.pull_workspace_clone.return_value = {
            'pulled': False, 'reason': 'dirty_working_tree',
            'detail': 'commit first',
        }
        service = AgentService(**_kwargs(repository_service=repo))
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'feat/x',
                                        SimpleNamespace(id='T1'))):
            result = service.pull_task('T1')
        self.assertEqual(len(result['skipped_repositories']), 1)

    def test_swallows_pull_workspace_exception(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'feat/x'
        repo.pull_workspace_clone.side_effect = RuntimeError('git fail')
        service = AgentService(**_kwargs(repository_service=repo))
        service.logger = MagicMock()
        with patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'feat/x',
                                        SimpleNamespace(id='T1'))):
            result = service.pull_task('T1')
        self.assertEqual(len(result['failed_repositories']), 1)


class UpdateSourceForTaskTests(unittest.TestCase):
    def test_returns_error_for_blank_task_id(self) -> None:
        service = AgentService(**_kwargs())
        self.assertFalse(service.update_source_for_task('')['updated'])

    def test_returns_error_when_no_workspace_context(self) -> None:
        service = AgentService(**_kwargs())
        with patch.object(service, 'push_task', return_value={'pushed': True}), \
             patch.object(service, '_resolve_publish_context',
                          return_value=([], '', None)):
            result = service.update_source_for_task('T1')
        self.assertFalse(result['updated'])

    def test_skips_when_get_repository_fails(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'feat/x'
        repo.get_repository.side_effect = ValueError('unknown')
        service = AgentService(**_kwargs(repository_service=repo))
        with patch.object(service, 'push_task',
                          return_value={'pushed': True}), \
             patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'feat/x',
                                        SimpleNamespace(id='T1'))):
            result = service.update_source_for_task('T1')
        self.assertEqual(len(result['skipped_repositories']), 1)

    def test_skips_when_local_path_blank(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'feat/x'
        repo.get_repository.return_value = SimpleNamespace(
            id='r1', local_path='',
        )
        service = AgentService(**_kwargs(repository_service=repo))
        with patch.object(service, 'push_task',
                          return_value={'pushed': True}), \
             patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'feat/x',
                                        SimpleNamespace(id='T1'))):
            result = service.update_source_for_task('T1')
        self.assertEqual(len(result['skipped_repositories']), 1)

    def test_records_update_warning(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'feat/x'
        repo.get_repository.return_value = SimpleNamespace(
            id='r1', local_path='/path',
        )
        repo.update_source_to_task_branch.return_value = {
            'warning': 'stashed changes', 'stash_conflict': False,
        }
        service = AgentService(**_kwargs(repository_service=repo))
        with patch.object(service, 'push_task',
                          return_value={'pushed': True}), \
             patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'feat/x',
                                        SimpleNamespace(id='T1'))):
            result = service.update_source_for_task('T1')
        self.assertTrue(result['updated'])
        self.assertEqual(len(result['warnings']), 1)

    def test_handles_runtime_error_in_update_source(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'feat/x'
        repo.get_repository.return_value = SimpleNamespace(
            id='r1', local_path='/path',
        )
        repo.update_source_to_task_branch.side_effect = RuntimeError('dirty')
        service = AgentService(**_kwargs(repository_service=repo))
        service.logger = MagicMock()
        with patch.object(service, 'push_task',
                          return_value={'pushed': True}), \
             patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'feat/x',
                                        SimpleNamespace(id='T1'))):
            result = service.update_source_for_task('T1')
        self.assertEqual(len(result['failed_repositories']), 1)

    def test_handles_generic_exception_in_update_source(self) -> None:
        repo_obj = SimpleNamespace(id='r1')
        repo = MagicMock()
        repo.build_branch_name.return_value = 'feat/x'
        repo.get_repository.return_value = SimpleNamespace(
            id='r1', local_path='/path',
        )
        repo.update_source_to_task_branch.side_effect = OSError('FS fail')
        service = AgentService(**_kwargs(repository_service=repo))
        service.logger = MagicMock()
        with patch.object(service, 'push_task',
                          return_value={'pushed': True}), \
             patch.object(service, '_resolve_publish_context',
                          return_value=([repo_obj], 'feat/x',
                                        SimpleNamespace(id='T1'))):
            result = service.update_source_for_task('T1')
        self.assertEqual(len(result['failed_repositories']), 1)


if __name__ == '__main__':
    unittest.main()
