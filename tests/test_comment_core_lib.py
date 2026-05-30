"""Coverage for the comment-core-lib JSON store.

Locks the public API the agent_service hooks rely on: add (with body
+ repo + parent validation), upsert_remote (dedup by remote_id +
preserve kato pipeline fields), update_status (resolve / reopen),
update_kato_status (queue / in_progress / addressed), delete (with
reply chain cleanup), and the queue helpers.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kato_core_lib.comment_core_lib import (
    CommentRecord,
    CommentSource,
    CommentStatus,
    KatoCommentStatus,
    LocalCommentStore,
)


class LocalCommentStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace_dir = Path(self._tmp.name)
        self.store = LocalCommentStore(self.workspace_dir)

    def test_add_persists_a_record_with_required_fields_filled_in(self) -> None:
        record = CommentRecord(
            repo_id='admin-client',
            file_path='src/auth.py',
            line=42,
            body='please rename this variable',
        )
        persisted = self.store.add(record)
        # ``id`` and ``created_at_epoch`` get auto-filled.
        self.assertTrue(persisted.id)
        self.assertGreater(persisted.created_at_epoch, 0)
        self.assertEqual(persisted.kato_status, KatoCommentStatus.IDLE.value)
        self.assertEqual(persisted.source, CommentSource.LOCAL.value)
        # File on disk has the comment under a top-level
        # ``comments`` key — the wire shape the SSE / API
        # endpoints rely on.
        on_disk = json.loads(
            (self.workspace_dir / '.kato-comments.json').read_text(encoding='utf-8'),
        )
        self.assertIn('comments', on_disk)
        self.assertEqual(len(on_disk['comments']), 1)

    def test_add_rejects_empty_body_and_missing_repo(self) -> None:
        with self.assertRaisesRegex(ValueError, 'body must be non-empty'):
            self.store.add(CommentRecord(repo_id='r', body='   '))
        with self.assertRaisesRegex(ValueError, 'repo_id must be non-empty'):
            self.store.add(CommentRecord(body='something'))

    def test_add_reply_requires_existing_parent(self) -> None:
        with self.assertRaisesRegex(ValueError, 'parent comment'):
            self.store.add(CommentRecord(
                repo_id='r', body='reply', parent_id='nope',
            ))

    def test_list_for_repo_filters_case_insensitively(self) -> None:
        self.store.add(CommentRecord(repo_id='Admin-Client', body='one'))
        self.store.add(CommentRecord(repo_id='backend', body='two'))
        self.assertEqual(
            len(self.store.list_for_repo('admin-client')), 1,
        )
        self.assertEqual(len(self.store.list_for_repo('backend')), 1)
        self.assertEqual(len(self.store.list()), 2)

    def test_upsert_remote_dedupes_by_remote_id_and_preserves_kato_fields(self) -> None:
        # Initial sync: insert.
        first = self.store.upsert_remote(CommentRecord(
            repo_id='r', body='first',
            source=CommentSource.REMOTE.value, remote_id='abc',
        ))
        # Kato addresses the comment.
        self.store.update_kato_status(
            first.id,
            kato_status=KatoCommentStatus.ADDRESSED.value,
            addressed_sha='deadbeef',
        )
        # Re-sync: same remote_id, edited body. Kato fields
        # (status + sha) must survive the upsert so a fix isn't
        # forgotten the next time the operator pulls comments.
        self.store.upsert_remote(CommentRecord(
            repo_id='r', body='first (edited)',
            source=CommentSource.REMOTE.value, remote_id='abc',
        ))
        records = self.store.list()
        self.assertEqual(len(records), 1, 'should not duplicate by remote_id')
        self.assertEqual(records[0].body, 'first (edited)')
        self.assertEqual(records[0].kato_status, KatoCommentStatus.ADDRESSED.value)
        self.assertEqual(records[0].kato_addressed_sha, 'deadbeef')

    def test_upsert_remote_refuses_local_records(self) -> None:
        with self.assertRaisesRegex(ValueError, 'source=remote'):
            self.store.upsert_remote(CommentRecord(
                repo_id='r', body='x', source=CommentSource.LOCAL.value,
                remote_id='abc',
            ))

    def test_update_status_resolves_and_reopens_with_audit_fields(self) -> None:
        record = self.store.add(CommentRecord(repo_id='r', body='x'))
        resolved = self.store.update_status(
            record.id,
            status=CommentStatus.RESOLVED.value,
            resolved_by='shay',
        )
        self.assertEqual(resolved.status, CommentStatus.RESOLVED.value)
        self.assertEqual(resolved.resolved_by, 'shay')
        self.assertGreater(resolved.resolved_at_epoch, 0)
        # Reopen clears the audit fields so the next resolve is
        # treated as a fresh action.
        reopened = self.store.update_status(
            record.id, status=CommentStatus.OPEN.value,
        )
        self.assertEqual(reopened.status, CommentStatus.OPEN.value)
        self.assertEqual(reopened.resolved_by, '')
        self.assertEqual(reopened.resolved_at_epoch, 0.0)

    def test_update_kato_status_validates_value(self) -> None:
        record = self.store.add(CommentRecord(repo_id='r', body='x'))
        with self.assertRaisesRegex(ValueError, 'unknown kato_status'):
            self.store.update_kato_status(record.id, kato_status='bogus')

    def test_delete_removes_replies_and_reply_chains(self) -> None:
        root = self.store.add(CommentRecord(repo_id='r', body='root'))
        reply = self.store.add(CommentRecord(
            repo_id='r', body='reply', parent_id=root.id,
        ))
        nested = self.store.add(CommentRecord(
            repo_id='r', body='nested', parent_id=reply.id,
        ))
        # Sanity.
        self.assertEqual(len(self.store.list()), 3)
        self.assertTrue(self.store.delete(root.id))
        # Root + every descendant reply gone in one hit.
        self.assertEqual(len(self.store.list()), 0)
        # Verify each is unfindable individually.
        for comment_id in (root.id, reply.id, nested.id):
            self.assertIsNone(self.store.get(comment_id))

    def test_queue_helpers_return_oldest_queued_first(self) -> None:
        first = self.store.add(CommentRecord(repo_id='r', body='one'))
        second = self.store.add(CommentRecord(repo_id='r', body='two'))
        # Force the first to be older than the second so the FIFO
        # order is unambiguous (creation timestamps could collide
        # within the same millisecond on a fast machine).
        first.created_at_epoch = 100.0
        second.created_at_epoch = 200.0
        self.store._persist([first, second])  # noqa: SLF001 — test is fine
        self.store.update_kato_status(
            first.id, kato_status=KatoCommentStatus.QUEUED.value,
        )
        self.store.update_kato_status(
            second.id, kato_status=KatoCommentStatus.QUEUED.value,
        )
        next_up = self.store.next_queued()
        self.assertEqual(next_up.id, first.id)


if __name__ == '__main__':
    unittest.main()
