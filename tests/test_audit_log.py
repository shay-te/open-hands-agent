"""Tests for the append-only audit log + ``./kato history`` script.

Three surfaces:

1. ``audit_log.append_audit_event`` writes JSONL records and
   ``read_audit_records`` reads them back. Concurrent appends and
   malformed lines are tolerated.
2. The hook points (publisher success, review-fix success,
   failure-handler) actually call into the helper.
3. ``scripts/audit_log_query.main`` prints a numbered list of the
   most recent records (capped at the script's history limit) and
   shows a friendly empty-state when no records exist. The script
   intentionally has no flags — operators jq the JSONL directly
   for fine-grained filtering.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kato_core_lib.helpers.audit_log_utils import (
    AUDIT_LOG_PATH_ENV_KEY,
    EVENT_REVIEW_FIX_COMPLETED,
    EVENT_TASK_COMPLETED,
    EVENT_TASK_FAILED,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    append_audit_event,
    default_audit_log_path,
    read_audit_records,
)


def _tmp_path() -> tuple[tempfile.TemporaryDirectory, Path]:
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / 'audit.log.jsonl'
    return tmp, path


class HelperRoundTripTests(unittest.TestCase):
    """``append_audit_event`` + ``read_audit_records`` shape."""

    def test_appends_and_reads_a_single_record(self) -> None:
        tmp, path = _tmp_path()
        self.addCleanup(tmp.cleanup)
        append_audit_event(
            event=EVENT_TASK_COMPLETED,
            task_id='PROJ-1',
            ticket_summary='do the thing',
            repositories=['client', 'backend'],
            branch='feature/proj-1',
            pr_url='https://example.com/pr/17',
            outcome=OUTCOME_SUCCESS,
            path=path,
        )
        records = read_audit_records(path=path)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record['event'], EVENT_TASK_COMPLETED)
        self.assertEqual(record['task_id'], 'PROJ-1')
        self.assertEqual(record['ticket_summary'], 'do the thing')
        self.assertEqual(record['repositories'], ['client', 'backend'])
        self.assertEqual(record['branch'], 'feature/proj-1')
        self.assertEqual(record['pr_url'], 'https://example.com/pr/17')
        self.assertEqual(record['outcome'], OUTCOME_SUCCESS)
        self.assertEqual(record['error'], '')
        self.assertIn('timestamp', record)

    def test_multiple_appends_preserve_order(self) -> None:
        tmp, path = _tmp_path()
        self.addCleanup(tmp.cleanup)
        for n in range(3):
            append_audit_event(
                event=EVENT_TASK_COMPLETED,
                task_id=f'PROJ-{n}',
                outcome=OUTCOME_SUCCESS,
                path=path,
            )
        ids = [record['task_id'] for record in read_audit_records(path=path)]
        self.assertEqual(ids, ['PROJ-0', 'PROJ-1', 'PROJ-2'])

    def test_read_returns_empty_when_file_missing(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(
            read_audit_records(path=Path(tmp.name) / 'missing.jsonl'),
            [],
        )

    def test_malformed_line_is_skipped(self) -> None:
        tmp, path = _tmp_path()
        self.addCleanup(tmp.cleanup)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            'not valid json\n'
            + json.dumps({
                'event': EVENT_TASK_COMPLETED,
                'task_id': 'PROJ-2',
                'outcome': OUTCOME_SUCCESS,
            })
            + '\nstill not json\n',
            encoding='utf-8',
        )
        records = read_audit_records(path=path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['task_id'], 'PROJ-2')

    def test_non_dict_json_line_is_skipped(self) -> None:
        # Line 131: ``if isinstance(record, dict):`` — a JSON line
        # that parses cleanly but is a list / string / number must
        # be dropped, not appended as-is. Defensive guard: the
        # reader only knows how to print dict-shaped records.
        tmp, path = _tmp_path()
        self.addCleanup(tmp.cleanup)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(['not', 'a', 'dict']) + '\n'
            + json.dumps('also not a dict') + '\n'
            + json.dumps({
                'event': EVENT_TASK_COMPLETED,
                'task_id': 'PROJ-3',
                'outcome': OUTCOME_SUCCESS,
            }) + '\n',
            encoding='utf-8',
        )
        records = read_audit_records(path=path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['task_id'], 'PROJ-3')

    def test_default_path_uses_env_override(self) -> None:
        with patch.dict(
            os.environ,
            {AUDIT_LOG_PATH_ENV_KEY: '/tmp/kato-test-audit.jsonl'},
            clear=False,
        ):
            self.assertEqual(
                default_audit_log_path(),
                Path('/tmp/kato-test-audit.jsonl'),
            )

    def test_concurrent_appends_do_not_lose_lines(self) -> None:
        tmp, path = _tmp_path()
        self.addCleanup(tmp.cleanup)

        def worker(idx: int) -> None:
            append_audit_event(
                event=EVENT_TASK_COMPLETED,
                task_id=f'PROJ-{idx}',
                outcome=OUTCOME_SUCCESS,
                path=path,
            )

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        records = read_audit_records(path=path)
        self.assertEqual(len(records), 8)
        self.assertEqual(
            sorted(r['task_id'] for r in records),
            [f'PROJ-{n}' for n in range(8)],
        )

    def test_append_failure_does_not_raise(self) -> None:
        # Best-effort: a write failure must NOT bubble up because
        # observability is not a correctness gate.
        bogus = Path('/dev/null/kato-cannot-make-this/audit.jsonl')
        # Should not raise.
        append_audit_event(
            event=EVENT_TASK_COMPLETED,
            task_id='PROJ-1',
            outcome=OUTCOME_SUCCESS,
            path=bogus,
        )


class HookIntegrationTests(unittest.TestCase):
    """The 3 hook helpers actually call ``append_audit_event``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'audit.jsonl'
        self._env_patch = patch.dict(
            os.environ,
            {AUDIT_LOG_PATH_ENV_KEY: str(self.path)},
            clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_publisher_helper_records_completion(self) -> None:
        from kato_core_lib.data_layers.service.task_publisher import (
            _record_task_completed,
        )

        task = SimpleNamespace(id='PROJ-1', summary='do the thing')
        prepared = SimpleNamespace(
            repositories=[
                SimpleNamespace(id='client'),
                SimpleNamespace(id='backend'),
            ],
            branch_name='feature/proj-1',
        )
        pull_requests = [
            {'url': 'https://example.com/pr/17'},
            {'url': 'https://example.com/pr/18'},
        ]
        _record_task_completed(task, prepared, pull_requests)
        records = read_audit_records(path=self.path)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record['event'], EVENT_TASK_COMPLETED)
        self.assertEqual(record['task_id'], 'PROJ-1')
        self.assertEqual(record['repositories'], ['client', 'backend'])
        self.assertEqual(record['branch'], 'feature/proj-1')
        self.assertIn('https://example.com/pr/17', record['pr_url'])
        self.assertEqual(record['outcome'], OUTCOME_SUCCESS)

    def test_publisher_helper_records_when_prepared_task_is_none(self) -> None:
        # Branch 611->617: ``if prepared_task is not None`` is False — the
        # repositories/branch block is skipped and we go straight to the
        # PR-URL collection. The record still lands with empty repos.
        from kato_core_lib.data_layers.service.task_publisher import (
            _record_task_completed,
        )
        task = SimpleNamespace(id='PROJ-NONE', summary='ad-hoc fix')
        _record_task_completed(task, None, [{'url': 'https://x.example/pr/1'}])
        records = read_audit_records(path=self.path)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record['task_id'], 'PROJ-NONE')
        self.assertEqual(record['repositories'], [])
        self.assertEqual(record['branch'], '')
        self.assertIn('https://x.example/pr/1', record['pr_url'])

    def test_publisher_helper_skips_non_dict_and_blank_url_entries(self) -> None:
        # Branches 619->618 (entry not a dict → loop continues) and
        # 621->618 (entry is a dict but url is blank → no append,
        # loop continues). Only the dict-with-real-url entry contributes
        # to pr_url.
        from kato_core_lib.data_layers.service.task_publisher import (
            _record_task_completed,
        )
        task = SimpleNamespace(id='PROJ-MIXED', summary='')
        prepared = SimpleNamespace(
            repositories=[SimpleNamespace(id='client')],
            branch_name='feature/proj-mixed',
        )
        pull_requests = [
            'not a dict',                       # 619->618: skipped
            42,                                 # 619->618: skipped
            {'url': ''},                        # 621->618: blank → skipped
            {'url': None},                      # 621->618: falsy → skipped
            {'url': 'https://example.com/pr/9'},
        ]
        _record_task_completed(task, prepared, pull_requests)
        records = read_audit_records(path=self.path)
        self.assertEqual(len(records), 1)
        # Exactly one URL survives — the dict-with-real-url entry.
        self.assertEqual(records[0]['pr_url'], 'https://example.com/pr/9')

    def test_review_fix_helper_records_completion(self) -> None:
        from kato_core_lib.data_layers.service.review_comment_service import (
            _record_review_fix_completed,
        )

        comments = [
            SimpleNamespace(pull_request_id='17'),
            SimpleNamespace(pull_request_id='17'),
        ]
        review_context = SimpleNamespace(
            task_id='PROJ-1',
            task_summary='fix it',
            repository_id='client',
            branch_name='feature/proj-1',
        )
        _record_review_fix_completed(comments, review_context)
        records = read_audit_records(path=self.path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['event'], EVENT_REVIEW_FIX_COMPLETED)
        self.assertEqual(records[0]['task_id'], 'PROJ-1')
        self.assertEqual(records[0]['repositories'], ['client'])
        self.assertEqual(records[0]['pr_url'], '17')

    def test_failure_helper_records_failure_with_error(self) -> None:
        from kato_core_lib.data_layers.service.task_failure_handler import (
            _record_task_failed,
        )

        task = SimpleNamespace(id='PROJ-2', summary='broke it')
        prepared = SimpleNamespace(
            repositories=[SimpleNamespace(id='client')],
            branch_name='feature/proj-2',
        )
        _record_task_failed(task, RuntimeError('git push refused'), prepared)
        records = read_audit_records(path=self.path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['event'], EVENT_TASK_FAILED)
        self.assertEqual(records[0]['outcome'], OUTCOME_FAILURE)
        self.assertEqual(records[0]['error'], 'git push refused')

    def test_failure_helper_truncates_oversize_error(self) -> None:
        from kato_core_lib.data_layers.service.task_failure_handler import (
            _record_task_failed,
        )

        task = SimpleNamespace(id='PROJ-2', summary='')
        long_error = RuntimeError('x' * 5000)
        _record_task_failed(task, long_error, None)
        records = read_audit_records(path=self.path)
        self.assertEqual(len(records), 1)
        self.assertLessEqual(len(records[0]['error']), 500)


class CliQueryTests(unittest.TestCase):
    """``scripts/audit_log_query.main`` filtering + empty state."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'audit.jsonl'
        self._env_patch = patch.dict(
            os.environ,
            {AUDIT_LOG_PATH_ENV_KEY: str(self.path)},
            clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _seed(self):
        # 3 success + 2 failure across 2 tasks + 1 review-fix success.
        for n in range(3):
            append_audit_event(
                event=EVENT_TASK_COMPLETED,
                task_id=f'PROJ-{n}',
                ticket_summary=f'task {n}',
                outcome=OUTCOME_SUCCESS,
                path=self.path,
            )
        append_audit_event(
            event=EVENT_TASK_FAILED,
            task_id='PROJ-1',
            ticket_summary='task 1 retry',
            outcome=OUTCOME_FAILURE,
            error='boom',
            path=self.path,
        )
        append_audit_event(
            event=EVENT_TASK_FAILED,
            task_id='PROJ-99',
            outcome=OUTCOME_FAILURE,
            error='also boom',
            path=self.path,
        )
        append_audit_event(
            event=EVENT_REVIEW_FIX_COMPLETED,
            task_id='PROJ-0',
            outcome=OUTCOME_SUCCESS,
            path=self.path,
        )

    def _run(self, *argv) -> tuple[str, str, int]:
        from scripts import audit_log_query

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = audit_log_query.main(list(argv))
        return out_buf.getvalue(), err_buf.getvalue(), rc

    def test_empty_state_message_when_no_log(self) -> None:
        out, err, rc = self._run()
        self.assertEqual(rc, 0)
        self.assertIn('No kato history yet', err)
        self.assertEqual(out, '')

    def test_default_prints_every_record(self) -> None:
        self._seed()
        out, _, rc = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(out.count('PROJ-'), 6)

    def test_records_are_numbered_for_at_a_glance_indexing(self) -> None:
        # The numbered prefix is the entire UX hint that the operator
        # is looking at a list — without it, the view collapses back
        # to a wall of tab-separated lines that's hard to scan. Pin
        # the format so a future "compact" change doesn't quietly
        # drop the indices.
        self._seed()
        out, _, _ = self._run()
        self.assertIn('  1.\t', out)
        self.assertIn('  6.\t', out)


if __name__ == '__main__':
    unittest.main()
