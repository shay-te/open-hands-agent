import unittest

from kato.data_layers.data.fields import ImplementationFields
from kato.helpers.kato_result_utils import (
    build_openhands_result,
    openhands_session_id,
    openhands_success_flag,
)


class KatoResultUtilsTests(unittest.TestCase):
    def test_openhands_success_flag_honors_default_when_success_missing(self) -> None:
        self.assertFalse(openhands_success_flag({}))
        self.assertTrue(openhands_success_flag({}, default=True))

    def test_openhands_session_id_reads_session_and_conversation_keys(self) -> None:
        self.assertEqual(
            openhands_session_id({ImplementationFields.SESSION_ID: 'conversation-1'}),
            'conversation-1',
        )
        self.assertEqual(
            openhands_session_id({'conversation_id': 'conversation-2'}),
            'conversation-2',
        )

    def test_build_openhands_result_applies_branch_commit_and_session_defaults(self) -> None:
        result = build_openhands_result(
            {
                'summary': 'Implemented task',
                ImplementationFields.MESSAGE: 'Validation report: no tests were defined.',
                ImplementationFields.SESSION_ID: 'conversation-3',
            },
            branch_name='feature/proj-1',
            default_commit_message='Implement PROJ-1',
        )

        self.assertEqual(
            result,
            {
                'branch_name': 'feature/proj-1',
                'summary': 'Implemented task',
                ImplementationFields.COMMIT_MESSAGE: 'Implement PROJ-1',
                ImplementationFields.MESSAGE: 'Validation report: no tests were defined.',
                ImplementationFields.SUCCESS: False,
                ImplementationFields.SESSION_ID: 'conversation-3',
            },
        )

    def test_build_openhands_result_allows_finish_payload_default_success(self) -> None:
        result = build_openhands_result(
            {},
            summary_fallback='Done.',
            default_success=True,
        )

        self.assertEqual(
            result,
            {
                'summary': 'Done.',
                ImplementationFields.SUCCESS: True,
            },
        )
