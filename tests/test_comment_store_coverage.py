"""Coverage for ``LocalCommentStore`` defensive branches."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.comment_core_lib.comment_record import (
    CommentRecord,
    CommentSource,
    CommentStatus,
    KatoCommentStatus,
)
from kato_core_lib.comment_core_lib.comment_store import LocalCommentStore


def _record(
    comment_id='c1',
    body='hello',
    repo_id='repo-a',
    parent_id='',
    source=CommentSource.LOCAL.value,
    remote_id='',
    status=CommentStatus.OPEN.value,
    kato_status=KatoCommentStatus.IDLE.value,
):
    return CommentRecord(
        id=comment_id,
        body=body,
        repo_id=repo_id,
        author='alice',
        parent_id=parent_id,
        source=source,
        remote_id=remote_id,
        status=status,
        kato_status=kato_status,
        created_at_epoch=1000.0,
    )


class LocalCommentStoreDefensiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        self.store = LocalCommentStore(self.workspace)

    def test_storage_path_property(self) -> None:
        # Line 51.
        self.assertEqual(self.store.storage_path, self.workspace / '.kato-comments.json')

    def test_list_for_repo_returns_empty_for_blank_id(self) -> None:
        # Line 60.
        self.assertEqual(self.store.list_for_repo(''), [])
        self.assertEqual(self.store.list_for_repo('   '), [])

    def test_get_returns_none_for_blank_id(self) -> None:
        # Line 69.
        self.assertIsNone(self.store.get(''))

    def test_get_returns_none_when_not_found(self) -> None:
        self.assertIsNone(self.store.get('does-not-exist'))

    def test_add_rejects_blank_body(self) -> None:
        with self.assertRaisesRegex(ValueError, 'body must be non-empty'):
            self.store.add(_record(body=''))

    def test_add_rejects_blank_repo_id(self) -> None:
        with self.assertRaisesRegex(ValueError, 'repo_id must be non-empty'):
            self.store.add(_record(repo_id=''))

    def test_add_rejects_unknown_parent_id(self) -> None:
        with self.assertRaisesRegex(ValueError, 'parent comment'):
            self.store.add(_record(parent_id='nonexistent'))

    def test_upsert_remote_rejects_non_remote_source(self) -> None:
        # Line 110-112.
        with self.assertRaisesRegex(ValueError, 'source=remote'):
            self.store.upsert_remote(_record(source=CommentSource.LOCAL.value))

    def test_upsert_remote_rejects_blank_remote_id(self) -> None:
        # Line 114.
        with self.assertRaisesRegex(ValueError, 'remote_id is required'):
            self.store.upsert_remote(_record(
                source=CommentSource.REMOTE.value, remote_id='',
            ))

    def test_upsert_remote_preserves_kato_pipeline_fields_on_update(self) -> None:
        # Lines 122-130.
        record = _record(
            source=CommentSource.REMOTE.value, remote_id='r1',
            kato_status=KatoCommentStatus.IDLE.value,
        )
        record.kato_addressed_sha = 'sha-abc'
        record.kato_failure_reason = 'some prior failure'
        self.store.upsert_remote(record)
        # Re-sync the same remote comment — the new one comes in IDLE
        # again, but the store preserves the prior pipeline state.
        updated_record = _record(
            comment_id='c2',  # new local id ignored on remote upsert
            source=CommentSource.REMOTE.value, remote_id='r1',
            body='updated body',
        )
        result = self.store.upsert_remote(updated_record)
        self.assertEqual(result.kato_addressed_sha, 'sha-abc')

    def test_upsert_remote_skips_non_matching_records(self) -> None:
        """Covers branch 175->174: the predicate (matching remote_id +
        REMOTE source) is False on a pre-existing record, so the loop
        keeps scanning before falling through to the append path."""
        # A local record + a remote record with a different remote_id —
        # both should be skipped so the new remote record ends up appended.
        self.store.add(_record('c-local', source=CommentSource.LOCAL.value))
        self.store.upsert_remote(_record(
            'c-other', source=CommentSource.REMOTE.value, remote_id='other',
        ))
        # Now upsert a brand-new remote_id — neither existing record matches.
        new_record = _record(
            'c-new', source=CommentSource.REMOTE.value, remote_id='r-new',
        )
        self.store.upsert_remote(new_record)
        ids = [r.remote_id for r in self.store.list() if r.remote_id]
        self.assertIn('r-new', ids)
        self.assertIn('other', ids)

    def test_update_status_rejects_unknown_status(self) -> None:
        # Lines 148-152.
        self.store.add(_record())
        with self.assertRaisesRegex(ValueError, 'unknown comment status'):
            self.store.update_status('c1', status='bogus')

    def test_update_status_returns_none_for_unknown_comment(self) -> None:
        # Line 169: loop completes without match → None.
        self.assertIsNone(self.store.update_status('never-existed', status=CommentStatus.OPEN.value))

    def test_update_status_resolves_with_explicit_resolver(self) -> None:
        self.store.add(_record())
        result = self.store.update_status(
            'c1', status=CommentStatus.RESOLVED.value, resolved_by='bob',
        )
        self.assertEqual(result.resolved_by, 'bob')

    def test_update_status_open_clears_resolved_metadata(self) -> None:
        self.store.add(_record())
        # First resolve, then re-open.
        self.store.update_status(
            'c1', status=CommentStatus.RESOLVED.value, resolved_by='bob',
        )
        result = self.store.update_status('c1', status=CommentStatus.OPEN.value)
        self.assertEqual(result.resolved_by, '')
        self.assertEqual(result.resolved_at_epoch, 0.0)

    def test_update_kato_status_rejects_unknown_value(self) -> None:
        self.store.add(_record())
        with self.assertRaisesRegex(ValueError, 'unknown kato_status'):
            self.store.update_kato_status('c1', kato_status='bogus')

    def test_update_kato_status_returns_none_for_unknown_id(self) -> None:
        # Line 204.
        self.assertIsNone(
            self.store.update_kato_status(
                'never', kato_status=KatoCommentStatus.IDLE.value,
            ),
        )

    def test_update_kato_status_clears_failure_reason_when_idle(self) -> None:
        # Lines 197-200.
        record = _record()
        record.kato_failure_reason = 'prior failure'
        self.store.add(record)
        # Move to QUEUED with no failure reason first.
        self.store.update_kato_status(
            'c1', kato_status=KatoCommentStatus.QUEUED.value,
        )
        # Move to IDLE — failure reason gets cleared.
        result = self.store.update_kato_status(
            'c1', kato_status=KatoCommentStatus.IDLE.value,
        )
        self.assertEqual(result.kato_failure_reason, '')

    def test_update_kato_status_records_addressed_sha(self) -> None:
        # Line 195: ``if addressed_sha: ...``.
        self.store.add(_record())
        result = self.store.update_kato_status(
            'c1', kato_status=KatoCommentStatus.ADDRESSED.value,
            addressed_sha='abc123',
        )
        self.assertEqual(result.kato_addressed_sha, 'abc123')

    def test_delete_removes_record_and_reply_chain(self) -> None:
        # Lines 218-228: deletes parent AND its replies.
        self.store.add(_record('p1'))
        self.store.add(_record('c2', parent_id='p1'))
        self.store.add(_record('c3'))  # unrelated
        removed = self.store.delete('p1')
        self.assertTrue(removed)
        # Only c3 remains.
        remaining_ids = [r.id for r in self.store.list()]
        self.assertEqual(remaining_ids, ['c3'])

    def test_delete_returns_false_for_unknown_id(self) -> None:
        self.assertFalse(self.store.delete('never-existed'))

    def test_load_all_returns_empty_when_file_missing(self) -> None:
        # The file doesn't exist on a fresh workspace.
        self.assertEqual(self.store.list(), [])

    def test_load_all_returns_empty_on_unreadable_file(self) -> None:
        # Lines 267-272.
        self.store.storage_path.write_text('not valid json')
        with patch.object(self.store, 'logger') as logger:
            result = self.store.list()
        self.assertEqual(result, [])
        logger.warning.assert_called()

    def test_load_all_returns_empty_for_non_dict_payload(self) -> None:
        # Lines 273-274.
        self.store.storage_path.write_text(
            json.dumps(['list', 'instead', 'of', 'dict']),
        )
        self.assertEqual(self.store.list(), [])

    def test_load_all_returns_empty_when_rows_not_list(self) -> None:
        # Lines 276-277.
        self.store.storage_path.write_text(
            json.dumps({'comments': 'not a list'}),
        )
        self.assertEqual(self.store.list(), [])

    def test_load_all_skips_non_dict_entries(self) -> None:
        # Lines 280-281.
        self.store.storage_path.write_text(
            json.dumps({
                'comments': [
                    'not a dict', 42, {'id': 'c1', 'body': 'real',
                                       'repo_id': 'r', 'source': 'local',
                                       'author': 'a'},
                ],
            }),
        )
        result = self.store.list()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, 'c1')

    def test_load_all_skips_malformed_records(self) -> None:
        # Lines 284-288: ``from_dict`` raises (TypeError/ValueError)
        # → log + skip. We drive this by giving a payload where a
        # numeric field can't be coerced.
        self.store.storage_path.write_text(
            json.dumps({
                'comments': [
                    {'id': 'c1', 'line': 'not-an-int'},  # int() will raise
                ],
            }),
        )
        with patch.object(self.store, 'logger') as logger:
            result = self.store.list()
        self.assertEqual(result, [])
        logger.warning.assert_called()


class LocalCommentStoreSkipBranchesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)
        self.store = LocalCommentStore(self.workspace)

    def test_update_status_skips_non_matching_ids(self) -> None:
        # Line 157: ``if current.id != comment_id: continue`` —
        # multiple records, only one matches.
        self.store.add(_record('c1'))
        self.store.add(_record('c2'))
        result = self.store.update_status(
            'c2', status=CommentStatus.RESOLVED.value,
        )
        self.assertEqual(result.id, 'c2')
        self.assertEqual(result.status, CommentStatus.RESOLVED.value)

    def test_update_kato_status_records_failure_reason(self) -> None:
        # Lines 196-197: ``if failure_reason: ...``.
        self.store.add(_record('c1'))
        result = self.store.update_kato_status(
            'c1', kato_status=KatoCommentStatus.FAILED.value,
            failure_reason='agent timed out',
        )
        self.assertEqual(result.kato_failure_reason, 'agent timed out')

    def test_next_queued_returns_none_when_empty(self) -> None:
        # Lines 254-255: ``if not queued: return None``.
        self.store.add(_record('c1', kato_status=KatoCommentStatus.IDLE.value))
        self.assertIsNone(self.store.next_queued())

    def test_next_queued_returns_oldest(self) -> None:
        # The success path — multiple queued, return the oldest.
        queued_old = _record('c1', kato_status=KatoCommentStatus.QUEUED.value)
        queued_old.created_at_epoch = 1000.0
        queued_new = _record('c2', kato_status=KatoCommentStatus.QUEUED.value)
        queued_new.created_at_epoch = 2000.0
        self.store.add(queued_old)
        self.store.add(queued_new)
        result = self.store.next_queued()
        self.assertEqual(result.id, 'c1')


if __name__ == '__main__':
    unittest.main()
