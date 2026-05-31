from __future__ import annotations

import unittest

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.helpers.result_utils import (
    build_openhands_result,
    openhands_session_id,
    openhands_success_flag,
)


class OpenhandsSuccessFlagTest(unittest.TestCase):
    def test_none_payload_returns_default_false(self):
        self.assertFalse(openhands_success_flag(None))

    def test_none_payload_returns_custom_default_true(self):
        self.assertTrue(openhands_success_flag(None, default=True))

    def test_non_mapping_list_returns_default(self):
        self.assertFalse(openhands_success_flag([1, 2, 3]))
        self.assertTrue(openhands_success_flag([1, 2, 3], default=True))

    def test_non_mapping_str_returns_default(self):
        # A str is not a Mapping, so the value-level str parsing is NOT reached.
        self.assertFalse(openhands_success_flag('true'))
        self.assertTrue(openhands_success_flag('false', default=True))

    def test_success_key_missing_returns_default(self):
        self.assertFalse(openhands_success_flag({'other': 'value'}))
        self.assertTrue(openhands_success_flag({'other': 'value'}, default=True))

    def test_success_bool_true_returned_as_is(self):
        # default=False but stored value True must win.
        self.assertTrue(openhands_success_flag({ImplementationFields.SUCCESS: True}))

    def test_success_bool_false_returned_as_is(self):
        # default=True but stored value False must win.
        self.assertFalse(
            openhands_success_flag({ImplementationFields.SUCCESS: False}, default=True)
        )

    def test_success_truthy_strings_case_and_whitespace(self):
        for raw in ('true', 'TRUE', 'YES', ' on ', '1', 'On', '  true  '):
            with self.subTest(raw=raw):
                self.assertTrue(
                    openhands_success_flag({ImplementationFields.SUCCESS: raw})
                )

    def test_success_falsy_strings(self):
        for raw in ('false', 'no', '0', '', '   ', 'maybe', '2'):
            with self.subTest(raw=raw):
                # default=True so a False return proves the string parsing decided it.
                self.assertFalse(
                    openhands_success_flag(
                        {ImplementationFields.SUCCESS: raw}, default=True
                    )
                )

    def test_success_non_bool_non_str_uses_bool_value(self):
        self.assertTrue(openhands_success_flag({ImplementationFields.SUCCESS: 1}))
        self.assertFalse(openhands_success_flag({ImplementationFields.SUCCESS: 0}))
        self.assertFalse(openhands_success_flag({ImplementationFields.SUCCESS: []}))
        self.assertTrue(openhands_success_flag({ImplementationFields.SUCCESS: [0]}))

    def test_success_non_bool_non_str_ignores_default(self):
        # bool(value) is used, not the default, even when default contradicts it.
        self.assertFalse(
            openhands_success_flag({ImplementationFields.SUCCESS: 0}, default=True)
        )
        self.assertTrue(
            openhands_success_flag({ImplementationFields.SUCCESS: 5}, default=False)
        )


class OpenhandsSessionIdTest(unittest.TestCase):
    def test_none_payload_returns_empty(self):
        self.assertEqual(openhands_session_id(None), '')

    def test_no_keys_present_returns_empty(self):
        self.assertEqual(openhands_session_id({'unrelated': 'x'}), '')

    def test_session_id_takes_precedence(self):
        payload = {
            'session_id': 'sess-1',
            'conversation_id': 'conv-1',
            ImplementationFields.AGENT_SESSION_ID: 'agent-1',
        }
        self.assertEqual(openhands_session_id(payload), 'sess-1')

    def test_session_id_is_stripped(self):
        self.assertEqual(openhands_session_id({'session_id': '  sess-2  '}), 'sess-2')

    def test_blank_session_id_falls_through_to_conversation_id(self):
        payload = {'session_id': '   ', 'conversation_id': 'conv-2'}
        self.assertEqual(openhands_session_id(payload), 'conv-2')

    def test_conversation_id_used_when_session_id_absent(self):
        payload = {
            'conversation_id': 'conv-3',
            ImplementationFields.AGENT_SESSION_ID: 'agent-3',
        }
        self.assertEqual(openhands_session_id(payload), 'conv-3')

    def test_agent_session_id_used_last(self):
        payload = {
            'session_id': '',
            'conversation_id': '   ',
            ImplementationFields.AGENT_SESSION_ID: 'agent-4',
        }
        self.assertEqual(openhands_session_id(payload), 'agent-4')

    def test_all_blank_returns_empty(self):
        payload = {
            'session_id': '',
            'conversation_id': '   ',
            ImplementationFields.AGENT_SESSION_ID: '',
        }
        self.assertEqual(openhands_session_id(payload), '')

    def test_object_without_get_returns_empty(self):
        # text_from_mapping is duck-typed: an object lacking a callable
        # ``.get`` yields the normalized default, so the chain returns ''.
        class NoGet:
            pass

        self.assertEqual(openhands_session_id(NoGet()), '')


class BuildOpenhandsResultTest(unittest.TestCase):
    def test_minimal_payload_keys(self):
        result = build_openhands_result({})
        self.assertEqual(
            set(result),
            {ImplementationFields.SUCCESS, 'summary'},
        )
        self.assertFalse(result[ImplementationFields.SUCCESS])
        self.assertEqual(result['summary'], '')

    def test_summary_from_payload(self):
        result = build_openhands_result(
            {'summary': 'did the work'}, summary_fallback='fallback'
        )
        self.assertEqual(result['summary'], 'did the work')

    def test_summary_falls_back_when_absent(self):
        result = build_openhands_result({}, summary_fallback='fallback summary')
        self.assertEqual(result['summary'], 'fallback summary')

    def test_blank_payload_summary_bypasses_fallback(self):
        # A present-but-blank 'summary' is read (not the fallback) and
        # normalized to '', so the fallback is NOT used. Pins that the
        # fallback only applies when the key is absent.
        result = build_openhands_result(
            {'summary': '   '}, summary_fallback='fallback summary'
        )
        self.assertEqual(result['summary'], '')

    def test_non_string_summary_is_normalized(self):
        result = build_openhands_result({'summary': 123})
        self.assertEqual(result['summary'], '123')

    def test_success_uses_default_success(self):
        result = build_openhands_result({}, default_success=True)
        self.assertTrue(result[ImplementationFields.SUCCESS])

    def test_success_from_payload_overrides_default(self):
        result = build_openhands_result(
            {ImplementationFields.SUCCESS: 'false'}, default_success=True
        )
        self.assertFalse(result[ImplementationFields.SUCCESS])

    def test_branch_name_added_when_set(self):
        result = build_openhands_result({}, branch_name='  feature/PROJ-1  ')
        self.assertEqual(result['branch_name'], 'feature/PROJ-1')

    def test_branch_name_blank_omitted(self):
        for branch in ('', '   ', None):
            with self.subTest(branch=branch):
                result = build_openhands_result({}, branch_name=branch)
                self.assertNotIn('branch_name', result)

    def test_commit_message_from_payload(self):
        result = build_openhands_result(
            {ImplementationFields.COMMIT_MESSAGE: '  fix bug  '},
            default_commit_message='default msg',
        )
        self.assertEqual(result[ImplementationFields.COMMIT_MESSAGE], 'fix bug')

    def test_commit_message_uses_default_when_payload_absent(self):
        result = build_openhands_result({}, default_commit_message='  default msg  ')
        self.assertEqual(result[ImplementationFields.COMMIT_MESSAGE], 'default msg')

    def test_commit_message_default_none_omitted(self):
        result = build_openhands_result({}, default_commit_message=None)
        self.assertNotIn(ImplementationFields.COMMIT_MESSAGE, result)

    def test_commit_message_blank_default_still_adds_empty_key(self):
        # The gate is ``default_commit_message is not None`` (not truthiness),
        # so a blank-but-non-None default normalizes to '' yet the key IS added.
        result = build_openhands_result({}, default_commit_message='   ')
        self.assertIn(ImplementationFields.COMMIT_MESSAGE, result)
        self.assertEqual(result[ImplementationFields.COMMIT_MESSAGE], '')

    def test_blank_payload_commit_message_with_none_default_omitted(self):
        # Blank payload commit_message + default None -> key absent entirely.
        result = build_openhands_result(
            {ImplementationFields.COMMIT_MESSAGE: '   '},
            default_commit_message=None,
        )
        self.assertNotIn(ImplementationFields.COMMIT_MESSAGE, result)

    def test_blank_payload_commit_message_falls_to_default(self):
        result = build_openhands_result(
            {ImplementationFields.COMMIT_MESSAGE: '   '},
            default_commit_message='default msg',
        )
        self.assertEqual(result[ImplementationFields.COMMIT_MESSAGE], 'default msg')

    def test_message_added_when_present(self):
        result = build_openhands_result({ImplementationFields.MESSAGE: '  hello  '})
        self.assertEqual(result[ImplementationFields.MESSAGE], 'hello')

    def test_message_omitted_when_absent_or_blank(self):
        self.assertNotIn(ImplementationFields.MESSAGE, build_openhands_result({}))
        self.assertNotIn(
            ImplementationFields.MESSAGE,
            build_openhands_result({ImplementationFields.MESSAGE: '   '}),
        )

    def test_agent_session_id_added_when_present(self):
        result = build_openhands_result({'session_id': 'sess-9'})
        self.assertEqual(result[ImplementationFields.AGENT_SESSION_ID], 'sess-9')

    def test_agent_session_id_omitted_when_absent(self):
        self.assertNotIn(
            ImplementationFields.AGENT_SESSION_ID, build_openhands_result({})
        )

    def test_full_permutation_exact_keys(self):
        payload = {
            ImplementationFields.SUCCESS: True,
            'summary': 'done',
            ImplementationFields.COMMIT_MESSAGE: 'commit it',
            ImplementationFields.MESSAGE: 'a message',
            'conversation_id': 'conv-x',
        }
        result = build_openhands_result(
            payload,
            branch_name='br',
            default_commit_message='ignored default',
        )
        self.assertEqual(
            set(result),
            {
                ImplementationFields.SUCCESS,
                'summary',
                'branch_name',
                ImplementationFields.COMMIT_MESSAGE,
                ImplementationFields.MESSAGE,
                ImplementationFields.AGENT_SESSION_ID,
            },
        )
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result['summary'], 'done')
        self.assertEqual(result['branch_name'], 'br')
        self.assertEqual(result[ImplementationFields.COMMIT_MESSAGE], 'commit it')
        self.assertEqual(result[ImplementationFields.MESSAGE], 'a message')
        self.assertEqual(result[ImplementationFields.AGENT_SESSION_ID], 'conv-x')

    def test_none_payload_produces_minimal_result(self):
        result = build_openhands_result(None, summary_fallback='fb')
        self.assertEqual(set(result), {ImplementationFields.SUCCESS, 'summary'})
        self.assertFalse(result[ImplementationFields.SUCCESS])
        self.assertEqual(result['summary'], 'fb')


if __name__ == '__main__':
    unittest.main()
