"""Coverage for medium-size service defensive branches.

Each test names the line(s) it pins. Hermetic — no network, no disk
state mutation beyond ``tempfile``. Mocks the heavy dependencies
(task_service, repository_service, etc.) at the boundary so a
refactor that breaks the fail-safe wiring surfaces here, not in prod.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# ============================================================================
# TriageService — outcome handlers + tag handling
# ============================================================================


class TriageServiceTests(unittest.TestCase):
    def test_init_rejects_missing_task_service(self) -> None:
        # Line 65: ``raise ValueError('task_service is required')``.
        from kato_core_lib.data_layers.service.triage_service import TriageService
        with self.assertRaisesRegex(ValueError, 'task_service is required'):
            TriageService(task_service=None)

    def test_apply_triage_remove_tag_notimplemented_swallowed(self) -> None:
        # Line 133: ``except NotImplementedError: pass`` — provider
        # doesn't expose remove_tag → continue silently.
        from kato_core_lib.data_layers.service.triage_service import TriageService
        from kato_core_lib.data_layers.data.task import Task

        task_service = MagicMock()
        task_service.add_tag.return_value = None
        task_service.remove_tag.side_effect = NotImplementedError(
            'provider has no remove_tag',
        )

        def fake_investigator(_t):
            return 'Reasoning text\nkato:triage:high'

        service = TriageService(
            task_service, triage_investigator=fake_investigator,
        )
        task = Task(id='PROJ-1', summary='x', tags=['kato:triage:investigate'])
        result = service.handle_task(task)
        # The outcome tag was still added even though remove failed silently.
        self.assertEqual(result['triage_tag'], 'kato:triage:high')

    def test_safe_add_comment_swallows_exception(self) -> None:
        # Lines 207-208: ``except Exception`` swallows add_comment failures.
        from kato_core_lib.data_layers.service.triage_service import TriageService
        from kato_core_lib.data_layers.data.task import Task

        task_service = MagicMock()
        task_service.add_comment.side_effect = RuntimeError('comment failed')
        service = TriageService(task_service)
        task = Task(id='PROJ-1', summary='x')
        # Should not raise.
        service._safe_add_comment(task, 'body')

    def test_extract_triage_tag_empty_text_returns_blank(self) -> None:
        # Line 216: ``if not text: return ''``.
        from kato_core_lib.data_layers.service.triage_service import TriageService
        self.assertEqual(TriageService._extract_triage_tag(''), '')
        self.assertEqual(TriageService._extract_triage_tag('   '), '')

    def test_build_claude_triage_investigator_returns_callable_when_available(
        self,
    ) -> None:
        # Line 251: builds the wrapper closure when client has investigate().
        from kato_core_lib.data_layers.service.triage_service import (
            build_claude_triage_investigator,
        )
        from kato_core_lib.data_layers.data.task import Task

        client = MagicMock()
        client.investigate.return_value = 'verdict\nkato:triage:low'
        impl_service = SimpleNamespace(_client=client)
        investigator = build_claude_triage_investigator(impl_service)
        self.assertTrue(callable(investigator))
        result = investigator(Task(id='PROJ-1', summary='x'))
        self.assertIn('kato:triage:low', result)
        client.investigate.assert_called_once()

    def test_triage_prompt_includes_summary_description_and_tags(self) -> None:
        # Lines 262-265: prompt assembly.
        from kato_core_lib.data_layers.service.triage_service import (
            triage_prompt_for_task,
        )
        from kato_core_lib.data_layers.data.task import Task
        task = Task(
            id='PROJ-1',
            summary='fix the build',
            description='build fails on macOS',
        )
        prompt = triage_prompt_for_task(task)
        self.assertIn('fix the build', prompt)
        self.assertIn('build fails on macOS', prompt)
        self.assertIn('kato:triage:', prompt)

    def test_has_investigate_tag_normalizes_dict_tag_entry(self) -> None:
        # Line 281: ``isinstance(raw_tag, dict)`` branch — providers
        # that return tags as dicts ({"name": "kato:triage:investigate"})
        # are normalized so the check still matches.
        from kato_core_lib.data_layers.service.triage_service import TriageService
        from kato_core_lib.data_layers.data.task import Task

        task_service = MagicMock()
        service = TriageService(task_service)
        # Task tags as list of dicts — provider-specific shape.
        task = Task(id='PROJ-1', tags=[{'name': 'kato:triage:investigate'}])
        # handle_task returns the unavailable result (no investigator)
        # rather than None — proving the tag was recognized.
        self.assertIsNotNone(service.handle_task(task))


# ============================================================================
# WaitPlanningService — every recoverable failure path
# ============================================================================


class WaitPlanningServiceTests(unittest.TestCase):
    def _service(self, **kwargs):
        from kato_core_lib.data_layers.service.wait_planning_service import (
            WaitPlanningService,
        )
        defaults = dict(
            session_manager=MagicMock(),
            repository_service=MagicMock(),
            task_state_service=MagicMock(),
            workspace_manager=None,
            planning_session_runner=None,
        )
        defaults.update(kwargs)
        return WaitPlanningService(**defaults)

    def test_handle_returns_none_for_task_without_tag(self) -> None:
        from kato_core_lib.data_layers.data.task import Task
        service = self._service()
        self.assertIsNone(service.handle_task(Task(id='PROJ-1', tags=[])))

    def test_handle_skips_when_session_manager_missing(self) -> None:
        # Lines 101-106: ``if self._session_manager is None``.
        from kato_core_lib.data_layers.data.task import Task
        from kato_core_lib.data_layers.data.fields import TaskTags
        service = self._service(session_manager=None)
        result = service.handle_task(
            Task(id='PROJ-1', tags=[TaskTags.WAIT_PLANNING]),
        )
        self.assertIsNotNone(result)

    def test_mark_workspace_waiting_swallows_when_no_workspace_manager(
        self,
    ) -> None:
        # Lines 159-160: workspace_manager is None → return silently.
        from kato_core_lib.data_layers.data.task import Task
        service = self._service(workspace_manager=None)
        # Should not raise.
        service._mark_workspace_waiting_for_operator(Task(id='PROJ-1'))

    def test_mark_workspace_waiting_swallows_update_exception(self) -> None:
        # Lines 169-170: update_resume_on_startup raises → log + continue.
        from kato_core_lib.data_layers.data.task import Task
        workspace_manager = MagicMock()
        workspace_manager.update_resume_on_startup.side_effect = RuntimeError(
            'meta write failed',
        )
        service = self._service(workspace_manager=workspace_manager)
        service.logger = MagicMock()
        service._mark_workspace_waiting_for_operator(Task(id='PROJ-1'))
        service.logger.exception.assert_called_once()

    def test_move_to_in_progress_swallows_exception(self) -> None:
        # Lines 182-183: ``task_state_service`` raises → log + continue.
        from kato_core_lib.data_layers.data.task import Task
        task_state = MagicMock()
        task_state.move_task_to_in_progress.side_effect = RuntimeError(
            'state move failed',
        )
        service = self._service(task_state_service=task_state)
        service.logger = MagicMock()
        service._move_to_in_progress(Task(id='PROJ-1'))
        service.logger.exception.assert_called_once()

    def test_resolve_planning_context_empty_repositories(self) -> None:
        # Line 197: ``return _PlanningContext(cwd='', expected_branch='')``.
        from kato_core_lib.data_layers.data.task import Task
        repo_service = MagicMock()
        repo_service.resolve_task_repositories.return_value = []
        service = self._service(repository_service=repo_service)
        ctx = service._resolve_planning_context(Task(id='PROJ-1'))
        self.assertEqual(ctx.cwd, '')
        self.assertEqual(ctx.expected_branch, '')

    def test_resolve_planning_context_empty_after_preparation(self) -> None:
        # Line 201: ``return _PlanningContext(cwd='', expected_branch='')``
        # after _prepare_repositories returns empty.
        from kato_core_lib.data_layers.data.task import Task
        repo_service = MagicMock()
        repo_service.resolve_task_repositories.return_value = [
            SimpleNamespace(id='repo-a', local_path='/tmp/repo'),
        ]
        repo_service.prepare_task_repositories.return_value = []
        service = self._service(repository_service=repo_service)
        ctx = service._resolve_planning_context(Task(id='PROJ-1'))
        self.assertEqual(ctx.expected_branch, '')

    def test_resolve_planning_context_blank_branch_name(self) -> None:
        # Line 206: blank branch_name → return without expected_branch.
        from kato_core_lib.data_layers.data.task import Task
        repo_service = MagicMock()
        repo_obj = SimpleNamespace(id='repo-a', local_path='/tmp/repo')
        repo_service.resolve_task_repositories.return_value = [repo_obj]
        repo_service.prepare_task_repositories.return_value = [repo_obj]
        repo_service.build_branch_name.return_value = ''  # blank
        service = self._service(repository_service=repo_service)
        ctx = service._resolve_planning_context(Task(id='PROJ-1'))
        self.assertEqual(ctx.cwd, '/tmp/repo')
        self.assertEqual(ctx.expected_branch, '')

    def test_resolve_planning_context_branch_checkout_fails(self) -> None:
        # Line 208: ``if not self._check_out_branches(...): return ...``.
        from kato_core_lib.data_layers.data.task import Task
        repo_service = MagicMock()
        repo_obj = SimpleNamespace(id='repo-a', local_path='/tmp/repo')
        repo_service.resolve_task_repositories.return_value = [repo_obj]
        repo_service.prepare_task_repositories.return_value = [repo_obj]
        repo_service.build_branch_name.return_value = 'feat/proj-1'
        repo_service.prepare_task_branches.side_effect = RuntimeError(
            'git checkout failed',
        )
        service = self._service(repository_service=repo_service)
        ctx = service._resolve_planning_context(Task(id='PROJ-1'))
        self.assertEqual(ctx.expected_branch, '')

    def test_safe_call_logs_and_returns_fallback(self) -> None:
        # Lines 267-269: ``except Exception: log + return fallback``.
        from kato_core_lib.data_layers.data.task import Task
        service = self._service()
        service.logger = MagicMock()

        def boom():
            raise RuntimeError('boom')

        result = service._safe_call(
            Task(id='PROJ-1'),
            'failing step %s',
            fallback='fallback value',
            action=boom,
        )
        self.assertEqual(result, 'fallback value')
        service.logger.exception.assert_called_once()

    def test_session_starter_defaults_returns_empty_without_runner(self) -> None:
        # Lines 282-283: ``return {}`` when runner is None.
        service = self._service(planning_session_runner=None)
        self.assertEqual(service._session_starter_defaults(), {})

    def test_session_starter_defaults_handles_runner_without_defaults(
        self,
    ) -> None:
        # Lines 296-298: ``if defaults is None: return {}``.
        service = self._service(
            planning_session_runner=SimpleNamespace(_defaults=None),
        )
        self.assertEqual(service._session_starter_defaults(), {})

    def test_session_starter_defaults_pulls_fields_from_runner(self) -> None:
        # Lines 299-304: read string fields + max_turns from defaults.
        defaults = SimpleNamespace(
            binary='claude',
            model='haiku',
            permission_mode='plan',
            permission_prompt_tool='',
            allowed_tools='',
            disallowed_tools='',
            effort='',
            max_turns=12,
        )
        service = self._service(
            planning_session_runner=SimpleNamespace(_defaults=defaults),
        )
        result = service._session_starter_defaults()
        self.assertEqual(result['binary'], 'claude')
        self.assertEqual(result['model'], 'haiku')
        self.assertEqual(result['permission_mode'], 'plan')
        self.assertEqual(result['max_turns'], 12)


# ============================================================================
# TaskPublisher — defensive branches
# ============================================================================


class TaskPublisherTests(unittest.TestCase):
    def test_max_retries_from_config_caps_at_zero(self) -> None:
        # Line 91-92 covered: bad config → DEFAULT.
        from kato_core_lib.data_layers.service.task_publisher import TaskPublisher
        # ValueError from int() → fallback to DEFAULT_PUBLISH_MAX_RETRIES.
        cfg = {'task_publish': {'max_retries': 'not-a-number'}}
        result = TaskPublisher.max_retries_from_config(cfg)
        self.assertEqual(result, TaskPublisher.DEFAULT_PUBLISH_MAX_RETRIES)

    def test_format_publish_failure_uses_class_name_for_blank_exception(
        self,
    ) -> None:
        # Line 67: ``first_line = exc.__class__.__name__`` fallback.
        from kato_core_lib.data_layers.service.task_publisher import (
            _format_publish_failure,
        )

        class _NoMessage(Exception):
            def __str__(self):
                return ''

        self.assertEqual(_format_publish_failure(_NoMessage()), '_NoMessage')

    def test_format_publish_failure_truncates_long_messages(self) -> None:
        # Line 67: ``if len(first_line) > 280``. Locks the cap so a
        # huge error string doesn't make the ticket comment unreadable.
        from kato_core_lib.data_layers.service.task_publisher import (
            _format_publish_failure,
        )
        long_msg = 'x' * 400
        result = _format_publish_failure(RuntimeError(long_msg))
        self.assertTrue(len(result) <= 280)
        self.assertTrue(result.endswith('...'))

    def test_comment_task_started_swallows_exception(self) -> None:
        # Lines 173-174: ``except Exception: log + continue``.
        from kato_core_lib.data_layers.service.task_publisher import TaskPublisher
        from kato_core_lib.data_layers.data.task import Task
        task_service = MagicMock()
        task_service.add_comment.side_effect = RuntimeError('comment failed')
        publisher = TaskPublisher(
            task_service=task_service,
            task_state_service=MagicMock(),
            repository_service=MagicMock(),
            notification_service=MagicMock(),
            state_registry=MagicMock(),
            failure_handler=MagicMock(),
        )
        publisher.logger = MagicMock()
        # Must not raise.
        publisher.comment_task_started(Task(id='PROJ-1'))
        publisher.logger.exception.assert_called_once()

    def test_create_pull_requests_records_unknown_error_when_outcome_none(
        self,
    ) -> None:
        # Lines 216-219: defensive ``if outcome is None`` branch — a
        # future regression that vanishes the return value gets caught.
        from kato_core_lib.data_layers.service.task_publisher import TaskPublisher
        from kato_core_lib.data_layers.data.task import Task
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext

        publisher = TaskPublisher(
            task_service=MagicMock(),
            task_state_service=MagicMock(),
            repository_service=MagicMock(),
            notification_service=MagicMock(),
            state_registry=MagicMock(),
            failure_handler=MagicMock(),
        )
        # Patch the per-repo helper to return None — drives the defensive branch.
        with patch.object(
            publisher,
            '_create_pull_request_for_repository',
            return_value=None,
        ):
            repo = SimpleNamespace(id='repo-a', local_path='/tmp/x')
            prepared = PreparedTaskContext(
                repositories=[repo],
                repository_branches={'repo-a': 'feat/x'},
                branch_name='feat/x',
                agents_instructions='',
            )
            prs, failed, unchanged = publisher._create_pull_requests(
                Task(id='PROJ-1', summary='x'),
                prepared,
                execution={},
            )
        self.assertEqual(prs, [])
        self.assertEqual(failed, [('repo-a', 'unknown error (no reason captured)')])

    def test_run_publish_with_retry_reraises_on_exhaustion(self) -> None:
        # Line 593: ``raise last_exc`` after loop exits.
        from kato_core_lib.data_layers.service.task_publisher import TaskPublisher
        publisher = TaskPublisher(
            task_service=MagicMock(),
            task_state_service=MagicMock(),
            repository_service=MagicMock(),
            notification_service=MagicMock(),
            state_registry=MagicMock(),
            failure_handler=MagicMock(),
            publish_max_retries=1,  # 2 attempts total
            sleep_fn=lambda _s: None,
        )

        def always_fail():
            raise RuntimeError('boom')

        with self.assertRaisesRegex(RuntimeError, 'boom'):
            publisher._run_publish_with_retry(
                task_id='PROJ-1',
                operation_label='test op',
                operation=always_fail,
            )


# ============================================================================
# WorkspaceRecoveryService — every branch
# ============================================================================


class WorkspaceRecoveryServiceTests(unittest.TestCase):
    def test_init_rejects_missing_workspace_manager(self) -> None:
        # Line 63.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        with self.assertRaisesRegex(ValueError, 'workspace_manager is required'):
            WorkspaceRecoveryService(
                workspace_manager=None,
                task_service=MagicMock(),
                repository_service=MagicMock(),
            )

    def test_init_rejects_missing_task_service(self) -> None:
        # Line 65.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        with self.assertRaisesRegex(ValueError, 'task_service is required'):
            WorkspaceRecoveryService(
                workspace_manager=MagicMock(),
                task_service=None,
                repository_service=MagicMock(),
            )

    def test_init_rejects_missing_repository_service(self) -> None:
        # Line 67.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        with self.assertRaisesRegex(ValueError, 'repository_service is required'):
            WorkspaceRecoveryService(
                workspace_manager=MagicMock(),
                task_service=MagicMock(),
                repository_service=None,
            )

    def test_recover_returns_early_when_no_live_tasks(self) -> None:
        # Lines 85-92: orphan folders exist but live-task fetch failed.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'PROJ-1').mkdir()  # orphan
            workspace_manager = MagicMock()
            workspace_manager.root = root
            task_service = MagicMock()
            task_service.get_assigned_tasks.side_effect = RuntimeError('fetch failed')
            task_service.get_review_tasks.side_effect = RuntimeError('fetch failed')
            service = WorkspaceRecoveryService(
                workspace_manager=workspace_manager,
                task_service=task_service,
                repository_service=MagicMock(),
            )
            service.logger = MagicMock()
            self.assertEqual(service.recover_orphan_workspaces(), [])
            service.logger.warning.assert_called()

    def test_recover_one_skips_when_no_live_task_with_id(self) -> None:
        # Lines 142-146: orphan folder without a matching live task.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        with tempfile.TemporaryDirectory() as td:
            orphan_dir = Path(td) / 'PROJ-99'
            orphan_dir.mkdir()
            service = WorkspaceRecoveryService(
                workspace_manager=MagicMock(),
                task_service=MagicMock(),
                repository_service=MagicMock(),
            )
            self.assertIsNone(service._recover_one(orphan_dir, {}))

    def test_recover_one_skips_when_no_git_subdirs(self) -> None:
        # Lines 148-152.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        with tempfile.TemporaryDirectory() as td:
            orphan_dir = Path(td) / 'PROJ-1'
            orphan_dir.mkdir()
            service = WorkspaceRecoveryService(
                workspace_manager=MagicMock(),
                task_service=MagicMock(),
                repository_service=MagicMock(),
            )
            task = SimpleNamespace(id='PROJ-1', summary='x')
            self.assertIsNone(
                service._recover_one(orphan_dir, {'PROJ-1': task}),
            )

    def test_recover_one_skips_when_resolve_fails(self) -> None:
        # Lines 155-160: resolve_task_repositories raises → skip.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        with tempfile.TemporaryDirectory() as td:
            orphan_dir = Path(td) / 'PROJ-1'
            (orphan_dir / 'repo-a' / '.git').mkdir(parents=True)
            repo_service = MagicMock()
            repo_service.resolve_task_repositories.side_effect = RuntimeError('boom')
            service = WorkspaceRecoveryService(
                workspace_manager=MagicMock(),
                task_service=MagicMock(),
                repository_service=repo_service,
            )
            task = SimpleNamespace(id='PROJ-1', summary='x')
            self.assertIsNone(
                service._recover_one(orphan_dir, {'PROJ-1': task}),
            )

    def test_recover_one_skips_when_no_matching_repository_ids(self) -> None:
        # Line 162-168.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        with tempfile.TemporaryDirectory() as td:
            orphan_dir = Path(td) / 'PROJ-1'
            (orphan_dir / 'unknown-repo' / '.git').mkdir(parents=True)
            repo_service = MagicMock()
            repo_service.resolve_task_repositories.return_value = [
                SimpleNamespace(id='different-repo'),
            ]
            service = WorkspaceRecoveryService(
                workspace_manager=MagicMock(),
                task_service=MagicMock(),
                repository_service=repo_service,
            )
            task = SimpleNamespace(id='PROJ-1', summary='x')
            self.assertIsNone(
                service._recover_one(orphan_dir, {'PROJ-1': task}),
            )

    def test_collect_orphan_directories_returns_empty_when_root_missing(
        self,
    ) -> None:
        # Line 110: ``if not root.exists(): return []``.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        with tempfile.TemporaryDirectory() as td:
            workspace_manager = MagicMock()
            workspace_manager.root = Path(td) / 'nope'
            service = WorkspaceRecoveryService(
                workspace_manager=workspace_manager,
                task_service=MagicMock(),
                repository_service=MagicMock(),
            )
            self.assertEqual(service._collect_orphan_directories(), [])

    def test_collect_skips_existing_metadata_folders(self) -> None:
        # ``if (entry / metadata_filename).is_file(): continue`` —
        # the recovery service now reads the filename from the
        # workspace_manager's data_access at runtime so it honours
        # kato's override (``.kato-meta.json``) without hardcoding.
        # Stub ``data_access.metadata_filename`` on the mocked
        # workspace_manager so the lookup returns the right string.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        from workspace_core_lib.workspace_core_lib.data_layers.data_access.workspace_data_access import (
            DEFAULT_METADATA_FILENAME,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'managed').mkdir()
            (root / 'managed' / DEFAULT_METADATA_FILENAME).write_text('{}')
            (root / 'orphan').mkdir()
            workspace_manager = MagicMock()
            workspace_manager.root = root
            workspace_manager.data_access.metadata_filename = (
                DEFAULT_METADATA_FILENAME
            )
            service = WorkspaceRecoveryService(
                workspace_manager=workspace_manager,
                task_service=MagicMock(),
                repository_service=MagicMock(),
            )
            orphans = service._collect_orphan_directories()
        self.assertEqual([o.name for o in orphans], ['orphan'])

    def test_match_repository_ids_uses_case_insensitive_match(self) -> None:
        # Line 222-224: case-insensitive folder name match for repo ids.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        matched = WorkspaceRecoveryService._match_repository_ids(
            repository_dirs=['Repo-A', 'repo-b'],
            task_repositories=[
                SimpleNamespace(id='repo-a'),  # case-insensitive match
                SimpleNamespace(id='repo-b'),
                SimpleNamespace(id=''),  # skipped (line 197)
            ],
        )
        self.assertEqual(matched, ['repo-a', 'repo-b'])


# ============================================================================
# RepositoryApprovalService — defensive paths
# ============================================================================


class RepositoryApprovalServiceTests(unittest.TestCase):
    def test_default_storage_path_honours_env_override(self) -> None:
        # Line 58.
        from kato_core_lib.data_layers.service.repository_approval_service import (
            APPROVED_REPOSITORIES_PATH_ENV_KEY, default_storage_path,
        )
        with patch.dict(os.environ,
                        {APPROVED_REPOSITORIES_PATH_ENV_KEY: '~/custom.json'}):
            path = default_storage_path()
        self.assertTrue(str(path).endswith('custom.json'))

    def test_is_approved_returns_none_for_blank_id(self) -> None:
        # Line 104.
        from kato_core_lib.data_layers.service.repository_approval_service import (
            RepositoryApprovalService,
        )
        with tempfile.TemporaryDirectory() as td:
            service = RepositoryApprovalService(Path(td) / 'approvals.json')
            self.assertIsNone(service.is_approved(''))
            self.assertIsNone(service.is_approved('   '))

    def test_lookup_returns_none_for_blank_id(self) -> None:
        # Line 114.
        from kato_core_lib.data_layers.service.repository_approval_service import (
            RepositoryApprovalService,
        )
        with tempfile.TemporaryDirectory() as td:
            service = RepositoryApprovalService(Path(td) / 'approvals.json')
            self.assertIsNone(service.lookup(''))

    def test_lookup_returns_none_when_not_in_sidecar(self) -> None:
        # Line 119: loop completes without match → return None.
        from kato_core_lib.data_layers.service.repository_approval_service import (
            RepositoryApprovalService,
        )
        with tempfile.TemporaryDirectory() as td:
            service = RepositoryApprovalService(Path(td) / 'approvals.json')
            self.assertIsNone(service.lookup('never-approved'))

    def test_approve_raises_for_blank_repository_id(self) -> None:
        # Line 142.
        from kato_core_lib.data_layers.service.repository_approval_service import (
            RepositoryApprovalService,
        )
        with tempfile.TemporaryDirectory() as td:
            service = RepositoryApprovalService(Path(td) / 'approvals.json')
            with self.assertRaisesRegex(ValueError, 'repository_id must be non-empty'):
                service.approve('', 'https://example.com/r.git')

    def test_revoke_returns_false_for_blank_id(self) -> None:
        # Line 171.
        from kato_core_lib.data_layers.service.repository_approval_service import (
            RepositoryApprovalService,
        )
        with tempfile.TemporaryDirectory() as td:
            service = RepositoryApprovalService(Path(td) / 'approvals.json')
            self.assertFalse(service.revoke(''))

    def test_unapproved_skips_blank_ids_in_input(self) -> None:
        # Line 194: ``if not repo_id: continue``.
        from kato_core_lib.data_layers.service.repository_approval_service import (
            RepositoryApprovalService,
        )
        with tempfile.TemporaryDirectory() as td:
            service = RepositoryApprovalService(Path(td) / 'approvals.json')
            result = service.unapproved_repository_ids([
                SimpleNamespace(id=''),  # skipped
                SimpleNamespace(id='unapproved-1'),
            ])
            self.assertEqual(result, ['unapproved-1'])

    def test_restricted_mode_skips_blank_ids(self) -> None:
        # Line 213: ``if not repo_id: continue``.
        from kato_core_lib.data_layers.service.repository_approval_service import (
            RepositoryApprovalService,
        )
        with tempfile.TemporaryDirectory() as td:
            service = RepositoryApprovalService(Path(td) / 'approvals.json')
            result = service.restricted_mode_repository_ids([
                SimpleNamespace(id=''),
                SimpleNamespace(id='some-repo'),
            ])
            # Neither is approved; result is empty but no crash on blank id.
            self.assertEqual(result, [])

    def test_operator_identity_falls_back_to_username_then_unknown(self) -> None:
        # Line 67-68: ``user or 'unknown'``.
        from kato_core_lib.data_layers.service.repository_approval_service import (
            operator_identity,
        )
        # No KATO_OPERATOR_EMAIL, no USER, no USERNAME.
        self.assertEqual(operator_identity(env={}), 'unknown')
        # USER set → wins over default.
        self.assertEqual(operator_identity(env={'USER': 'alice'}), 'alice')
        # USERNAME (Windows) set → wins.
        self.assertEqual(operator_identity(env={'USERNAME': 'bob'}), 'bob')


# ============================================================================
# RepositoryPublicationService — error paths
# ============================================================================


class RepositoryPublicationServiceTests(unittest.TestCase):
    def _service(self):
        from kato_core_lib.data_layers.service.repository_publication_service import (
            RepositoryPublicationService,
        )
        return RepositoryPublicationService(
            repository_service=MagicMock(),
            max_retries=2,
        )

    def test_find_pull_requests_returns_empty_when_api_setup_fails(self) -> None:
        # Lines 117-119: ``if not _ensure_pr_api...: return []``.
        service = self._service()
        service._repository_service._prepare_pull_request_api.side_effect = (
            RuntimeError('no token')
        )
        repo = SimpleNamespace(id='repo-a')
        self.assertEqual(service.find_pull_requests(repo), [])

    def test_list_pull_request_comments_returns_empty_when_api_setup_fails(
        self,
    ) -> None:
        # Lines 104-105 (the comments path).
        service = self._service()
        service._repository_service._prepare_pull_request_api.side_effect = (
            RuntimeError('no token'),
        )
        repo = SimpleNamespace(id='repo-a')
        self.assertEqual(service.list_pull_request_comments(repo, 'pr-1'), [])

    def test_ensure_pr_api_logs_each_failure_only_once(self) -> None:
        # Lines 138-145: same repo failing twice logs only on first call.
        service = self._service()
        service._repository_service._prepare_pull_request_api.side_effect = (
            RuntimeError('persistent failure')
        )
        service.logger = MagicMock()
        repo = SimpleNamespace(id='repo-a')
        service._ensure_pr_api_or_log_once(repo, 'lookup')
        service._ensure_pr_api_or_log_once(repo, 'lookup')
        # Only logged once even though api setup failed twice.
        self.assertEqual(service.logger.info.call_count, 1)

    def test_resolve_review_comment_passes_through(self) -> None:
        # Lines 156-157: thin delegate.
        service = self._service()
        comment = SimpleNamespace(comment_id='c1')
        repo = SimpleNamespace(id='repo-a')
        service.resolve_review_comment(repo, comment)
        service._repository_service._pull_request_data_access.assert_called()

    def test_reply_to_review_comment_passes_through(self) -> None:
        # Line 164.
        service = self._service()
        comment = SimpleNamespace(comment_id='c1')
        repo = SimpleNamespace(id='repo-a')
        service.reply_to_review_comment(repo, comment, body='done')
        service._repository_service._pull_request_data_access.assert_called()

    def test_restore_workspace_after_publication_returns_early_for_per_task_clone(
        self,
    ) -> None:
        # Lines 163-164: early-return when path looks like a per-task clone.
        service = self._service()
        with tempfile.TemporaryDirectory() as td:
            clone_path = Path(td) / 'PROJ-1' / 'repo-a'
            clone_path.mkdir(parents=True)
            (clone_path.parent / '.kato-meta.json').write_text('{}')
            repo = SimpleNamespace(id='repo-a', local_path=str(clone_path))
            service._restore_workspace_after_publication(repo, 'main')
        # restore_task_repositories was NOT called.
        service._repository_service.restore_task_repositories.assert_not_called()

    def test_restore_workspace_swallows_restore_exception(self) -> None:
        # Lines 167-172: exception during restore is logged + swallowed.
        service = self._service()
        service.logger = MagicMock()
        service._repository_service.restore_task_repositories.side_effect = (
            RuntimeError('restore failed')
        )
        repo = SimpleNamespace(id='repo-a', local_path='/non-per-task-path')
        # Should not raise.
        service._restore_workspace_after_publication(repo, 'main')
        service.logger.exception.assert_called_once()

    def test_is_per_task_workspace_clone_returns_false_for_blank_path(
        self,
    ) -> None:
        # Line 179: ``if not local_path: return False``.
        from kato_core_lib.data_layers.service.repository_publication_service import (
            RepositoryPublicationService,
        )
        self.assertFalse(
            RepositoryPublicationService._is_per_task_workspace_clone(
                SimpleNamespace(local_path=''),
            )
        )

    def test_is_per_task_workspace_clone_swallows_oserror(self) -> None:
        # Lines 182-183.
        from kato_core_lib.data_layers.service.repository_publication_service import (
            RepositoryPublicationService,
        )
        with patch.object(Path, 'is_file', side_effect=OSError('FS issue')):
            self.assertFalse(
                RepositoryPublicationService._is_per_task_workspace_clone(
                    SimpleNamespace(local_path='/some/path'),
                )
            )


# ============================================================================
# PlanningSessionRunner — config + helper paths
# ============================================================================


class PlanningSessionRunnerTests(unittest.TestCase):
    def test_coerce_optional_int_handles_none_and_blank(self) -> None:
        # Lines 38-39: ``None`` / '' → None.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            _coerce_optional_int,
        )
        self.assertIsNone(_coerce_optional_int(None))
        self.assertIsNone(_coerce_optional_int(''))

    def test_coerce_optional_int_handles_typeerror(self) -> None:
        # Lines 40-44: TypeError/ValueError → None.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            _coerce_optional_int,
        )
        self.assertIsNone(_coerce_optional_int('not a number'))
        self.assertIsNone(_coerce_optional_int(object()))
        # Zero / negative → None too.
        self.assertIsNone(_coerce_optional_int(0))
        self.assertIsNone(_coerce_optional_int(-5))
        # Positive integer → returned.
        self.assertEqual(_coerce_optional_int(7), 7)

    def test_from_config_returns_none_for_non_claude_backend(self) -> None:
        # Lines 100-101: non-claude backend → None.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner,
        )
        self.assertIsNone(
            PlanningSessionRunner.from_config(
                open_cfg=SimpleNamespace(),
                agent_backend='openhands',
                session_manager=MagicMock(),
            )
        )

    def test_from_config_returns_none_when_session_manager_missing(self) -> None:
        # Lines 103.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner,
        )
        self.assertIsNone(
            PlanningSessionRunner.from_config(
                open_cfg=SimpleNamespace(),
                agent_backend='claude',
                session_manager=None,
            )
        )

    def test_from_config_returns_none_when_claude_cfg_missing(self) -> None:
        # Line 106: ``if claude_cfg is None: return None``.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner,
        )
        cfg = SimpleNamespace(claude=None)
        self.assertIsNone(
            PlanningSessionRunner.from_config(
                open_cfg=cfg,
                agent_backend='claude',
                session_manager=MagicMock(),
            )
        )

    def test_resume_session_for_chat_rejects_blank_task_id(self) -> None:
        # Line 170.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner, StreamingSessionDefaults,
        )
        runner = PlanningSessionRunner(
            session_manager=MagicMock(),
            defaults=StreamingSessionDefaults(),
        )
        with self.assertRaisesRegex(ValueError, 'task_id is required'):
            runner.resume_session_for_chat(task_id='', message='hi')

    def test_resume_session_for_chat_rejects_blank_message(self) -> None:
        # Line 173.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner, StreamingSessionDefaults,
        )
        runner = PlanningSessionRunner(
            session_manager=MagicMock(),
            defaults=StreamingSessionDefaults(),
        )
        with self.assertRaisesRegex(ValueError, 'message is required'):
            runner.resume_session_for_chat(task_id='T1', message='   ')

    def test_resume_session_for_chat_sends_raw_message_when_session_id_persisted(
        self,
    ) -> None:
        # Bug-fix lock: when ``--resume <session_id>`` will be used,
        # Claude already has the workspace context from the prior JSONL.
        # Wrapping the follow-up message in another inventory/continuity
        # block makes Claude treat each respawn as a fresh task and
        # re-explore the workspace — burning tokens and producing the
        # "starts everything from scratch" behavior the operator sees.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner, StreamingSessionDefaults,
        )
        manager = MagicMock()
        # Existing record with a persisted session id.
        manager.get_record.return_value = SimpleNamespace(
            agent_session_id='abc-123',
        )
        runner = PlanningSessionRunner(
            session_manager=manager, defaults=StreamingSessionDefaults(),
        )
        runner.resume_session_for_chat(
            task_id='T1', message='please look at the bug',
            cwd='/wks/T1', additional_dirs=['/wks/T1/repo-b'],
        )
        # The raw message went through verbatim — NO ``Repositories
        # available`` / ``Trust it`` / ``Forbidden`` wrapper.
        sent_prompt = manager.start_session.call_args.kwargs['initial_prompt']
        self.assertEqual(sent_prompt, 'please look at the bug')

    def test_resume_session_for_chat_wraps_message_on_first_spawn(self) -> None:
        # Symmetric guarantee: when there's no session id to resume from
        # (first message after kato adopt / fresh workspace), DO wrap the
        # message so Claude sees the workspace inventory + guardrails on
        # its very first turn. This is the only path that justifies the
        # workspace-context preamble.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner, StreamingSessionDefaults,
        )
        manager = MagicMock()
        # No record yet → first spawn → wrap.
        manager.get_record.return_value = None
        runner = PlanningSessionRunner(
            session_manager=manager, defaults=StreamingSessionDefaults(),
        )
        runner.resume_session_for_chat(
            task_id='T1', message='start the work',
            cwd='/wks/T1',
        )
        sent_prompt = manager.start_session.call_args.kwargs['initial_prompt']
        # Continuity block leads.
        self.assertIn('Trust it', sent_prompt)
        self.assertIn('start the work', sent_prompt)

    def test_fix_review_comments_rejects_empty(self) -> None:
        # Line 266.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner, StreamingSessionDefaults,
        )
        runner = PlanningSessionRunner(
            session_manager=MagicMock(),
            defaults=StreamingSessionDefaults(),
        )
        with self.assertRaisesRegex(ValueError, 'at least one comment'):
            runner.fix_review_comments([], 'b', task_id='T')

    def test_fix_review_comments_rejects_blank_task_id(self) -> None:
        # Line 269.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner, StreamingSessionDefaults,
        )
        runner = PlanningSessionRunner(
            session_manager=MagicMock(),
            defaults=StreamingSessionDefaults(),
        )
        with self.assertRaisesRegex(ValueError, 'task_id is required'):
            runner.fix_review_comments(
                [SimpleNamespace(comment_id='c1')], 'b', task_id='   ',
            )

    def test_fix_review_comments_terminates_existing_session(self) -> None:
        # Line 271: existing session detected → terminate before re-spawn.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner, StreamingSessionDefaults,
        )
        manager = MagicMock()
        manager.get_session.return_value = SimpleNamespace(agent_session_id='old')
        runner = PlanningSessionRunner(
            session_manager=manager,
            defaults=StreamingSessionDefaults(),
        )
        # Make ``start_session`` raise so we exit fast — we only care
        # that ``terminate_session`` was called BEFORE the spawn.
        manager.start_session.side_effect = RuntimeError('skip')
        # Build a comment that satisfies ClaudeCliClient._build_review_prompt.
        comment = SimpleNamespace(
            comment_id='c1', body='please fix', author='reviewer',
            file_path='', line_number='', line_type='', commit_sha='',
            all_comments=[],
        )
        with self.assertRaises(RuntimeError):
            runner.fix_review_comments([comment], 'b', task_id='T1')
        manager.terminate_session.assert_called_once_with('T1')

    def test_working_directory_returns_empty_when_no_repositories(self) -> None:
        # Lines 412, 415.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner,
        )
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
        self.assertEqual(
            PlanningSessionRunner._working_directory(None),
            '',
        )
        prepared = PreparedTaskContext(
            repositories=[],
            repository_branches={},
            branch_name='',
            agents_instructions='',
        )
        self.assertEqual(
            PlanningSessionRunner._working_directory(prepared),
            '',
        )

    def test_wait_for_terminal_returns_terminal_when_session_dies(self) -> None:
        # Line 396: ``if not session.is_alive: terminal = session.terminal_event``.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner, StreamingSessionDefaults,
        )
        runner = PlanningSessionRunner(
            session_manager=MagicMock(),
            defaults=StreamingSessionDefaults(),
        )
        terminal_event = SimpleNamespace(is_terminal=True, raw={'result': 'done'})
        session = MagicMock()
        session.poll_event.return_value = None  # no event from polling
        session.is_alive = False  # session died
        session.terminal_event = terminal_event
        result = runner._wait_for_terminal_event(session, task_id='T')
        self.assertIs(result, terminal_event)

    def test_wait_for_terminal_returns_none_after_max_wait_exceeded(self) -> None:
        # Lines 400-406.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner, StreamingSessionDefaults,
        )
        runner = PlanningSessionRunner(
            session_manager=MagicMock(),
            defaults=StreamingSessionDefaults(),
            max_wait_seconds=0.0,  # deadline is "now"
        )
        runner.logger = MagicMock()
        session = MagicMock()
        session.poll_event.return_value = None
        session.is_alive = True  # alive but timeout will fire first
        runner._clock = lambda: 9999.0  # past deadline immediately
        result = runner._wait_for_terminal_event(session, task_id='T')
        self.assertIsNone(result)
        runner.logger.warning.assert_called()


# ============================================================================
# Final close-out: edge lines missed by the broader test classes above
# ============================================================================


class TriageHasInvestigateTagStringInputTests(unittest.TestCase):
    """Line 281: when ``task.tags`` is a plain string (some providers
    return tags this way), normalize to a single-item list."""

    def test_string_tags_normalized_to_list(self) -> None:
        from kato_core_lib.data_layers.service.triage_service import (
            _has_investigate_tag,
        )
        from kato_core_lib.data_layers.data.fields import TaskTags
        # Pass a plain string in place of a list — the function must
        # treat it as a single-element list rather than iterate chars.
        task = SimpleNamespace(tags=TaskTags.TRIAGE_INVESTIGATE)
        self.assertTrue(_has_investigate_tag(task))


class WorkspaceRecoverySingleOrphanExceptionTests(unittest.TestCase):
    """Lines 97-101: exception during ``_recover_one`` of a specific
    orphan must be caught and logged, allowing recovery to continue
    with the next orphan."""

    def test_first_orphan_failure_does_not_block_others(self) -> None:
        # The outer try/except (lines 95-101) catches any exception
        # _recover_one re-raises (e.g. workspace_manager.create() fails)
        # so a single broken orphan never aborts the recovery pass.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'PROJ-1' / 'repo-a' / '.git').mkdir(parents=True)
            workspace_manager = MagicMock()
            workspace_manager.root = root
            # workspace_manager.create raises — NOT caught inside _recover_one.
            workspace_manager.create.side_effect = RuntimeError(
                'workspace registry write failed',
            )
            task_service = MagicMock()
            task_proj1 = SimpleNamespace(id='PROJ-1', summary='x')
            task_service.get_assigned_tasks.return_value = [task_proj1]
            task_service.get_review_tasks.return_value = []
            repo_service = MagicMock()
            repo_service.resolve_task_repositories.return_value = [
                SimpleNamespace(id='repo-a'),
            ]
            service = WorkspaceRecoveryService(
                workspace_manager=workspace_manager,
                task_service=task_service,
                repository_service=repo_service,
            )
            service.logger = MagicMock()
            # Patch the claude session locator so we don't depend on
            # external state.
            with patch(
                'kato_core_lib.data_layers.service.workspace_recovery_service.'
                'find_session_id_for_cwd',
                return_value=None,
            ):
                # The whole recovery pass must not raise.
                result = service.recover_orphan_workspaces()
        self.assertEqual(result, [])
        # The crash inside _recover_one was caught at the outer level.
        service.logger.exception.assert_called_once()


class MediumServicesRemainingEdgeTests(unittest.TestCase):
    """Final edge lines that the broader test classes above didn't quite
    reach. Each is a tiny defensive path; one test per line for clarity."""

    def test_planning_runner_treats_dead_session_with_no_terminal_event(
        self,
    ) -> None:
        # Branch: session.is_alive=False + session.terminal_event=None.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner, StreamingSessionDefaults,
        )
        runner = PlanningSessionRunner(
            session_manager=MagicMock(),
            defaults=StreamingSessionDefaults(),
        )
        session = MagicMock()
        session.poll_event.return_value = None
        session.is_alive = False
        session.terminal_event = None
        self.assertIsNone(runner._wait_for_terminal_event(session, task_id='T'))

    def test_planning_runner_continues_on_non_terminal_events(self) -> None:
        # Line 396: non-terminal event → continue the loop. We feed a
        # non-terminal event first, then a terminal one; the loop must
        # consume both and end with the terminal event.
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner, StreamingSessionDefaults,
        )
        runner = PlanningSessionRunner(
            session_manager=MagicMock(),
            defaults=StreamingSessionDefaults(),
        )
        non_terminal = SimpleNamespace(is_terminal=False, raw={})
        terminal = SimpleNamespace(is_terminal=True, raw={'result': 'done'})
        session = MagicMock()
        session.poll_event.side_effect = [non_terminal, terminal]
        session.is_alive = True
        result = runner._wait_for_terminal_event(session, task_id='T')
        self.assertIs(result, terminal)

    def test_approval_service_storage_path_property(self) -> None:
        # Line 94: @property accessor.
        from kato_core_lib.data_layers.service.repository_approval_service import (
            RepositoryApprovalService,
        )
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'approvals.json'
            service = RepositoryApprovalService(target)
            self.assertEqual(service.storage_path, target)

    def test_publication_find_pull_requests_succeeds(self) -> None:
        # Line 119: success branch — api ready, delegates to data access.
        from kato_core_lib.data_layers.service.repository_publication_service import (
            RepositoryPublicationService,
        )
        repo_service = MagicMock()
        data_access = MagicMock()
        data_access.find_pull_requests.return_value = [{'id': '1'}]
        repo_service._pull_request_data_access.return_value = data_access
        service = RepositoryPublicationService(repo_service, max_retries=1)
        repo = SimpleNamespace(id='repo-a')
        result = service.find_pull_requests(repo, source_branch='feat/x')
        self.assertEqual(result, [{'id': '1'}])

    def test_publisher_logs_pushed_branch_without_url(self) -> None:
        # Line 268: ``else`` branch when pull_request_url is blank.
        from kato_core_lib.data_layers.service.task_publisher import TaskPublisher
        from kato_core_lib.data_layers.data.task import Task
        from kato_core_lib.data_layers.data.fields import PullRequestFields
        from kato_core_lib.helpers.task_context_utils import PreparedTaskContext

        publisher = TaskPublisher(
            task_service=MagicMock(),
            task_state_service=MagicMock(),
            repository_service=MagicMock(),
            notification_service=MagicMock(),
            state_registry=MagicMock(),
            failure_handler=MagicMock(),
        )
        publisher.logger = MagicMock()
        # Patch the per-repo PR creator to return a PR dict with NO url.
        pr = {
            PullRequestFields.ID: '17',
            PullRequestFields.URL: '',  # blank URL drives the else branch
            PullRequestFields.TITLE: 'fix',
        }
        with patch.object(
            publisher, '_create_repository_pull_request', return_value=pr,
        ):
            repo = SimpleNamespace(id='repo-a', local_path='/tmp/x')
            prepared = PreparedTaskContext(
                repositories=[repo],
                repository_branches={'repo-a': 'feat/x'},
                branch_name='feat/x',
                agents_instructions='',
            )
            publisher._create_pull_request_for_repository(
                Task(id='PROJ-1'),
                prepared,
                repo,
                description='',
                commit_message='msg',
                session_id='',
            )
        # The else-branch log message DOES NOT include 'and opened PR at <url>'.
        # Just verify _log_task_step was called multiple times (started + log).
        self.assertGreater(publisher.logger.info.call_count, 0)

    def test_triage_extracts_tag_text_from_object_attribute(self) -> None:
        # Line 281: ``isinstance(raw_tag, dict)`` is False → use ``getattr``.
        # Drives the ELSE branch — raw_tag is an object with a ``name`` attr.
        from kato_core_lib.data_layers.service.triage_service import TriageService
        from kato_core_lib.data_layers.data.task import Task

        task_service = MagicMock()
        service = TriageService(task_service)
        # Task tags as list of namespaces with .name — drives line 287.
        task = Task(
            id='PROJ-1',
            tags=[SimpleNamespace(name='kato:triage:investigate')],
        )
        # The unavailable result proves the tag was recognized via the
        # object-attribute path.
        self.assertIsNotNone(service.handle_task(task))

    def test_wait_planning_spawn_session_swallows_start_exception(self) -> None:
        # Lines 159-160: ``except Exception: self.logger.exception``.
        from kato_core_lib.data_layers.service.wait_planning_service import (
            WaitPlanningService, _PlanningContext,
        )
        from kato_core_lib.data_layers.data.task import Task
        manager = MagicMock()
        manager.start_session.side_effect = RuntimeError('spawn failed')
        service = WaitPlanningService(
            session_manager=manager,
            repository_service=MagicMock(),
            task_state_service=MagicMock(),
        )
        service.logger = MagicMock()
        # Must not raise.
        service._spawn_planning_session(
            Task(id='PROJ-1'),
            _PlanningContext(cwd='/tmp', expected_branch='feat/x'),
        )
        service.logger.exception.assert_called_once()

    def test_recovery_skips_non_directory_entries(self) -> None:
        # Line 114: ``if not entry.is_dir(): continue`` in _collect_orphan_directories.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'orphan').mkdir()
            (root / 'stray-file.txt').write_text('not a dir')
            workspace_manager = MagicMock()
            workspace_manager.root = root
            service = WorkspaceRecoveryService(
                workspace_manager=workspace_manager,
                task_service=MagicMock(),
                repository_service=MagicMock(),
            )
            orphans = service._collect_orphan_directories()
        # The file was skipped, only the directory is in the result.
        names = [o.name for o in orphans]
        self.assertIn('orphan', names)
        self.assertNotIn('stray-file.txt', names)

    def test_recovery_one_swallows_exception_in_loop(self) -> None:
        # Lines 97-101: exception inside the recovery loop is logged
        # + skipped rather than aborting the whole recovery pass.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            orphan = root / 'PROJ-1'
            (orphan / 'repo-a' / '.git').mkdir(parents=True)
            workspace_manager = MagicMock()
            workspace_manager.root = root
            task_service = MagicMock()
            task = SimpleNamespace(id='PROJ-1', summary='x')
            task_service.get_assigned_tasks.return_value = [task]
            task_service.get_review_tasks.return_value = []
            repo_service = MagicMock()
            # Raise inside _recover_one path.
            repo_service.resolve_task_repositories.side_effect = RuntimeError(
                'cannot resolve',
            )
            service = WorkspaceRecoveryService(
                workspace_manager=workspace_manager,
                task_service=task_service,
                repository_service=repo_service,
            )
            # Should not raise. Result is [] because the only orphan failed.
            result = service.recover_orphan_workspaces()
        self.assertEqual(result, [])

    def test_git_repository_subdirs_skips_non_dirs_and_non_git(self) -> None:
        # Line 197: ``if not entry.is_dir(): continue`` in _git_repository_subdirs.
        from kato_core_lib.data_layers.service.workspace_recovery_service import (
            WorkspaceRecoveryService,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'real-repo' / '.git').mkdir(parents=True)
            (root / 'not-a-git-dir').mkdir()
            (root / 'stray-file').write_text('x')
            names = WorkspaceRecoveryService._git_repository_subdirs(root)
        self.assertEqual(names, ['real-repo'])


if __name__ == '__main__':
    unittest.main()
