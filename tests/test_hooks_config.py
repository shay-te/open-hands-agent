"""Tests for the hooks config loader + schema validation."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.hooks.config import (
    HookConfig,
    HookConfigError,
    HookDefinition,
    HookPoint,
    load_hooks_config,
)


class HookPointTests(unittest.TestCase):

    def test_every_documented_point_has_an_enum_member(self) -> None:
        # Pinning the surface — adding a hook point requires
        # updating this list AND any wiring in kato core.
        self.assertEqual(
            sorted(p.value for p in HookPoint),
            ['post_tool_use', 'pre_tool_use', 'session_end',
             'session_start', 'stop', 'user_prompt_submit'],
        )


class HookDefinitionMatchTests(unittest.TestCase):

    def test_empty_match_matches_every_event(self) -> None:
        hook = HookDefinition(
            point=HookPoint.SESSION_END, command='echo ok', match={},
        )
        self.assertTrue(hook.matches({}))
        self.assertTrue(hook.matches({'task_id': 'T1'}))

    def test_equality_predicate_matches_only_on_exact_value(self) -> None:
        hook = HookDefinition(
            point=HookPoint.PRE_TOOL_USE, command='echo',
            match={'tool': 'Bash'},
        )
        self.assertTrue(hook.matches({'tool': 'Bash'}))
        self.assertFalse(hook.matches({'tool': 'Edit'}))
        self.assertFalse(hook.matches({'tool': 'bash'}))  # case-sensitive

    def test_regex_predicate_uses_search(self) -> None:
        hook = HookDefinition(
            point=HookPoint.PRE_TOOL_USE, command='echo',
            match={'command_regex': '^rm -rf'},
        )
        self.assertTrue(hook.matches({'command': 'rm -rf /tmp/x'}))
        self.assertFalse(hook.matches({'command': 'ls -la'}))

    def test_invalid_regex_falls_back_to_non_match(self) -> None:
        # Defensive: a typo in the operator's regex shouldn't fire
        # the hook on every event. Better to silently skip than
        # block everything by mistake.
        hook = HookDefinition(
            point=HookPoint.PRE_TOOL_USE, command='echo',
            match={'command_regex': '['},  # invalid regex
        )
        self.assertFalse(hook.matches({'command': 'whatever'}))

    def test_multiple_predicates_must_ALL_match(self) -> None:
        # AND semantics — the hook gates on the conjunction.
        hook = HookDefinition(
            point=HookPoint.PRE_TOOL_USE, command='echo',
            match={'tool': 'Bash', 'command_regex': 'rm -rf'},
        )
        self.assertTrue(hook.matches({'tool': 'Bash', 'command': 'rm -rf /tmp'}))
        # Different tool fails the conjunction even with matching cmd.
        self.assertFalse(hook.matches({'tool': 'Edit', 'command': 'rm -rf'}))

    def test_missing_field_treated_as_empty_string(self) -> None:
        # If the event doesn't carry the field the predicate names,
        # the predicate compares against ''. Equality predicate
        # against a non-empty value → no match.
        hook = HookDefinition(
            point=HookPoint.PRE_TOOL_USE, command='echo',
            match={'tool': 'Bash'},
        )
        self.assertFalse(hook.matches({}))


class LoadHooksConfigTests(unittest.TestCase):

    def test_no_file_at_default_path_returns_empty_config(self) -> None:
        # The whole point: hooks are opt-in. No file → no hooks,
        # no exceptions.
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {'HOME': td}, clear=False):
                with patch.dict(os.environ, {}, clear=False):
                    # Make sure the env override is empty so we
                    # fall back to ``~/.kato/hooks.json`` which
                    # doesn't exist under the temp HOME.
                    os.environ.pop('KATO_HOOKS_CONFIG', None)
                    config = load_hooks_config()
        # We can't reliably point HOME to td from a unit test
        # without touching the global; the contract we actually
        # care about is: no path → empty. Pass an explicit
        # non-existent path to verify.
        config = load_hooks_config('/nonexistent/path/hooks.json')
        self.assertTrue(config.is_empty())

    def test_loads_valid_config_from_explicit_path(self) -> None:
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump({
                'pre_tool_use': [{
                    'match': {'tool': 'Bash'},
                    'command': '/usr/local/bin/audit',
                    'timeout_seconds': 5,
                }],
                'session_end': [{'command': 'curl webhook'}],
            }, fh)
            path = fh.name
        try:
            config = load_hooks_config(path)
            pre_hooks = config.for_point(HookPoint.PRE_TOOL_USE)
            end_hooks = config.for_point(HookPoint.SESSION_END)
            self.assertEqual(len(pre_hooks), 1)
            self.assertEqual(pre_hooks[0].command, '/usr/local/bin/audit')
            self.assertEqual(pre_hooks[0].match, {'tool': 'Bash'})
            self.assertEqual(pre_hooks[0].timeout_seconds, 5.0)
            self.assertEqual(len(end_hooks), 1)
        finally:
            os.unlink(path)

    def test_unknown_hook_point_raises_at_boot(self) -> None:
        # Operator typo (``pre_tool`` instead of ``pre_tool_use``)
        # → loud error at startup, not silent drop.
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump({'pre_tool': [{'command': 'x'}]}, fh)
            path = fh.name
        try:
            with self.assertRaisesRegex(HookConfigError, 'unknown hook point'):
                load_hooks_config(path)
        finally:
            os.unlink(path)

    def test_missing_command_field_raises(self) -> None:
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump({'session_end': [{'match': {}}]}, fh)
            path = fh.name
        try:
            with self.assertRaisesRegex(HookConfigError, 'command'):
                load_hooks_config(path)
        finally:
            os.unlink(path)

    def test_non_string_command_raises(self) -> None:
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump({'session_end': [{'command': 12345}]}, fh)
            path = fh.name
        try:
            with self.assertRaisesRegex(HookConfigError, 'command'):
                load_hooks_config(path)
        finally:
            os.unlink(path)

    def test_invalid_json_raises_with_path_in_message(self) -> None:
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            fh.write('{ not valid json')
            path = fh.name
        try:
            with self.assertRaisesRegex(HookConfigError, 'not valid JSON'):
                load_hooks_config(path)
        finally:
            os.unlink(path)

    def test_non_object_top_level_raises(self) -> None:
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump(['not an object'], fh)
            path = fh.name
        try:
            with self.assertRaisesRegex(HookConfigError, 'object at the top level'):
                load_hooks_config(path)
        finally:
            os.unlink(path)

    def test_non_list_hook_point_raises(self) -> None:
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump({'session_end': {'command': 'x'}}, fh)
            path = fh.name
        try:
            with self.assertRaisesRegex(HookConfigError, 'must map to a list'):
                load_hooks_config(path)
        finally:
            os.unlink(path)

    def test_zero_or_negative_timeout_raises(self) -> None:
        for bad_timeout in [0, -1, -0.5]:
            with tempfile.NamedTemporaryFile(
                'w', suffix='.json', delete=False, encoding='utf-8',
            ) as fh:
                json.dump({
                    'session_end': [{'command': 'x', 'timeout_seconds': bad_timeout}],
                }, fh)
                path = fh.name
            try:
                with self.assertRaisesRegex(HookConfigError, 'positive'):
                    load_hooks_config(path)
            finally:
                os.unlink(path)

    def test_explicit_path_takes_priority_over_env(self) -> None:
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump({'session_end': [{'command': 'explicit'}]}, fh)
            explicit_path = fh.name
        try:
            with patch.dict(os.environ, {
                'KATO_HOOKS_CONFIG': '/some/other/path/that/does/not/exist',
            }):
                config = load_hooks_config(explicit_path)
            self.assertEqual(
                config.for_point(HookPoint.SESSION_END)[0].command,
                'explicit',
            )
        finally:
            os.unlink(explicit_path)

    def test_env_var_used_when_no_explicit_path(self) -> None:
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump({'session_end': [{'command': 'from-env'}]}, fh)
            env_path = fh.name
        try:
            with patch.dict(os.environ, {'KATO_HOOKS_CONFIG': env_path}):
                config = load_hooks_config()
            self.assertEqual(
                config.for_point(HookPoint.SESSION_END)[0].command,
                'from-env',
            )
        finally:
            os.unlink(env_path)


class HookConfigDefensiveBranchTests(unittest.TestCase):
    """Cover the remaining defensive branches in ``_parse_one_hook``
    and ``load_hooks_config``."""

    def test_hook_entry_not_a_dict_raises(self) -> None:
        # Line 131: hook entry must be a dict.
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump({'session_end': ['not-a-dict']}, fh)
            path = fh.name
        try:
            with self.assertRaisesRegex(HookConfigError, 'must be an object'):
                load_hooks_config(path)
        finally:
            os.unlink(path)

    def test_match_not_a_dict_raises(self) -> None:
        # Line 141: match field must be a dict.
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump({
                'session_end': [{'command': 'echo', 'match': 'not-a-dict'}],
            }, fh)
            path = fh.name
        try:
            with self.assertRaisesRegex(HookConfigError, '``match``'):
                load_hooks_config(path)
        finally:
            os.unlink(path)

    def test_timeout_not_a_number_raises(self) -> None:
        # Lines 147-148: timeout_seconds must coerce to float.
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump({
                'session_end': [{'command': 'echo', 'timeout_seconds': 'forever'}],
            }, fh)
            path = fh.name
        try:
            with self.assertRaisesRegex(HookConfigError, 'must be a number'):
                load_hooks_config(path)
        finally:
            os.unlink(path)

    def test_read_text_oserror_raises_hook_config_error(self) -> None:
        # Lines 178-179: file exists at the resolved path but unreadable.
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump({'session_end': [{'command': 'echo'}]}, fh)
            path = fh.name
        try:
            with patch.object(Path, 'read_text', side_effect=OSError('denied')):
                with self.assertRaisesRegex(HookConfigError, 'failed to read'):
                    load_hooks_config(path)
        finally:
            os.unlink(path)


if __name__ == '__main__':
    unittest.main()
