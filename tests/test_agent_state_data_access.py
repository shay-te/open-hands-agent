import json
import tempfile
import unittest
from pathlib import Path

import bootstrap  # noqa: F401

from openhands_agent.data_layers.data_access.agent_state_data_access import (
    AgentStateDataAccess,
)
from openhands_agent.fields import PullRequestFields, StatusFields


class AgentStateDataAccessTests(unittest.TestCase):
    def test_validate_creates_empty_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / 'state.json'

            data_access = AgentStateDataAccess(str(state_path))
            data_access.validate()

            self.assertTrue(state_path.exists())
            self.assertEqual(
                json.loads(state_path.read_text(encoding='utf-8')),
                {
                    'processed_tasks': {},
                    'pull_request_contexts': {},
                    'processed_review_comments': {},
                },
            )

    def test_marks_processed_tasks_and_remembers_pr_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_access = AgentStateDataAccess(str(Path(tmp_dir) / 'state.json'))
            data_access.validate()
            data_access.mark_task_processed(
                'PROJ-1',
                [
                    {
                        PullRequestFields.REPOSITORY_ID: 'client',
                        PullRequestFields.ID: '17',
                        PullRequestFields.URL: 'https://example/pr/17',
                    }
                ],
            )
            data_access.remember_pull_request_context(
                '17',
                'client',
                'feature/proj-1/client',
            )

            self.assertTrue(data_access.is_task_processed('PROJ-1'))
            processed_task = data_access.get_processed_task('PROJ-1')
            self.assertEqual(processed_task[StatusFields.STATUS], StatusFields.READY_FOR_REVIEW)
            self.assertEqual(
                processed_task[PullRequestFields.PULL_REQUESTS][0][PullRequestFields.ID],
                '17',
            )
            self.assertEqual(
                data_access.get_pull_request_contexts('17'),
                [
                    {
                        PullRequestFields.REPOSITORY_ID: 'client',
                        'branch_name': 'feature/proj-1/client',
                    }
                ],
            )
            self.assertEqual(
                data_access.list_pull_request_contexts(),
                [
                    {
                        PullRequestFields.ID: '17',
                        PullRequestFields.REPOSITORY_ID: 'client',
                        'branch_name': 'feature/proj-1/client',
                    }
                ],
            )

    def test_marks_review_comments_processed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_access = AgentStateDataAccess(str(Path(tmp_dir) / 'state.json'))
            data_access.validate()

            data_access.mark_review_comment_processed('client', '17', '99')

            self.assertTrue(data_access.is_review_comment_processed('client', '17', '99'))
            self.assertFalse(data_access.is_review_comment_processed('client', '17', '100'))

    def test_rejects_invalid_json_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / 'state.json'
            state_path.write_text('{not-json', encoding='utf-8')

            data_access = AgentStateDataAccess(str(state_path))

            with self.assertRaisesRegex(ValueError, 'invalid agent state file'):
                data_access.validate()
