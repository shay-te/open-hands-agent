"""Round-trip + serialization tests for :class:`WorkspaceRecord`."""

from __future__ import annotations

import unittest

from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
    SUPPORTED_WORKSPACE_STATUSES,
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_DONE,
    WORKSPACE_STATUS_ERRORED,
    WORKSPACE_STATUS_PROVISIONING,
    WORKSPACE_STATUS_REVIEW,
    WORKSPACE_STATUS_TERMINATED,
    WorkspaceRecord,
)


class WorkspaceRecordTests(unittest.TestCase):
    def test_to_dict_round_trip_preserves_every_field(self) -> None:
        original = WorkspaceRecord(
            task_id='PROJ-9',
            task_summary='roundtrip test',
            status=WORKSPACE_STATUS_DONE,
            repository_ids=['repo1', 'repo2'],
            agent_session_id='sess-uuid',
            cwd='/tmp/work',
            resume_on_startup=False,
            created_at_epoch=100.0,
            updated_at_epoch=200.0,
        )
        round_trip = WorkspaceRecord.from_dict(original.to_dict())
        self.assertEqual(round_trip, original)

    def test_from_dict_accepts_legacy_claude_session_id_key(self) -> None:
        # Pre-rename deployments persisted the agent session id under
        # ``claude_session_id``. Read-side compat: we accept that key
        # so existing on-disk data loads without a migration script.
        legacy_payload = {
            'task_id': 'PROJ-1',
            'claude_session_id': 'legacy-sess-id',
        }
        record = WorkspaceRecord.from_dict(legacy_payload)
        self.assertEqual(record.agent_session_id, 'legacy-sess-id')

    def test_from_dict_prefers_new_key_when_both_present(self) -> None:
        payload = {
            'task_id': 'PROJ-1',
            'agent_session_id': 'new-id',
            'claude_session_id': 'legacy-id',
        }
        record = WorkspaceRecord.from_dict(payload)
        self.assertEqual(record.agent_session_id, 'new-id')

    def test_from_dict_falls_back_when_new_key_is_whitespace(self) -> None:
        payload = {
            'task_id': 'PROJ-1',
            'agent_session_id': '   ',
            'claude_session_id': 'legacy-id',
        }
        record = WorkspaceRecord.from_dict(payload)
        self.assertEqual(record.agent_session_id, 'legacy-id')

    def test_from_dict_strips_session_id_and_cwd(self) -> None:
        record = WorkspaceRecord.from_dict({
            'task_id': 'PROJ-1',
            'agent_session_id': '  sess-uuid\n',
            'cwd': '  /tmp/work  ',
        })
        self.assertEqual(record.agent_session_id, 'sess-uuid')
        self.assertEqual(record.cwd, '/tmp/work')

    def test_to_dict_uses_new_key_only(self) -> None:
        # Write-side: every persisted record uses the canonical name.
        # No ``claude_session_id`` gets written ever; legacy callers
        # are expected to migrate over time.
        record = WorkspaceRecord(
            task_id='PROJ-1', agent_session_id='abc',
        )
        payload = record.to_dict()
        self.assertIn('agent_session_id', payload)
        self.assertNotIn('claude_session_id', payload)

    def test_from_dict_tolerates_missing_optional_fields(self) -> None:
        # Hand-edited or partial payloads: only ``task_id`` is required.
        record = WorkspaceRecord.from_dict({'task_id': 'PROJ-1'})
        self.assertEqual(record.task_id, 'PROJ-1')
        self.assertEqual(record.task_summary, '')
        self.assertEqual(record.status, WORKSPACE_STATUS_PROVISIONING)
        self.assertEqual(record.repository_ids, [])
        self.assertEqual(record.agent_session_id, '')

    def test_from_dict_drops_non_string_repository_ids(self) -> None:
        record = WorkspaceRecord.from_dict({
            'task_id': 'PROJ-1',
            'repository_ids': ['ok', '', None, 'also-ok'],
        })
        self.assertEqual(record.repository_ids, ['ok', 'also-ok'])

    def test_from_dict_handles_invalid_repository_ids_field(self) -> None:
        # If the on-disk JSON has been corrupted to a non-list, treat
        # it as empty rather than raising — load must never crash.
        record = WorkspaceRecord.from_dict({
            'task_id': 'PROJ-1',
            'repository_ids': 'not-a-list',
        })
        self.assertEqual(record.repository_ids, [])

    def test_status_constants_are_in_supported_set(self) -> None:
        for status in (
            WORKSPACE_STATUS_PROVISIONING,
            WORKSPACE_STATUS_ACTIVE,
            WORKSPACE_STATUS_REVIEW,
            WORKSPACE_STATUS_DONE,
            WORKSPACE_STATUS_ERRORED,
            WORKSPACE_STATUS_TERMINATED,
        ):
            self.assertIn(status, SUPPORTED_WORKSPACE_STATUSES)


class WorkspaceStatusValuesTests(unittest.TestCase):
    def test_provisioning_value(self) -> None:
        self.assertEqual(WORKSPACE_STATUS_PROVISIONING, 'provisioning')

    def test_active_value(self) -> None:
        self.assertEqual(WORKSPACE_STATUS_ACTIVE, 'active')

    def test_review_value(self) -> None:
        self.assertEqual(WORKSPACE_STATUS_REVIEW, 'review')

    def test_done_value(self) -> None:
        self.assertEqual(WORKSPACE_STATUS_DONE, 'done')

    def test_errored_value(self) -> None:
        self.assertEqual(WORKSPACE_STATUS_ERRORED, 'errored')

    def test_terminated_value(self) -> None:
        self.assertEqual(WORKSPACE_STATUS_TERMINATED, 'terminated')

    def test_supported_statuses_count_is_six(self) -> None:
        self.assertEqual(len(SUPPORTED_WORKSPACE_STATUSES), 6)


class WorkspaceRecordFromDictFieldsTest(unittest.TestCase):
    def test_from_dict_sets_cwd(self) -> None:
        record = WorkspaceRecord.from_dict({'task_id': 'T1', 'cwd': '/some/path'})
        self.assertEqual(record.cwd, '/some/path')

    def test_from_dict_sets_resume_on_startup_false(self) -> None:
        record = WorkspaceRecord.from_dict(
            {'task_id': 'T1', 'resume_on_startup': False},
        )
        self.assertFalse(record.resume_on_startup)

    def test_from_dict_sets_resume_on_startup_defaults_true(self) -> None:
        record = WorkspaceRecord.from_dict({'task_id': 'T1'})
        self.assertTrue(record.resume_on_startup)

    def test_from_dict_sets_created_at_epoch(self) -> None:
        record = WorkspaceRecord.from_dict(
            {'task_id': 'T1', 'created_at_epoch': 1234567890.5},
        )
        self.assertAlmostEqual(record.created_at_epoch, 1234567890.5)

    def test_from_dict_sets_updated_at_epoch(self) -> None:
        record = WorkspaceRecord.from_dict(
            {'task_id': 'T1', 'updated_at_epoch': 9999999.0},
        )
        self.assertAlmostEqual(record.updated_at_epoch, 9999999.0)

    def test_from_dict_sets_task_summary(self) -> None:
        record = WorkspaceRecord.from_dict(
            {'task_id': 'T1', 'task_summary': 'my task'},
        )
        self.assertEqual(record.task_summary, 'my task')

    def test_from_dict_sets_status(self) -> None:
        record = WorkspaceRecord.from_dict(
            {'task_id': 'T1', 'status': WORKSPACE_STATUS_REVIEW},
        )
        self.assertEqual(record.status, WORKSPACE_STATUS_REVIEW)

    def test_from_dict_sets_repository_ids(self) -> None:
        record = WorkspaceRecord.from_dict(
            {'task_id': 'T1', 'repository_ids': ['r1', 'r2']},
        )
        self.assertEqual(record.repository_ids, ['r1', 'r2'])


if __name__ == '__main__':
    unittest.main()
