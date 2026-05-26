"""Coverage for kato.data_layers.service.triage_service.

Pin down the contract of the ``kato:triage:investigate`` short-circuit:

* Tasks without the investigate tag pass through unchanged (return None
  so the caller continues with the regular flow).
* Tasks with the tag run the investigator, parse the response into one
  of the canonical outcome tags, and apply it via task_service.add_tag.
* The investigate tag is removed once the outcome tag is on.
* Failure modes (no investigator wired, parse failure, add_tag fails)
  leave the task untouched and post an actionable comment.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from kato_core_lib.data_layers.data.fields import StatusFields, TaskTags
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.data_layers.service.triage_service import (
    TRIAGE_STATUS_INCONCLUSIVE,
    TRIAGE_STATUS_TRIAGED,
    TRIAGE_STATUS_UNAVAILABLE,
    TriageService,
)
from tests.utils import build_task


def _task_with_tags(*tags: str):
    return build_task(tags=list(tags))


class TriageDetectionTests(unittest.TestCase):
    """The short-circuit only fires when the investigate tag is on."""

    def setUp(self) -> None:
        self.task_service = Mock()
        self.investigator = Mock(return_value='kato:triage:medium')
        self.service = TriageService(
            task_service=self.task_service,
            triage_investigator=self.investigator,
        )

    def test_returns_none_when_no_triage_tag(self) -> None:
        result = self.service.handle_task(_task_with_tags())
        self.assertIsNone(result)
        self.investigator.assert_not_called()

    def test_returns_none_when_other_kato_tag_only(self) -> None:
        # ``kato:wait-planning`` is a different short-circuit — triage
        # service must ignore it and let the orchestrator route on.
        result = self.service.handle_task(
            _task_with_tags(TaskTags.WAIT_PLANNING),
        )
        self.assertIsNone(result)
        self.investigator.assert_not_called()

    def test_returns_none_when_outcome_tag_only(self) -> None:
        # A task that's already been triaged shouldn't re-trigger
        # investigation (we leave outcome tags around as the result).
        result = self.service.handle_task(
            _task_with_tags(TaskTags.TRIAGE_HIGH),
        )
        self.assertIsNone(result)

    def test_detects_investigate_tag_as_dict(self) -> None:
        # Some platforms surface tags as dicts ({'name': ...}); the
        # detector has to handle both shapes.
        task = SimpleNamespace(
            id='PROJ-1',
            summary='thing',
            description='',
            tags=[{'name': TaskTags.TRIAGE_INVESTIGATE}],
        )
        result = self.service.handle_task(task)
        self.assertIsNotNone(result)
        self.investigator.assert_called_once()


class TriageHappyPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task_service = Mock()

    def _make_service(self, response: str) -> TriageService:
        return TriageService(
            task_service=self.task_service,
            triage_investigator=Mock(return_value=response),
        )

    def test_applies_outcome_tag_and_removes_investigate(self) -> None:
        service = self._make_service(
            'After reading the task, my classification is:\n'
            'kato:triage:high'
        )

        result = service.handle_task(
            _task_with_tags(TaskTags.TRIAGE_INVESTIGATE),
        )

        self.assertEqual(result[StatusFields.STATUS], TRIAGE_STATUS_TRIAGED)
        self.assertEqual(result['triage_tag'], TaskTags.TRIAGE_HIGH)
        self.task_service.add_tag.assert_called_once_with(
            'PROJ-1', TaskTags.TRIAGE_HIGH,
        )
        self.task_service.remove_tag.assert_called_once_with(
            'PROJ-1', TaskTags.TRIAGE_INVESTIGATE,
        )
        self.task_service.add_comment.assert_called_once()

    def test_each_canonical_outcome_tag_is_recognized(self) -> None:
        outcomes = [
            TaskTags.TRIAGE_CRITICAL,
            TaskTags.TRIAGE_HIGH,
            TaskTags.TRIAGE_MEDIUM,
            TaskTags.TRIAGE_LOW,
            TaskTags.TRIAGE_DUPLICATE,
            TaskTags.TRIAGE_WONTFIX,
            TaskTags.TRIAGE_INVALID,
            TaskTags.TRIAGE_NEEDS_INFO,
            TaskTags.TRIAGE_BLOCKED,
            TaskTags.TRIAGE_QUESTION,
        ]
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                self.task_service.reset_mock()
                service = self._make_service(f'Reasoning. {outcome}')
                result = service.handle_task(
                    _task_with_tags(TaskTags.TRIAGE_INVESTIGATE),
                )
                self.assertEqual(result['triage_tag'], outcome)
                self.task_service.add_tag.assert_called_once_with(
                    'PROJ-1', outcome,
                )

    def test_match_is_case_insensitive_in_response(self) -> None:
        # Claude might capitalize. The parser shouldn't be picky as
        # long as the canonical lower-case form is what gets applied.
        service = self._make_service('Final: KATO:TRIAGE:Critical')

        result = service.handle_task(
            _task_with_tags(TaskTags.TRIAGE_INVESTIGATE),
        )

        self.assertEqual(result['triage_tag'], TaskTags.TRIAGE_CRITICAL)

    def test_extracts_first_match_when_response_has_multiple(self) -> None:
        # Operators sometimes ramble. Take the first canonical match.
        service = self._make_service(
            'Could be kato:triage:high or kato:triage:medium. '
            'Final answer: kato:triage:high'
        )

        result = service.handle_task(
            _task_with_tags(TaskTags.TRIAGE_INVESTIGATE),
        )

        self.assertEqual(result['triage_tag'], TaskTags.TRIAGE_HIGH)


class TriageInconclusiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task_service = Mock()

    def test_response_without_recognizable_tag_is_inconclusive(self) -> None:
        service = TriageService(
            task_service=self.task_service,
            triage_investigator=Mock(
                return_value="I'm not sure how to classify this.",
            ),
        )

        result = service.handle_task(
            _task_with_tags(TaskTags.TRIAGE_INVESTIGATE),
        )

        self.assertEqual(result[StatusFields.STATUS], TRIAGE_STATUS_INCONCLUSIVE)
        self.task_service.add_tag.assert_not_called()
        self.task_service.remove_tag.assert_not_called()
        # Comment posted explaining the inconclusive outcome.
        self.task_service.add_comment.assert_called_once()
        comment = self.task_service.add_comment.call_args.args[1]
        self.assertIn('could not classify', comment.lower())

    def test_investigator_exception_is_inconclusive_not_crash(self) -> None:
        service = TriageService(
            task_service=self.task_service,
            triage_investigator=Mock(side_effect=RuntimeError('claude down')),
        )

        result = service.handle_task(
            _task_with_tags(TaskTags.TRIAGE_INVESTIGATE),
        )

        self.assertEqual(result[StatusFields.STATUS], TRIAGE_STATUS_INCONCLUSIVE)
        self.task_service.add_tag.assert_not_called()

    def test_add_tag_failure_is_inconclusive(self) -> None:
        # Tag-API call blows up. Don't pretend the task was triaged.
        self.task_service.add_tag.side_effect = RuntimeError('youtrack 500')
        service = TriageService(
            task_service=self.task_service,
            triage_investigator=Mock(return_value='kato:triage:high'),
        )

        result = service.handle_task(
            _task_with_tags(TaskTags.TRIAGE_INVESTIGATE),
        )

        self.assertEqual(result[StatusFields.STATUS], TRIAGE_STATUS_INCONCLUSIVE)
        self.task_service.remove_tag.assert_not_called()

    def test_inconclusive_without_reason_still_records_claude_response(self) -> None:
        # Branch 158->160: ``reason`` is blank, so the ``Reason:`` line is
        # not appended — execution proceeds directly to the
        # ``if claude_response`` check.
        service = TriageService(
            task_service=self.task_service,
            triage_investigator=Mock(),
        )

        result = service._record_inconclusive(
            _task_with_tags(TaskTags.TRIAGE_INVESTIGATE),
            reason='',
            claude_response='raw model output',
        )

        self.assertEqual(result[StatusFields.STATUS], TRIAGE_STATUS_INCONCLUSIVE)
        comment = self.task_service.add_comment.call_args.args[1]
        self.assertNotIn('Reason:', comment)
        self.assertIn('raw model output', comment)


class TriageUnavailableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task_service = Mock()

    def test_no_investigator_yields_unavailable(self) -> None:
        service = TriageService(
            task_service=self.task_service,
            triage_investigator=None,
        )

        result = service.handle_task(
            _task_with_tags(TaskTags.TRIAGE_INVESTIGATE),
        )

        self.assertEqual(result[StatusFields.STATUS], TRIAGE_STATUS_UNAVAILABLE)
        self.task_service.add_tag.assert_not_called()
        self.task_service.add_comment.assert_called_once()
        comment = self.task_service.add_comment.call_args.args[1]
        self.assertIn('could not triage', comment.lower())

    def test_platform_without_tag_support_yields_unavailable(self) -> None:
        # Ticket platform raises NotImplementedError on add_tag (e.g.
        # a future client that opted out of tag manipulation). Triage
        # service must report unavailable, not crash.
        self.task_service.add_tag.side_effect = NotImplementedError(
            'this client does not support add_tag',
        )
        service = TriageService(
            task_service=self.task_service,
            triage_investigator=Mock(return_value='kato:triage:high'),
        )

        result = service.handle_task(
            _task_with_tags(TaskTags.TRIAGE_INVESTIGATE),
        )

        self.assertEqual(result[StatusFields.STATUS], TRIAGE_STATUS_UNAVAILABLE)


class TriageRemoveTagResilienceTests(unittest.TestCase):
    def test_remove_tag_failure_does_not_revert_outcome(self) -> None:
        # The outcome tag is already on the task — that's the work
        # product. If removing the investigate tag fails (network blip,
        # platform without remove_tag support), the operation is still
        # a success and we don't fail the task.
        task_service = Mock()
        task_service.remove_tag.side_effect = RuntimeError('flaky')
        service = TriageService(
            task_service=task_service,
            triage_investigator=Mock(return_value='kato:triage:low'),
        )

        result = service.handle_task(
            _task_with_tags(TaskTags.TRIAGE_INVESTIGATE),
        )

        self.assertEqual(result[StatusFields.STATUS], TRIAGE_STATUS_TRIAGED)
        self.assertEqual(result['triage_tag'], TaskTags.TRIAGE_LOW)
        task_service.add_tag.assert_called_once()


if __name__ == '__main__':
    unittest.main()
