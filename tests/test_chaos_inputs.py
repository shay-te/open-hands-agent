"""Chaos / fuzz inputs against the REAL comment + workspace stack.

What real users / operators actually push at kato that the old test
suite never exercised:

  * 100KB stack traces pasted into a comment body
  * unicode soup: emoji, RTL marks, zero-width joiners, mixed scripts
  * NULs and other control chars
  * SQL-injection-shaped strings (kato is not a database, but the
    JSON serialiser shouldn't choke on quotes)
  * weird task ids — case mix, hyphens, underscores
  * partial workspace states: missing metadata, garbage JSON

No mocks here for the actual store / workspace path. Only the
ever-required upstream services on AgentService are mocked because
they'd need HTTP creds to build.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.comment_core_lib import (
    CommentRecord,
    CommentSource,
    KatoCommentStatus,
    LocalCommentStore,
)

from tests.chaos_lib import (
    CHAOS_BODIES,
    CHAOS_TASK_IDS_SAFE,
    CONTROL_CHARS,
    EMOJI_HEAVY,
    HUGE_BODY,
    IMPATIENT_BODIES,
    IMPATIENT_TITLES,
    MIXED_LANGUAGE,
    NESTED_EMOJI,
    PATH_TRAVERSAL,
    RTL_INJECTION,
    SQL_INJECTION,
    WEIRD_QUOTES,
    ZERO_WIDTH_SOUP,
    build_real_agent_service,
    materialize_workspace,
    queue_real_comment,
    real_store_for,
)


class CommentBodyChaosRoundTripTests(unittest.TestCase):
    """Every chaos body must round-trip through real disk JSON intact."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-chaos-body-')
        self.addCleanup(self._tmp.cleanup)
        workspace = Path(self._tmp.name) / 'ws'
        workspace.mkdir()
        self.store = LocalCommentStore(workspace)

    def test_every_chaos_body_round_trips(self) -> None:
        for body in CHAOS_BODIES:
            with self.subTest(preview=body[:40]):
                self.store.add(CommentRecord(
                    repo_id='r', body=body, author='op',
                    source=CommentSource.LOCAL.value,
                ))
        live_bodies = [c.body for c in self.store.list()]
        for body in CHAOS_BODIES:
            self.assertIn(body, live_bodies)

    def test_100kb_body_round_trip(self) -> None:
        record = self.store.add(CommentRecord(
            repo_id='r', body=HUGE_BODY, author='op',
            source=CommentSource.LOCAL.value,
        ))
        live = self.store.get(record.id)
        self.assertIsNotNone(live)
        self.assertEqual(live.body, HUGE_BODY)
        self.assertGreater(len(live.body), 80_000)

    def test_nul_char_does_not_break_json_writer(self) -> None:
        # JSON serialiser must encode ; the round-trip should
        # preserve the exact same string.
        self.store.add(CommentRecord(
            repo_id='r', body=CONTROL_CHARS, author='op',
            source=CommentSource.LOCAL.value,
        ))
        self.assertEqual(self.store.list()[0].body, CONTROL_CHARS)

    def test_sql_injection_body_is_just_text(self) -> None:
        # No SQL involved — but the JSON serialiser shouldn't mangle
        # nested quotes / semicolons. (And anyone grep-ing the JSON
        # store later sees the raw text, not a parser exception.)
        self.store.add(CommentRecord(
            repo_id='r', body=SQL_INJECTION, author='op',
            source=CommentSource.LOCAL.value,
        ))
        on_disk_text = (self.store.storage_path).read_text(encoding='utf-8')
        # The serialised JSON is valid JSON.
        parsed = json.loads(on_disk_text)
        self.assertEqual(
            parsed['comments'][0]['body'], SQL_INJECTION,
        )

    def test_mixed_language_body_is_preserved(self) -> None:
        # Multi-script: latin + CJK + arabic + cyrillic. Real users
        # paste this kind of comment in shared projects.
        self.store.add(CommentRecord(
            repo_id='r', body=MIXED_LANGUAGE, author='op',
            source=CommentSource.LOCAL.value,
        ))
        self.assertEqual(self.store.list()[0].body, MIXED_LANGUAGE)

    def test_emoji_heavy_body_round_trip(self) -> None:
        for body in (EMOJI_HEAVY, NESTED_EMOJI):
            store_path = Path(self._tmp.name) / f'ws-emoji-{id(body)}'
            store_path.mkdir()
            store = LocalCommentStore(store_path)
            store.add(CommentRecord(
                repo_id='r', body=body, author='op',
                source=CommentSource.LOCAL.value,
            ))
            self.assertEqual(store.list()[0].body, body)

    def test_rtl_and_zero_width_chars_round_trip(self) -> None:
        for body in (RTL_INJECTION, ZERO_WIDTH_SOUP, WEIRD_QUOTES):
            store_path = Path(self._tmp.name) / f'ws-bidi-{id(body)}'
            store_path.mkdir()
            store = LocalCommentStore(store_path)
            store.add(CommentRecord(
                repo_id='r', body=body, author='op',
                source=CommentSource.LOCAL.value,
            ))
            self.assertEqual(store.list()[0].body, body)

    def test_empty_and_whitespace_only_bodies_are_rejected(self) -> None:
        # LocalCommentStore explicitly rejects empty / whitespace —
        # this is the documented contract. Stress: the IMPATIENT_BODIES
        # set INCLUDES '' and '  ' on purpose, so we make sure that
        # filtering them out upstream is the right behaviour.
        empties = [b for b in IMPATIENT_BODIES if not b.strip()]
        self.assertGreater(len(empties), 0,
                           'fixture broke: should contain empty bodies')
        for body in empties:
            with self.subTest(body=repr(body)):
                with self.assertRaises(ValueError):
                    self.store.add(CommentRecord(
                        repo_id='r', body=body, author='op',
                        source=CommentSource.LOCAL.value,
                    ))


class TaskIdChaosWorkspaceTests(unittest.TestCase):
    """Real workspaces created with chaos-y task ids that operators actually use."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-chaos-task-')
        self.addCleanup(self._tmp.cleanup)
        self.service, self.workspace_service = build_real_agent_service(
            Path(self._tmp.name),
        )

    def test_all_safe_chaos_task_ids_materialize_and_list(self) -> None:
        for task_id in CHAOS_TASK_IDS_SAFE:
            materialize_workspace(self.workspace_service, task_id)
        listed = {w.task_id for w in self.service._safe_list_workspaces()}
        for task_id in CHAOS_TASK_IDS_SAFE:
            self.assertIn(task_id, listed)

    def test_path_traversal_task_id_is_sanitized_under_root(self) -> None:
        # The workspace data-access layer SANITISES (not rejects) path
        # separators — a "../../etc/passwd" id ends up as a single
        # folder inside the workspace root, not an escape. Defense
        # in depth: the operator gets a working workspace, the
        # filesystem stays safe.
        record = materialize_workspace(self.workspace_service, PATH_TRAVERSAL)
        actual_path = self.workspace_service.workspace_path(record.task_id)
        # Resolves cleanly under the workspace root.
        self.assertTrue(
            actual_path.resolve().is_relative_to(
                self.workspace_service.root.resolve(),
            ),
            f'sanitised path {actual_path} escaped workspace root',
        )
        # The sanitised task_id has no path separators left.
        self.assertNotIn('/', record.task_id)
        self.assertNotIn('\\', record.task_id)

    def test_blank_task_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            materialize_workspace(self.workspace_service, '')

    def test_drain_handles_each_chaos_task_id(self) -> None:
        # Queue an impatient-string comment for every safe chaos id,
        # then run a real drain. Every comment must dispatch.
        for task_id in CHAOS_TASK_IDS_SAFE:
            materialize_workspace(self.workspace_service, task_id)
            queue_real_comment(
                self.workspace_service, task_id,
                body=f'fix it {task_id} now',
            )
        with patch.object(self.service, '_run_comment_agent', return_value=True):
            results = self.service.drain_all_queued_task_comments()
        dispatched = {r['task_id'] for r in results}
        for task_id in CHAOS_TASK_IDS_SAFE:
            self.assertIn(task_id, dispatched)


class BrokenWorkspaceRecoveryTests(unittest.TestCase):
    """Partial / corrupted workspace state must NOT crash the scan loop."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-broken-ws-')
        self.addCleanup(self._tmp.cleanup)
        self.service, self.workspace_service = build_real_agent_service(
            Path(self._tmp.name),
        )

    def test_drain_survives_a_workspace_with_garbage_comment_file(self) -> None:
        # Healthy workspace + broken-store workspace side by side.
        materialize_workspace(self.workspace_service, 'GOOD-1')
        queue_real_comment(self.workspace_service, 'GOOD-1', body='do it')
        materialize_workspace(self.workspace_service, 'BAD-1')
        bad_path = real_store_for(self.workspace_service, 'BAD-1').storage_path
        bad_path.write_text('NOT EVEN JSON !!!', encoding='utf-8')

        with patch.object(self.service, '_run_comment_agent', return_value=True):
            results = self.service.drain_all_queued_task_comments()
        # The healthy workspace still drained.
        self.assertEqual([r['task_id'] for r in results], ['GOOD-1'])

    def test_workspace_folder_without_metadata_does_not_crash_listing(self) -> None:
        # Operator manually created a folder under the workspace root
        # but no metadata file. workspace_service.list_all returns a
        # synthetic ``errored`` record; scan loop must keep going.
        rogue = self.workspace_service.workspace_path('ROGUE-1')
        rogue.mkdir(parents=True)
        # No metadata file written.
        materialize_workspace(self.workspace_service, 'GOOD-2')
        queue_real_comment(self.workspace_service, 'GOOD-2',
                           body='whats wrong with you')

        with patch.object(self.service, '_run_comment_agent', return_value=True):
            results = self.service.drain_all_queued_task_comments()
        # GOOD-2 drained; ROGUE-1 was listed but had no store, no crash.
        ok_ids = [r['task_id'] for r in results if r.get('started')]
        self.assertIn('GOOD-2', ok_ids)

    def test_requeue_skips_workspace_with_unreadable_store(self) -> None:
        # Mirror of test_drain_survives_..., for the boot-recovery path.
        materialize_workspace(self.workspace_service, 'GOOD-1')
        store = real_store_for(self.workspace_service, 'GOOD-1')
        record = store.add(CommentRecord(
            repo_id='r1', body='ok do it', author='op',
            source=CommentSource.LOCAL.value,
            kato_status=KatoCommentStatus.IN_PROGRESS.value,
        ))
        materialize_workspace(self.workspace_service, 'BAD-1')
        bad_path = real_store_for(self.workspace_service, 'BAD-1').storage_path
        bad_path.write_text('}}}}}', encoding='utf-8')

        requeued = self.service.requeue_stuck_in_progress_comments()
        # Only the good workspace was successfully requeued.
        self.assertEqual(requeued, [{'task_id': 'GOOD-1',
                                     'comment_id': record.id}])


class TaskTitleChaosTests(unittest.TestCase):
    """Impatient-title round-trip through WorkspaceRecord."""

    def test_every_impatient_title_survives_workspace_create(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='kato-chaos-titles-')
        self.addCleanup(tmp.cleanup)
        service, workspace_service = build_real_agent_service(Path(tmp.name))

        for i, title in enumerate(IMPATIENT_TITLES):
            task_id = f'IMP-{i}'
            workspace_service.create(
                task_id=task_id,
                task_summary=title,
                repository_ids=['repo-a'],
            )
        # Re-read every record and confirm the summary survived.
        listed = {w.task_id: w.task_summary
                  for w in service._safe_list_workspaces()}
        for i, title in enumerate(IMPATIENT_TITLES):
            self.assertEqual(listed[f'IMP-{i}'], title)


if __name__ == '__main__':
    unittest.main()
