"""Tests for the Kato-specific resume_prompt.md atomic writer.

The generic renderer + session adapter are tested in
``agent_core_lib/agent_core_lib/tests/test_resume_prompt_utils.py``;
this file covers only the workspace-on-disk write path that stays in
``kato_core_lib``.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kato_core_lib.helpers.resume_prompt_writer import (
    RESUME_PROMPT_FILENAME,
    write_resume_prompt,
)


class WriteResumePromptTests(unittest.TestCase):

    def test_writes_file_at_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / 'workspaces' / 'PROJ-1'
            ws.mkdir(parents=True)
            content = '# hello world'
            ok = write_resume_prompt(ws, content)
            self.assertTrue(ok)
            target = ws / RESUME_PROMPT_FILENAME
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(), content)

    def test_creates_parent_directory_if_missing(self) -> None:
        # Operator might invoke the writer for a not-yet-provisioned
        # task; the atomic-text helper should still create the dir.
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / 'never-existed'
            ok = write_resume_prompt(ws, 'hi')
            self.assertTrue(ok)
            self.assertTrue((ws / RESUME_PROMPT_FILENAME).is_file())

    def test_atomic_no_partial_file_on_failure(self) -> None:
        # When the workspace path is a FILE (not a directory), the
        # write fails cleanly — no half-written file lying around.
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / 'blocker'
            blocker.write_text('this is a file, not a directory')
            ok = write_resume_prompt(blocker, 'should fail')
            self.assertFalse(ok)
            # Original blocker file untouched.
            self.assertEqual(
                blocker.read_text(), 'this is a file, not a directory',
            )

    def test_no_op_when_workspace_path_blank(self) -> None:
        self.assertFalse(write_resume_prompt('', 'content'))
        self.assertFalse(write_resume_prompt(None, 'content'))


if __name__ == '__main__':
    unittest.main()
