"""Coverage tests for four small agent_core_lib helper modules.

Product-agnostic: no kato imports, fake fixtures only. Asserts the
structural behavior of each helper, exercising both sides of every
branch and the documented edge cases.
"""
from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agent_core_lib.agent_core_lib.helpers.atomic_write import atomic_write_json
from agent_core_lib.agent_core_lib.helpers.logging_utils import configure_logger
from agent_core_lib.agent_core_lib.helpers.resume_prompt_utils import (
    build_inputs_from_session,
)
from agent_core_lib.agent_core_lib.helpers.text_utils import (
    condensed_text,
    text_from_attr,
    text_from_mapping,
)


class CondensedTextTests(unittest.TestCase):
    def test_collapses_runs_of_whitespace_and_newlines(self):
        result = condensed_text('  alpha\t\tbeta\n\n  gamma   ')
        self.assertEqual(result, 'alpha beta gamma')

    def test_none_returns_empty_string(self):
        self.assertEqual(condensed_text(None), '')

    def test_empty_string_returns_empty_string(self):
        self.assertEqual(condensed_text(''), '')

    def test_whitespace_only_collapses_to_empty(self):
        self.assertEqual(condensed_text('   \n\t  '), '')

    def test_single_token_unchanged(self):
        self.assertEqual(condensed_text('solo'), 'solo')


class TextFromAttrTests(unittest.TestCase):
    def test_reads_attribute_and_normalizes(self):
        obj = SimpleNamespace(name='  PROJ-1  ')
        self.assertEqual(text_from_attr(obj, 'name'), 'PROJ-1')

    def test_missing_attribute_uses_default_normalized(self):
        obj = SimpleNamespace()
        self.assertEqual(
            text_from_attr(obj, 'missing', '  fallback  '), 'fallback'
        )

    def test_missing_attribute_default_empty_string(self):
        obj = SimpleNamespace()
        self.assertEqual(text_from_attr(obj, 'missing'), '')

    def test_non_string_attribute_is_stringified(self):
        obj = SimpleNamespace(count=42)
        self.assertEqual(text_from_attr(obj, 'count'), '42')


class TextFromMappingTests(unittest.TestCase):
    def test_none_mapping_returns_normalized_default(self):
        # mapping is None branch (line 25)
        self.assertEqual(text_from_mapping(None, 'key', '  def  '), 'def')

    def test_none_mapping_default_empty(self):
        self.assertEqual(text_from_mapping(None, 'key'), '')

    def test_mapping_without_callable_get_returns_default(self):
        # an int has no .get at all -> default branch (line 28)
        self.assertEqual(text_from_mapping(7, 'key', '  fb  '), 'fb')

    def test_mapping_with_non_callable_get_attr_returns_default(self):
        # object whose .get is not callable -> not callable branch (line 28)
        weird = SimpleNamespace(get='i am not callable')
        self.assertEqual(text_from_mapping(weird, 'key', 'd'), 'd')

    def test_real_dict_reads_key(self):
        self.assertEqual(
            text_from_mapping({'key': '  hello  '}, 'key'), 'hello'
        )

    def test_real_dict_missing_key_uses_default(self):
        self.assertEqual(
            text_from_mapping({'other': 'x'}, 'key', '  miss  '), 'miss'
        )

    def test_duck_typed_get_object_reads(self):
        duck = SimpleNamespace(get=lambda k, d: '  ducked  ' if k == 'key' else d)
        self.assertEqual(text_from_mapping(duck, 'key', 'd'), 'ducked')


class AtomicWriteJsonTests(unittest.TestCase):
    def test_success_writes_sorted_indented_json_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'out.json'
            payload = {'b': 2, 'a': 1, 'nested': {'z': 9, 'y': 8}}
            ok = atomic_write_json(path, payload)
            self.assertTrue(ok)
            self.assertTrue(path.exists())
            text = path.read_text(encoding='utf-8')
            self.assertEqual(
                text, json.dumps(payload, indent=2, sort_keys=True)
            )
            with path.open(encoding='utf-8') as fh:
                self.assertEqual(json.load(fh), payload)

    def test_success_removes_temp_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'out.json'
            ok = atomic_write_json(path, {'a': 1})
            self.assertTrue(ok)
            tmp = path.with_suffix('.json.tmp')
            self.assertFalse(
                tmp.exists(), 'temp .json.tmp must be renamed away'
            )

    def test_failure_returns_false_and_no_logger_does_not_raise(self):
        # Path under a non-existent directory makes write_text raise OSError.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'no_such_dir' / 'out.json'
            ok = atomic_write_json(path, {'a': 1})
            self.assertFalse(ok)
            self.assertFalse(path.exists())

    def test_failure_with_logger_and_label_warns_with_label_segment(self):
        logger = mock.Mock(spec=logging.Logger)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'missing' / 'out.json'
            ok = atomic_write_json(
                path, {'a': 1}, logger=logger, label='PROJ-1'
            )
            self.assertFalse(ok)
            logger.warning.assert_called_once()
            args = logger.warning.call_args.args
            # format string + label_text positional arg
            self.assertEqual(args[0], 'failed to persist json%s at %s: %s')
            self.assertEqual(args[1], ' for PROJ-1')

    def test_failure_with_logger_empty_label_has_no_label_segment(self):
        logger = mock.Mock(spec=logging.Logger)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'missing' / 'out.json'
            ok = atomic_write_json(path, {'a': 1}, logger=logger, label='')
            self.assertFalse(ok)
            logger.warning.assert_called_once()
            args = logger.warning.call_args.args
            self.assertEqual(args[1], '')

    def test_failure_via_patched_replace_raising_oserror(self):
        # Exercise the OSError path through .replace() raising (write_text ok).
        logger = mock.Mock(spec=logging.Logger)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'out.json'
            with mock.patch.object(
                Path, 'replace', side_effect=OSError('boom')
            ):
                ok = atomic_write_json(path, {'a': 1}, logger=logger)
            self.assertFalse(ok)
            logger.warning.assert_called_once()


class ConfigureLoggerTests(unittest.TestCase):
    # NOTE: the base namespace string is a tracked, deferred follow-up that
    # will be genericized, so we assert the *relationship* between base and
    # child loggers (child.name == base.name + '.<suffix>') rather than
    # hard-coding the literal root namespace as a contract.
    def test_empty_name_returns_base_logger(self):
        base = configure_logger('')
        self.assertIsInstance(base, logging.Logger)

    def test_whitespace_name_strips_to_base(self):
        base = configure_logger('')
        whitespace = configure_logger('   ')
        self.assertIs(whitespace, base)

    def test_suffix_produces_child_of_base(self):
        base = configure_logger('')
        child = configure_logger('x')
        self.assertIsInstance(base, logging.Logger)
        self.assertEqual(child.name, base.name + '.x')

    def test_named_suffix_relationship(self):
        base = configure_logger('')
        child = configure_logger('mysuffix')
        self.assertEqual(child.name, base.name + '.mysuffix')


def _event(event_type, raw):
    return SimpleNamespace(event_type=event_type, raw=raw)


def _assistant_raw(blocks):
    return {'message': {'role': 'assistant', 'content': blocks}}


class BuildInputsFromSessionBranchTests(unittest.TestCase):
    def test_non_user_non_assistant_event_is_ignored(self):
        # 222->214: event_type 'system' makes the elif False; loop continues
        # without touching last_user / last_assistant.
        events = [
            _event('system', {'message': {'content': [
                {'type': 'text', 'text': 'should be ignored'}
            ]}}),
        ]
        out = build_inputs_from_session(
            task_id='PROJ-1',
            task_summary='summary',
            branch_name='feature/x',
            workspace_path='/tmp/ws',
            repository_paths=['/tmp/ws/repo'],
            recent_events=events,
        )
        self.assertEqual(out.last_user_text, '')
        self.assertEqual(out.last_assistant_text, '')
        self.assertEqual(out.recent_assistant_texts, [])

    def test_non_user_non_assistant_does_not_override_real_turns(self):
        events = [
            _event('user', {'message': {'content': 'hi there'}}),
            _event('assistant', _assistant_raw([
                {'type': 'text', 'text': 'real answer'}
            ])),
            _event('system', {'message': {'content': [
                {'type': 'text', 'text': 'ignored noise'}
            ]}}),
        ]
        out = build_inputs_from_session(
            task_id='PROJ-1',
            task_summary='summary',
            branch_name='feature/x',
            workspace_path='/tmp/ws',
            repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.last_user_text, 'hi there')
        self.assertEqual(out.last_assistant_text, 'real answer')
        self.assertEqual(out.recent_assistant_texts, ['real answer'])

    def test_whitespace_text_block_is_skipped_in_flatten(self):
        # 265->260: a {'type':'text','text':'   '} block flattens to '' so the
        # `if text:` is False and it is skipped; only the real block survives.
        events = [
            _event('assistant', _assistant_raw([
                {'type': 'text', 'text': '   '},
                {'type': 'text', 'text': 'kept text'},
            ])),
        ]
        out = build_inputs_from_session(
            task_id='PROJ-1',
            task_summary='summary',
            branch_name='feature/x',
            workspace_path='/tmp/ws',
            repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.last_assistant_text, 'kept text')
        self.assertEqual(out.recent_assistant_texts, ['kept text'])

    def test_whitespace_block_dropped_between_two_real_blocks(self):
        # Reinforces 265->260: the empty block sits BETWEEN two real blocks,
        # so the skip happens mid-list. The two real blocks join with the
        # '\n\n' separator and the whitespace block leaves no trace.
        events = [
            _event('assistant', _assistant_raw([
                {'type': 'text', 'text': 'first'},
                {'type': 'text', 'text': '   '},
                {'type': 'text', 'text': 'second'},
            ])),
        ]
        out = build_inputs_from_session(
            task_id='PROJ-1',
            task_summary='summary',
            branch_name='feature/x',
            workspace_path='/tmp/ws',
            repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.last_assistant_text, 'first\n\nsecond')
        self.assertEqual(out.recent_assistant_texts, ['first\n\nsecond'])

    def test_empty_recent_events_yields_empty_inputs(self):
        out = build_inputs_from_session(
            task_id='PROJ-1',
            task_summary='summary',
            branch_name='feature/x',
            workspace_path='/tmp/ws',
            repository_paths=None,
            recent_events=None,
        )
        self.assertEqual(out.last_user_text, '')
        self.assertEqual(out.last_assistant_text, '')
        self.assertEqual(out.recent_assistant_texts, [])
        self.assertEqual(out.repository_paths, [])

    def test_recent_assistant_texts_respects_max(self):
        events = [
            _event('assistant', _assistant_raw([
                {'type': 'text', 'text': f'turn {i}'}
            ]))
            for i in range(5)
        ]
        out = build_inputs_from_session(
            task_id='PROJ-1',
            task_summary='summary',
            branch_name='feature/x',
            workspace_path='/tmp/ws',
            repository_paths=[],
            recent_events=events,
            max_recent_assistant=2,
        )
        self.assertEqual(out.recent_assistant_texts, ['turn 3', 'turn 4'])
        self.assertEqual(out.last_assistant_text, 'turn 4')


if __name__ == '__main__':
    unittest.main()
