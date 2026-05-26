import unittest
from unittest.mock import patch

from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
from tests.utils import build_task


class WaitPlanningServicePromptTests(unittest.TestCase):
    def test_planning_prompt_marks_ignored_repositories_out_of_bounds(self) -> None:
        with patch.dict(
            'os.environ',
            {'KATO_IGNORED_REPOSITORY_FOLDERS': 'secret-client'},
        ):
            prompt = WaitPlanningService._build_planning_prompt(build_task())

        self.assertIn('Forbidden repository folders', prompt)
        self.assertIn('- secret-client', prompt)
        self.assertIn('Do not access them with Read, Glob, Grep, Bash', prompt)
        self.assertIn('Execution protocol for forbidden repositories', prompt)
        self.assertIn('DO NOT call any tools', prompt)


class WaitPlanningTagDetectionTests(unittest.TestCase):
    def test_task_with_unrelated_tags_is_not_wait_planning(self) -> None:
        # Branch 85->84: an unrelated tag is encountered, the inner ``if``
        # is False, and the loop continues to the next iteration before
        # eventually falling through to ``return False``.
        task = build_task(tags=['kato:triage:high', 'other-tag', ''])
        self.assertFalse(WaitPlanningService.task_has_wait_planning_tag(task))

    def test_task_with_wait_planning_tag_returns_true(self) -> None:
        task = build_task(tags=['unrelated', 'kato:wait-planning'])
        self.assertTrue(WaitPlanningService.task_has_wait_planning_tag(task))


if __name__ == '__main__':
    unittest.main()
