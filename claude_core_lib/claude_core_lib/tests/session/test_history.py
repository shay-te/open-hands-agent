"""Coverage for kato.client.claude.session_history.

The module is tiny but load-bearing: it's how the workspace-recovery
service reattaches an orphan task folder to its existing Claude
conversation, and how the planning UI replays history after kato
restarts. A regression here turns into a silent context loss for the
user, so cover every branch explicitly.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_core_lib.claude_core_lib.session.history import (
    delete_session_file,
    find_session_file,
    find_session_id_for_cwd,
    iter_event_paths,
    load_history_events,
    _coerce_event,
    _default_projects_root,
    _has_displayable_text,
    _is_orchestration_prompt,
    _is_tool_result_only,
    _paths_equivalent,
    _peek_session_metadata,
    _CLAUDE_SESSIONS_ROOT_ENV_KEY,
    _DEFAULT_PROJECTS_ROOT,
)


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as fh:
        for line in lines:
            fh.write(json.dumps(line) + '\n')


class FindSessionIdForCwdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.projects_root = Path(self._tmp.name)

    def _seed_session(
        self,
        encoded_dir: str,
        agent_session_id: str,
        cwd: str,
        *,
        mtime: float | None = None,
    ) -> Path:
        path = self.projects_root / encoded_dir / f'{agent_session_id}.jsonl'
        _write_jsonl(path, [
            {'type': 'queue-operation', 'sessionId': agent_session_id},
            {'type': 'user', 'cwd': cwd, 'sessionId': agent_session_id,
             'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'hi'}]}},
        ])
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_returns_empty_on_blank_input(self) -> None:
        self.assertEqual(find_session_id_for_cwd('', projects_root=self.projects_root), '')
        self.assertEqual(find_session_id_for_cwd('   ', projects_root=self.projects_root), '')

    def test_returns_empty_when_projects_root_missing(self) -> None:
        missing = self.projects_root / 'never-created'
        result = find_session_id_for_cwd('/some/repo', projects_root=missing)
        self.assertEqual(result, '')

    def test_returns_empty_when_no_session_matches_cwd(self) -> None:
        self._seed_session(
            'enc-other', 'sess-1', cwd='/Users/shay/different/repo',
        )
        result = find_session_id_for_cwd(
            '/Users/shay/target/repo', projects_root=self.projects_root,
        )
        self.assertEqual(result, '')

    def test_returns_session_id_for_matching_cwd(self) -> None:
        target_cwd = self.projects_root / 'workspaces' / 'PROJ-1' / 'repo'
        target_cwd.mkdir(parents=True)
        self._seed_session(
            'enc-target', 'sess-target', cwd=str(target_cwd),
        )
        result = find_session_id_for_cwd(
            str(target_cwd), projects_root=self.projects_root,
        )
        self.assertEqual(result, 'sess-target')

    def test_picks_most_recent_session_when_multiple_match(self) -> None:
        target_cwd = self.projects_root / 'workspaces' / 'PROJ-1' / 'repo'
        target_cwd.mkdir(parents=True)
        # Older session — same cwd.
        self._seed_session(
            'enc-target', 'sess-old', cwd=str(target_cwd),
            mtime=time.time() - 3600,
        )
        # Newer session — same cwd, fresher mtime.
        self._seed_session(
            'enc-target-newer', 'sess-new', cwd=str(target_cwd),
            mtime=time.time(),
        )

        result = find_session_id_for_cwd(
            str(target_cwd), projects_root=self.projects_root,
        )

        self.assertEqual(result, 'sess-new')

    def test_normalizes_paths_before_comparing(self) -> None:
        # Trailing slash on input shouldn't break the match.
        target_cwd = self.projects_root / 'workspaces' / 'PROJ-1' / 'repo'
        target_cwd.mkdir(parents=True)
        self._seed_session('enc', 'sess-x', cwd=str(target_cwd))

        result = find_session_id_for_cwd(
            str(target_cwd) + '/', projects_root=self.projects_root,
        )

        self.assertEqual(result, 'sess-x')

    def test_skips_sessions_without_cwd_metadata(self) -> None:
        # A JSONL whose first 20 lines are queue-ops with no cwd: must
        # not crash, must not match anything.
        path = self.projects_root / 'enc-noisy' / 'sess-noop.jsonl'
        path.parent.mkdir(parents=True)
        with path.open('w', encoding='utf-8') as fh:
            for _ in range(30):
                fh.write(json.dumps({'type': 'queue-operation'}) + '\n')

        result = find_session_id_for_cwd(
            '/whatever', projects_root=self.projects_root,
        )

        self.assertEqual(result, '')

    def test_skips_jsonl_with_unparseable_first_lines(self) -> None:
        path = self.projects_root / 'enc-bad' / 'sess-bad.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text('not json at all\n', encoding='utf-8')

        # Should not raise — just no match.
        result = find_session_id_for_cwd(
            '/whatever', projects_root=self.projects_root,
        )
        self.assertEqual(result, '')


class FindSessionFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.projects_root = Path(self._tmp.name)

    def test_returns_none_for_blank_session_id(self) -> None:
        self.assertIsNone(find_session_file('', projects_root=self.projects_root))

    def test_returns_none_when_no_jsonl_matches(self) -> None:
        result = find_session_file('missing-id', projects_root=self.projects_root)
        self.assertIsNone(result)

    def test_finds_jsonl_under_any_encoded_project_dir(self) -> None:
        target = self.projects_root / 'enc-x' / 'session-id.jsonl'
        target.parent.mkdir(parents=True)
        target.write_text('{}\n', encoding='utf-8')

        result = find_session_file('session-id', projects_root=self.projects_root)

        self.assertEqual(result, target)


class LoadHistoryEventsTests(unittest.TestCase):
    """Existing replay logic gets light coverage here for safety."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.projects_root = Path(self._tmp.name)

    def test_returns_empty_when_session_not_found(self) -> None:
        events = load_history_events('missing', projects_root=self.projects_root)
        self.assertEqual(events, [])

    def test_filters_internal_noise_keeps_user_assistant(self) -> None:
        path = self.projects_root / 'enc-x' / 'sess-1.jsonl'
        path.parent.mkdir(parents=True)
        _write_jsonl(path, [
            {'type': 'queue-operation'},
            {'type': 'attachment'},
            {
                'type': 'user',
                'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'hello'}]},
            },
            {
                'type': 'assistant',
                'message': {
                    'role': 'assistant',
                    'content': [{'type': 'text', 'text': 'hi back'}],
                },
            },
        ])

        events = load_history_events('sess-1', projects_root=self.projects_root)

        types = [event['type'] for event in events]
        self.assertEqual(types, ['user', 'assistant'])

    def test_keeps_orchestration_prompts(self) -> None:
        # Restart replay must keep every prompt that was sent to Claude,
        # including Kato's initial orchestration prompt.
        path = self.projects_root / 'enc-x' / 'sess-orch.jsonl'
        path.parent.mkdir(parents=True)
        _write_jsonl(path, [
            {
                'type': 'user',
                'message': {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': 'Security guardrails:\n- do not...'},
                    ],
                },
            },
            {
                'type': 'user',
                'message': {
                    'role': 'user',
                    'content': [{'type': 'text', 'text': 'real user message'}],
                },
            },
        ])
        events = load_history_events('sess-orch', projects_root=self.projects_root)
        self.assertEqual(len(events), 2)
        joined = json.dumps(events)
        self.assertIn('Security guardrails', joined)
        self.assertIn('real user message', joined)

    def test_passes_tool_result_user_messages_through(self) -> None:
        # Tool-result-only ``user`` messages are NOT filtered out — they're
        # the response side of an assistant tool call and the chat UI
        # renders them as the tool output card.
        path = self.projects_root / 'enc-x' / 'sess-tool.jsonl'
        path.parent.mkdir(parents=True)
        _write_jsonl(path, [
            {
                'type': 'user',
                'message': {
                    'role': 'user',
                    'content': [{'type': 'tool_result', 'tool_use_id': 'u1', 'content': 'out'}],
                },
            },
        ])
        events = load_history_events('sess-tool', projects_root=self.projects_root)
        self.assertEqual(len(events), 1)

    def test_drops_user_messages_without_displayable_text(self) -> None:
        # Empty-content user records (some Claude CLI versions emit these
        # as queue placeholders) get filtered.
        path = self.projects_root / 'enc-x' / 'sess-empty.jsonl'
        path.parent.mkdir(parents=True)
        _write_jsonl(path, [
            {
                'type': 'user',
                'message': {'role': 'user', 'content': ''},
            },
            {
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': 'a'}]},
            },
        ])
        events = load_history_events('sess-empty', projects_root=self.projects_root)
        types = [event['type'] for event in events]
        self.assertEqual(types, ['assistant'])

    def test_skips_unparseable_lines(self) -> None:
        path = self.projects_root / 'enc-x' / 'sess-bad.jsonl'
        path.parent.mkdir(parents=True)
        with path.open('w', encoding='utf-8') as fh:
            fh.write('not-json\n')
            fh.write(json.dumps({
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': 'ok'}]},
            }) + '\n')
            fh.write('\n')  # blank
        events = load_history_events('sess-bad', projects_root=self.projects_root)
        self.assertEqual(len(events), 1)

    def test_respects_max_events_cap(self) -> None:
        path = self.projects_root / 'enc-x' / 'sess-big.jsonl'
        path.parent.mkdir(parents=True)
        _write_jsonl(path, [
            {
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': f'msg {i}'}]},
            }
            for i in range(10)
        ])
        events = load_history_events(
            'sess-big', projects_root=self.projects_root, max_events=3,
        )
        self.assertEqual(len(events), 3)


class DefaultProjectsRootTests(unittest.TestCase):
    def test_uses_env_override_when_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ[_CLAUDE_SESSIONS_ROOT_ENV_KEY] = tmp
            try:
                root = _default_projects_root()
                self.assertEqual(root, Path(tmp))
            finally:
                os.environ.pop(_CLAUDE_SESSIONS_ROOT_ENV_KEY, None)

    def test_falls_back_to_home_dot_claude_projects(self) -> None:
        # Blank or unset env var → home default.
        os.environ.pop(_CLAUDE_SESSIONS_ROOT_ENV_KEY, None)
        self.assertEqual(_default_projects_root(), _DEFAULT_PROJECTS_ROOT)

    def test_strips_whitespace_in_env_value(self) -> None:
        os.environ[_CLAUDE_SESSIONS_ROOT_ENV_KEY] = '   '
        try:
            # Whitespace-only treated as unset → home default.
            self.assertEqual(_default_projects_root(), _DEFAULT_PROJECTS_ROOT)
        finally:
            os.environ.pop(_CLAUDE_SESSIONS_ROOT_ENV_KEY, None)


class PathsEquivalentTests(unittest.TestCase):
    def test_false_for_empty_inputs(self) -> None:
        self.assertFalse(_paths_equivalent('', '/anything'))
        self.assertFalse(_paths_equivalent('/anything', ''))
        self.assertFalse(_paths_equivalent('', ''))

    def test_true_for_same_resolved_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Both the absolute path and a path-with-trailing-slash should match.
            self.assertTrue(_paths_equivalent(tmp, tmp + '/'))

    def test_false_for_different_paths(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertFalse(_paths_equivalent(a, b))


class PeekSessionMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_returns_cwd_and_session_id_from_first_qualifying_record(self) -> None:
        path = self.dir / 'sess.jsonl'
        _write_jsonl(path, [
            {'type': 'queue-operation', 'sessionId': 'sess-A'},  # no cwd
            {'type': 'user', 'cwd': '/repo', 'sessionId': 'sess-A'},
        ])
        cwd, sid = _peek_session_metadata(path)
        self.assertEqual(cwd, '/repo')
        self.assertEqual(sid, 'sess-A')

    def test_normalizes_session_id_from_record(self) -> None:
        path = self.dir / 'sess.jsonl'
        _write_jsonl(path, [
            {'type': 'user', 'cwd': '/repo', 'sessionId': '  sess-A\n'},
        ])

        cwd, sid = _peek_session_metadata(path)

        self.assertEqual(cwd, '/repo')
        self.assertEqual(sid, 'sess-A')

    def test_returns_empty_when_no_record_has_both_fields(self) -> None:
        path = self.dir / 'lonely.jsonl'
        _write_jsonl(path, [
            {'type': 'queue-operation', 'sessionId': 'sess-B'},
        ])
        self.assertEqual(_peek_session_metadata(path), ('', ''))

    def test_skips_blank_and_garbage_lines(self) -> None:
        path = self.dir / 'mixed.jsonl'
        with path.open('w', encoding='utf-8') as fh:
            fh.write('\n')
            fh.write('not-json\n')
            fh.write('[1,2,3]\n')  # not a dict
            fh.write(json.dumps({'cwd': '/x', 'sessionId': 'sid'}) + '\n')
        cwd, sid = _peek_session_metadata(path)
        self.assertEqual((cwd, sid), ('/x', 'sid'))

    def test_returns_empty_on_oserror(self) -> None:
        # Path that doesn't exist → OSError on open → returns empty.
        path = self.dir / 'does-not-exist.jsonl'
        self.assertEqual(_peek_session_metadata(path), ('', ''))

    def test_caps_at_twenty_lines(self) -> None:
        # Past the 20-line peek window the function gives up.
        path = self.dir / 'huge.jsonl'
        lines = [{'type': 'queue-operation'}] * 21
        lines.append({'cwd': '/x', 'sessionId': 'sid'})  # at index 21 — past the cap
        _write_jsonl(path, lines)
        cwd, sid = _peek_session_metadata(path)
        self.assertEqual((cwd, sid), ('', ''))


class CoerceEventTests(unittest.TestCase):
    def test_returns_none_for_blank_line(self) -> None:
        self.assertIsNone(_coerce_event(''))
        self.assertIsNone(_coerce_event('   \n'))

    def test_returns_none_for_invalid_json(self) -> None:
        self.assertIsNone(_coerce_event('not-json'))

    def test_returns_none_for_non_dict_payload(self) -> None:
        self.assertIsNone(_coerce_event('[1, 2, 3]'))
        self.assertIsNone(_coerce_event('"string"'))

    def test_returns_none_for_irrelevant_event_type(self) -> None:
        self.assertIsNone(_coerce_event(json.dumps({'type': 'queue-operation'})))
        self.assertIsNone(_coerce_event(json.dumps({'type': 'attachment'})))

    def test_returns_payload_for_assistant_event(self) -> None:
        payload = {
            'type': 'assistant',
            'message': {'content': [{'type': 'text', 'text': 'hi'}]},
        }
        self.assertEqual(_coerce_event(json.dumps(payload)), payload)


class HasDisplayableTextTests(unittest.TestCase):
    def test_false_for_non_dict(self) -> None:
        self.assertFalse(_has_displayable_text('not a dict'))
        self.assertFalse(_has_displayable_text(None))

    def test_true_for_non_empty_string_content(self) -> None:
        self.assertTrue(_has_displayable_text({'content': 'hello'}))

    def test_false_for_blank_string_content(self) -> None:
        self.assertFalse(_has_displayable_text({'content': '   '}))

    def test_true_for_list_with_text_block(self) -> None:
        self.assertTrue(_has_displayable_text({
            'content': [{'type': 'text', 'text': 'hello'}],
        }))

    def test_false_for_list_with_only_blank_blocks(self) -> None:
        self.assertFalse(_has_displayable_text({
            'content': [
                {'type': 'text', 'text': '   '},
                {'type': 'tool_result', 'content': 'irrelevant'},
            ],
        }))

    def test_false_for_non_list_non_string_content(self) -> None:
        self.assertFalse(_has_displayable_text({'content': 42}))


class IsOrchestrationPromptTests(unittest.TestCase):
    def test_false_when_message_not_dict(self) -> None:
        self.assertFalse(_is_orchestration_prompt('not a dict'))

    def test_true_when_marker_present(self) -> None:
        for marker in (
            'Security guardrails:', 'Tool guardrails:',
            'Address pull request comment', 'When you are done:',
        ):
            self.assertTrue(_is_orchestration_prompt({
                'content': [{'type': 'text', 'text': f'prefix {marker} suffix'}],
            }))

    def test_false_when_no_marker(self) -> None:
        self.assertFalse(_is_orchestration_prompt({
            'content': [{'type': 'text', 'text': 'hello world'}],
        }))

    def test_handles_non_list_content(self) -> None:
        # Non-list content means there are no blocks → no markers → False.
        self.assertFalse(_is_orchestration_prompt({'content': 'plain'}))


class IsToolResultOnlyTests(unittest.TestCase):
    def test_false_when_not_dict(self) -> None:
        self.assertFalse(_is_tool_result_only('plain'))

    def test_false_for_non_list_content(self) -> None:
        self.assertFalse(_is_tool_result_only({'content': 'string'}))

    def test_false_for_empty_content_list(self) -> None:
        self.assertFalse(_is_tool_result_only({'content': []}))

    def test_true_when_every_block_is_tool_result(self) -> None:
        self.assertTrue(_is_tool_result_only({
            'content': [
                {'type': 'tool_result'},
                {'type': 'tool_result'},
            ],
        }))

    def test_false_when_any_block_is_not_tool_result(self) -> None:
        self.assertFalse(_is_tool_result_only({
            'content': [
                {'type': 'tool_result'},
                {'type': 'text', 'text': 'hi'},
            ],
        }))


class FindSessionFileEdgeCases(unittest.TestCase):
    def test_returns_none_when_projects_root_is_not_a_directory(self) -> None:
        # Line 50 — root is a regular file, not a directory.
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / 'not_a_dir'
            file_path.write_text('hi')
            self.assertIsNone(find_session_file('sid', projects_root=file_path))


class FindSessionIdForCwdStatFailure(unittest.TestCase):
    def test_recovers_when_stat_fails_on_one_jsonl(self) -> None:
        # Lines 87-88: stat() raises OSError → mtime falls back to 0.0
        # rather than killing the iteration. A good session next to a bad
        # one must still be discoverable.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'enc').mkdir()
            good = root / 'enc' / 'good.jsonl'
            _write_jsonl(good, [
                {'type': 'user', 'cwd': '/repo', 'sessionId': 'good'},
            ])
            bad = root / 'enc' / 'bad.jsonl'
            _write_jsonl(bad, [
                {'type': 'user', 'cwd': '/repo', 'sessionId': 'bad'},
            ])

            original_stat = Path.stat

            def selective(self_path, *args, **kwargs):
                if self_path.name == 'bad.jsonl':
                    raise PermissionError('locked')
                return original_stat(self_path, *args, **kwargs)

            from unittest.mock import patch as patch_obj
            with patch_obj.object(Path, 'stat', selective):
                result = find_session_id_for_cwd('/repo', projects_root=root)
            # The good one survives the stat failure on the bad one.
            self.assertIn(result, ('good', 'bad'))


class PathsEquivalentResolveFailure(unittest.TestCase):
    def test_falls_back_to_string_compare_when_resolve_raises(self) -> None:
        # Lines 131-132: Path.resolve() raises OSError → string compare.
        from unittest.mock import patch as patch_obj
        with patch_obj.object(Path, 'resolve', side_effect=OSError('boom')):
            self.assertTrue(_paths_equivalent('/repo/', '/repo'))
            self.assertFalse(_paths_equivalent('/a', '/b'))


class LoadHistoryEventsOpenFailure(unittest.TestCase):
    def test_returns_empty_on_oserror_reading_file(self) -> None:
        # Lines 161-162: OSError while reading → return [], not raise.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'enc' / 'sess.jsonl'
            path.parent.mkdir()
            path.write_text('hi')  # valid path

            from unittest.mock import patch as patch_obj
            with patch_obj.object(Path, 'open', side_effect=OSError('locked')):
                events = load_history_events('sess', projects_root=root)
            self.assertEqual(events, [])


class IsOrchestrationPromptNonDictBlock(unittest.TestCase):
    def test_skips_non_dict_blocks_in_content_list(self) -> None:
        # Line 210: ``if not isinstance(block, dict): continue``
        # Mixed-type content list — non-dicts (string, int) just get skipped.
        self.assertTrue(_is_orchestration_prompt({
            'content': [
                'plain string',
                42,
                {'type': 'text', 'text': 'Security guardrails:'},
            ],
        }))


class HasDisplayableTextNonDictBlock(unittest.TestCase):
    def test_skips_non_dict_blocks_in_content_list(self) -> None:
        # Line 237: ``if not isinstance(block, dict): continue``
        # Should still detect the real text block past the junk.
        self.assertTrue(_has_displayable_text({
            'content': [
                'not a dict',
                42,
                {'type': 'text', 'text': 'hello'},
            ],
        }))


class IterEventPathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.projects_root = Path(self._tmp.name)

    def test_returns_nothing_when_root_missing(self) -> None:
        # Iterator is empty (generator immediately returns).
        result = list(iter_event_paths(projects_root=self.projects_root / 'gone'))
        self.assertEqual(result, [])

    def test_yields_jsonl_files_under_each_project_dir(self) -> None:
        (self.projects_root / 'enc-a').mkdir()
        (self.projects_root / 'enc-a' / 's1.jsonl').write_text('')
        (self.projects_root / 'enc-a' / 's2.jsonl').write_text('')
        (self.projects_root / 'enc-b').mkdir()
        (self.projects_root / 'enc-b' / 's3.jsonl').write_text('')
        # A non-dir entry at the top level → should be skipped.
        (self.projects_root / 'not-a-dir.txt').write_text('')

        paths = list(iter_event_paths(projects_root=self.projects_root))
        names = sorted(p.name for p in paths)
        self.assertEqual(names, ['s1.jsonl', 's2.jsonl', 's3.jsonl'])


class DeleteSessionFileTests(unittest.TestCase):
    """``delete_session_file`` removes a forgotten task's transcript."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.projects_root = Path(self._tmp.name)

    def _seed(self, encoded_dir: str, agent_session_id: str) -> Path:
        path = self.projects_root / encoded_dir / f'{agent_session_id}.jsonl'
        _write_jsonl(path, [{'type': 'user', 'sessionId': agent_session_id}])
        return path

    def test_deletes_matching_transcript_and_reports_true(self) -> None:
        path = self._seed('enc-a', 'sess-del')
        self.assertTrue(path.is_file())
        removed = delete_session_file(
            'sess-del', projects_root=self.projects_root,
        )
        self.assertTrue(removed)
        self.assertFalse(path.is_file())

    def test_leaves_other_transcripts_untouched(self) -> None:
        keep = self._seed('enc-a', 'keep-me')
        target = self._seed('enc-a', 'drop-me')
        delete_session_file('drop-me', projects_root=self.projects_root)
        self.assertFalse(target.is_file())
        self.assertTrue(keep.is_file())

    def test_false_when_no_match(self) -> None:
        self._seed('enc-a', 'sess-1')
        self.assertFalse(
            delete_session_file('nope', projects_root=self.projects_root),
        )

    def test_false_on_blank_id(self) -> None:
        self.assertFalse(
            delete_session_file('', projects_root=self.projects_root),
        )
        self.assertFalse(
            delete_session_file('   ', projects_root=self.projects_root),
        )

    def test_false_when_root_missing(self) -> None:
        missing = self.projects_root / 'never-created'
        self.assertFalse(
            delete_session_file('x', projects_root=missing),
        )

    def test_false_when_unlink_raises_oserror(self) -> None:
        # Defensive ``except OSError`` branch: file exists, find_session_file
        # returns its path, but unlink fails (perms denied, file vanished
        # mid-delete, fs hiccup). delete_session_file must report False
        # rather than propagate — the done-cleanup loop should never
        # crash on a leftover transcript.
        path = self._seed('enc-a', 'cant-delete')
        self.assertTrue(path.is_file())
        with patch.object(Path, 'unlink', side_effect=OSError('denied')):
            removed = delete_session_file(
                'cant-delete', projects_root=self.projects_root,
            )
        self.assertFalse(removed)


if __name__ == '__main__':
    unittest.main()
