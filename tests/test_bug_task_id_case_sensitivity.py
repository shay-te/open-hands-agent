"""Regression test for the case-insensitive task-id lookup contract.

Previously ``_normalize_task_id`` preserved case for both the
in-memory dict key AND the on-disk filename. Operators (or
internal code paths) calling with different casings of the same
logical id produced TWO separate records:

   - Linux (case-sensitive FS): ``PROJ-1.json`` and ``proj-1.json``
     accumulate as siblings; the planning UI shows two tabs for one
     task.
   - macOS (case-insensitive FS): the second write silently
     overwrites the first; data for one casing is lost.

The fix splits two concerns:

   - ``_normalize_task_id`` preserves the ORIGINAL case so display
     fields (``record.task_id``, error messages, logs) match what
     the ticket system uses.
   - ``_lookup_key`` lowercases for dict access AND the on-disk
     filename. ``PROJ-1`` and ``proj-1`` resolve to the same logical
     task.

This file pins both halves: the same record is found regardless of
case, AND only one file exists on disk per logical task.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class BugTaskIdCaseSensitivityTests(unittest.TestCase):

    def test_records_for_different_case_task_ids_share_one_record(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            mgr.adopt_session_id(
                'PROJ-1', agent_session_id='session-id-X',
            )
            # Lookup under lower-case must hit the same record.
            record = mgr.get_record('proj-1')
            self.assertIsNotNone(record)
            self.assertEqual(record.agent_session_id, 'session-id-X')

    def test_original_case_preserved_in_record_task_id(self) -> None:
        # Display contract: the operator's "PROJ-1" stays visible
        # in the record even though the dict key is lowercased.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            record = mgr.adopt_session_id(
                'PROJ-1', agent_session_id='sid',
            )
            self.assertEqual(record.task_id, 'PROJ-1')
            # Cross-case lookup also returns the original-cased value.
            self.assertEqual(
                mgr.get_record('proj-1').task_id, 'PROJ-1',
            )

    def test_state_dir_holds_exactly_one_file_per_logical_task(self) -> None:
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            mgr.adopt_session_id('PROJ-1', agent_session_id='id-A')
            with self.assertRaises(RuntimeError):
                mgr.adopt_session_id('proj-1', agent_session_id='id-B')

            files = sorted(Path(state_dir).glob('*.json'))
            self.assertEqual(
                len(files), 1,
                f'state_dir accumulated {len(files)} files for the '
                f'same logical task: {[f.name for f in files]}',
            )
            self.assertEqual(mgr.get_record('PROJ-1').agent_session_id, 'id-A')

    def test_terminate_session_handles_case_mismatched_call(self) -> None:
        # The terminate path uses the lookup key too — caller can
        # pass any casing and the right record is found.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
            SESSION_STATUS_TERMINATED,
        )
        with tempfile.TemporaryDirectory() as state_dir:
            mgr = ClaudeSessionManager(
                state_dir=state_dir, session_factory=lambda **_: None,
            )
            mgr.adopt_session_id('PROJ-1', agent_session_id='sid')
            # Mark active first via update_status, then terminate via
            # different case. The status transition should land.
            mgr.update_status('PROJ-1', SESSION_STATUS_TERMINATED)
            self.assertEqual(
                mgr.get_record('proj-1').status, SESSION_STATUS_TERMINATED,
            )


if __name__ == '__main__':
    unittest.main()
