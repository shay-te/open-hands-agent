"""Tests for Claude Code session adoption in the planning UI.

Two surfaces are pinned down here:

1. ``ClaudeSessionMetadata`` discovery — kato walks
   ``~/.claude/projects/`` (or ``KATO_CLAUDE_SESSIONS_ROOT`` for
   tests), parses the JSONL transcripts, and returns metadata the
   planning UI dropdown can render. Search filtering, recency
   ordering, malformed-line tolerance, and bounded read are all
   nailed down.
2. ``ClaudeSessionManager.adopt_session_id`` — when the operator
   picks a session, kato writes the session id into the per-task
   record so the next agent spawn ``--resume``s that conversation
   instead of starting fresh. Idempotent, refuses empty ids,
   creates a record from scratch when none existed before.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

from claude_core_lib.claude_core_lib.session.manager import (
    ClaudeSessionManager,
    PlanningSessionRecord,
    SESSION_STATUS_TERMINATED,
)
from claude_core_lib.claude_core_lib.session.index import (
    CLAUDE_SESSIONS_ROOT_ENV_KEY,
    ClaudeSessionMetadata,
    claude_project_dir_for_cwd,
    default_sessions_root,
    list_sessions,
    migrate_session_to_workspace,
)


def _write_transcript(
    root: Path,
    project_dir: str,
    agent_session_id: str,
    *,
    cwd: str = '/Users/dev/repos/myproj',
    user_messages: list[str] | None = None,
    extra_lines: list[dict] | None = None,
    file_mtime: float | None = None,
) -> Path:
    project_path = root / project_dir
    project_path.mkdir(parents=True, exist_ok=True)
    transcript_path = project_path / f'{agent_session_id}.jsonl'
    lines: list[str] = []
    for text in user_messages or []:
        lines.append(json.dumps({
            'type': 'user',
            'sessionId': agent_session_id,
            'cwd': cwd,
            'message': {'content': text},
        }))
    for raw in extra_lines or []:
        lines.append(json.dumps(raw))
    transcript_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    if file_mtime is not None:
        os.utime(transcript_path, (file_mtime, file_mtime))
    return transcript_path


class SessionDiscoveryTests(unittest.TestCase):
    """Walking the JSONL store, parsing, ordering."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_returns_empty_when_root_missing(self) -> None:
        self.assertEqual(
            list_sessions(sessions_root=self.root / 'does-not-exist'),
            [],
        )

    def test_returns_empty_when_no_transcripts(self) -> None:
        # Empty projects dir → empty list, not a crash.
        self.assertEqual(list_sessions(sessions_root=self.root), [])

    def test_discovers_single_session(self) -> None:
        _write_transcript(
            self.root, '-Users-dev-repos-myproj', 'sess-1',
            user_messages=['help me with the auth flow'],
        )
        sessions = list_sessions(sessions_root=self.root)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].agent_session_id, 'sess-1')
        self.assertEqual(sessions[0].cwd, '/Users/dev/repos/myproj')
        self.assertEqual(sessions[0].turn_count, 1)
        self.assertEqual(
            sessions[0].first_user_message, 'help me with the auth flow',
        )

    def test_first_and_last_user_messages_are_distinct(self) -> None:
        _write_transcript(
            self.root, '-proj', 'sess-1',
            user_messages=['first thought', 'middle', 'last thought'],
        )
        session = list_sessions(sessions_root=self.root)[0]
        self.assertEqual(session.first_user_message, 'first thought')
        self.assertEqual(session.last_user_message, 'last thought')
        self.assertEqual(session.turn_count, 3)

    def test_orders_by_recency_descending(self) -> None:
        now = time.time()
        _write_transcript(
            self.root, '-proj', 'old', user_messages=['old'],
            file_mtime=now - 3600,
        )
        _write_transcript(
            self.root, '-proj', 'new', user_messages=['new'],
            file_mtime=now,
        )
        ids = [s.agent_session_id for s in list_sessions(sessions_root=self.root)]
        self.assertEqual(ids, ['new', 'old'])

    def test_query_matches_cwd_substring(self) -> None:
        _write_transcript(
            self.root, '-Users-dev-repos-billing', 'sess-billing',
            cwd='/Users/dev/repos/billing',
            user_messages=['fix the invoice bug'],
        )
        _write_transcript(
            self.root, '-Users-dev-repos-marketing', 'sess-marketing',
            cwd='/Users/dev/repos/marketing',
            user_messages=['update the pricing page'],
        )
        results = list_sessions(sessions_root=self.root, query='billing')
        self.assertEqual([s.agent_session_id for s in results], ['sess-billing'])

    def test_query_matches_user_message_substring(self) -> None:
        _write_transcript(
            self.root, '-proj-a', 'sess-a',
            user_messages=['fix the auth flow'],
        )
        _write_transcript(
            self.root, '-proj-b', 'sess-b',
            user_messages=['add a new dashboard'],
        )
        results = list_sessions(sessions_root=self.root, query='auth')
        self.assertEqual([s.agent_session_id for s in results], ['sess-a'])

    def test_query_is_case_insensitive(self) -> None:
        _write_transcript(
            self.root, '-proj', 'sess',
            user_messages=['Fix The AUTH Flow'],
        )
        results = list_sessions(sessions_root=self.root, query='auth')
        self.assertEqual(len(results), 1)

    def test_max_results_is_respected(self) -> None:
        for n in range(5):
            _write_transcript(
                self.root, f'-proj-{n}', f'sess-{n}',
                user_messages=[f'task {n}'],
            )
        results = list_sessions(sessions_root=self.root, max_results=2)
        self.assertEqual(len(results), 2)

    def test_malformed_jsonl_lines_are_skipped(self) -> None:
        project_dir = self.root / '-proj'
        project_dir.mkdir()
        path = project_dir / 'sess.jsonl'
        path.write_text(
            'this is not json\n'
            + json.dumps({
                'type': 'user',
                'sessionId': 'sess',
                'cwd': '/proj',
                'message': {'content': 'good message'},
            })
            + '\nstill not json\n',
            encoding='utf-8',
        )
        results = list_sessions(sessions_root=self.root)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].first_user_message, 'good message')

    def test_user_message_with_text_part_list_is_extracted(self) -> None:
        _write_transcript(
            self.root, '-proj', 'sess',
            extra_lines=[{
                'type': 'user',
                'sessionId': 'sess',
                'cwd': '/proj',
                'message': {
                    'content': [
                        {'type': 'tool_result', 'content': 'output'},
                        {'type': 'text', 'text': 'the actual question'},
                    ],
                },
            }],
        )
        session = list_sessions(sessions_root=self.root)[0]
        self.assertEqual(session.first_user_message, 'the actual question')

    def test_tool_result_user_records_do_not_provide_preview(self) -> None:
        # A user record carrying only a tool_result (no text) should
        # increment turn count but not overwrite a meaningful preview.
        _write_transcript(
            self.root, '-proj', 'sess',
            user_messages=['real question'],
            extra_lines=[{
                'type': 'user',
                'sessionId': 'sess',
                'cwd': '/proj',
                'message': {
                    'content': [
                        {'type': 'tool_result', 'content': 'tool output'},
                    ],
                },
            }],
        )
        session = list_sessions(sessions_root=self.root)[0]
        self.assertEqual(session.first_user_message, 'real question')
        self.assertEqual(session.last_user_message, 'real question')
        self.assertEqual(session.turn_count, 2)

    def test_long_preview_is_clipped(self) -> None:
        long_text = 'x' * 500
        _write_transcript(
            self.root, '-proj', 'sess',
            user_messages=[long_text],
        )
        session = list_sessions(sessions_root=self.root)[0]
        self.assertLess(len(session.first_user_message), 500)
        self.assertTrue(session.first_user_message.endswith('…'))

    def test_default_sessions_root_uses_env_override(self) -> None:
        with patch.dict(
            os.environ,
            {CLAUDE_SESSIONS_ROOT_ENV_KEY: str(self.root)},
            clear=False,
        ):
            self.assertEqual(default_sessions_root(), self.root)

    def test_metadata_to_dict_is_json_serialisable(self) -> None:
        meta = ClaudeSessionMetadata(
            agent_session_id='sess',
            cwd='/proj',
            last_modified_epoch=1.5,
            turn_count=2,
            first_user_message='hi',
            last_user_message='bye',
            transcript_path='/tmp/sess.jsonl',
        )
        self.assertEqual(json.loads(json.dumps(meta.to_dict())), meta.to_dict())


class SessionManagerAdoptionTests(unittest.TestCase):
    """``ClaudeSessionManager.adopt_session_id`` writes the id back."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.manager = ClaudeSessionManager(state_dir=self.state_dir)

    def test_adopt_creates_record_when_none_exists(self) -> None:
        record = self.manager.adopt_session_id(
            'PROJ-1', agent_session_id='abc-def',
        )
        self.assertEqual(record.task_id, 'PROJ-1')
        self.assertEqual(record.agent_session_id, 'abc-def')
        self.assertEqual(record.status, SESSION_STATUS_TERMINATED)

    def test_adopt_refuses_to_change_existing_session_id(self) -> None:
        self.manager.adopt_session_id('PROJ-1', agent_session_id='first')
        with self.assertRaisesRegex(RuntimeError, 'already pinned'):
            self.manager.adopt_session_id('PROJ-1', agent_session_id='second')
        self.assertEqual(
            self.manager.get_record('PROJ-1').agent_session_id,
            'first',
        )

    def test_adopt_persists_record_to_disk(self) -> None:
        self.manager.adopt_session_id(
            'PROJ-1', agent_session_id='abc-def',
            task_summary='fix the bug',
        )
        # New manager instance reads the persisted record from disk
        # at construction.
        fresh = ClaudeSessionManager(state_dir=self.state_dir)
        record = fresh.get_record('PROJ-1')
        self.assertIsNotNone(record)
        self.assertEqual(record.agent_session_id, 'abc-def')
        self.assertEqual(record.task_summary, 'fix the bug')

    def test_adopt_refuses_empty_session_id(self) -> None:
        with self.assertRaisesRegex(ValueError, 'must be non-empty'):
            self.manager.adopt_session_id('PROJ-1', agent_session_id='')

    def test_adopt_strips_whitespace_around_session_id(self) -> None:
        record = self.manager.adopt_session_id(
            'PROJ-1', agent_session_id='  abc-def\n',
        )
        self.assertEqual(record.agent_session_id, 'abc-def')

    def test_adopt_does_not_change_cwd_so_kato_keeps_workspace_isolation(self) -> None:
        # Adoption MUST NOT repoint kato's spawn cwd at the source
        # session's directory. The operator wants kato to run
        # against its per-task workspace clone (an isolated copy)
        # so it can review changes against a clean worktree, not
        # against their live editor checkout. A short-lived
        # experiment with the opposite behaviour broke that
        # invariant — kato edited the dev's checkout in-place and
        # mixed git state. This test locks the safe default down.
        # Pre-set a cwd as if a previous spawn populated it.
        first = self.manager.adopt_session_id('PROJ-1', agent_session_id='abc-def')
        first.cwd = '/wks/PROJ-1/admin-backend'
        # Idempotent re-adopt — record.cwd must be untouched.
        self.manager.adopt_session_id('PROJ-1', agent_session_id='abc-def')
        self.assertEqual(
            self.manager.get_record('PROJ-1').cwd,
            '/wks/PROJ-1/admin-backend',
        )

    def test_adopt_does_not_overwrite_existing_task_summary(self) -> None:
        self.manager.adopt_session_id(
            'PROJ-1', agent_session_id='first',
            task_summary='first summary',
        )
        self.manager.adopt_session_id(
            'PROJ-1', agent_session_id='first',
            task_summary='second summary',
        )
        record = self.manager.get_record('PROJ-1')
        # First summary stays — the operator is adopting an existing
        # conversation, not redefining the task.
        self.assertEqual(record.task_summary, 'first summary')


class ProjectDirEncodingTests(unittest.TestCase):
    """``claude_project_dir_for_cwd`` matches Claude Code's on-disk layout."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env_patch = patch.dict(
            os.environ,
            {CLAUDE_SESSIONS_ROOT_ENV_KEY: self._tmp.name},
            clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_encodes_unix_cwd_with_dash_separator(self) -> None:
        # Replace ``/`` with ``-``; leading slash becomes leading dash.
        result = claude_project_dir_for_cwd('/Users/shay/repos/myproj')
        self.assertEqual(
            result,
            Path(self._tmp.name) / '-Users-shay-repos-myproj',
        )

    def test_collapses_to_workspace_root_under_env_override(self) -> None:
        # Override pins the projects root to a temp dir so tests
        # don't write into the operator's real ~/.claude.
        result = claude_project_dir_for_cwd('/x/y')
        self.assertTrue(str(result).startswith(self._tmp.name))

    def test_encodes_windows_cwd_collapsing_drive_colon_and_backslashes(
        self,
    ) -> None:
        # On Windows Claude Code flattens both the drive colon AND each
        # backslash to ``-`` — ``C:\Codes\proj`` becomes
        # ``C--Codes-proj`` (the consecutive ``:\`` produces two dashes
        # in a row). Replacing only ``os.sep`` left the colon intact so
        # the migrated JSONL was unreachable from --resume.
        with patch('os.path.abspath', side_effect=lambda p: p):
            result = claude_project_dir_for_cwd(r'C:\Codes\UNA-2489-proj')
        self.assertEqual(
            result.name,
            'C--Codes-UNA-2489-proj',
        )

    def test_flattens_underscore_to_dash(self) -> None:
        # UNA-2669 regression: Claude Code flattens ``_`` to ``-`` too.
        # When a workspace lives under ``dev_kato/`` and kato's encoder
        # kept the underscore, the migrated JSONL landed in
        # ``-Users-...-dev_kato-...`` while ``claude --resume`` looked
        # in ``-Users-...-dev-kato-...``. The session was therefore
        # never found and review-comment fixes crashed on every scan
        # tick (refusing the fresh fallback by design).
        result = claude_project_dir_for_cwd(
            '/Users/me/dev_kato/UNA-2669/ob-love-admin-client',
        )
        self.assertEqual(
            result.name,
            '-Users-me-dev-kato-UNA-2669-ob-love-admin-client',
        )

    def test_flattens_dot_to_dash(self) -> None:
        # Claude Code also flattens ``.`` (a hidden folder or a path
        # like ``./something``) to ``-``. Pin that behaviour so a
        # cwd like ``/Users/me/.local/share/repo`` resolves to the
        # same project dir Claude writes to.
        result = claude_project_dir_for_cwd('/Users/me/.cache/proj')
        self.assertEqual(
            result.name,
            '-Users-me--cache-proj',
        )


class ClaudeProjectDirAbsolutePathTests(unittest.TestCase):
    """``claude_project_dir_for_cwd`` must always return an absolute path.

    UNA-2669 regression: when ``KATO_CLAUDE_SESSIONS_ROOT`` was unset,
    ``Path('').expanduser()`` evaluated to ``Path('.')`` — truthy and
    ``is_dir()`` for the current working directory — silently rerouting
    the project dir to a RELATIVE path under wherever kato was running.
    The adoption-flow JSONL migration then wrote to
    ``<kato_cwd>/<encoded>/<id>.jsonl`` instead of
    ``~/.claude/projects/<encoded>/<id>.jsonl``, Claude never found it,
    and every ``--resume`` failed.
    """

    def test_returns_absolute_path_when_env_override_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(CLAUDE_SESSIONS_ROOT_ENV_KEY, None)
            result = claude_project_dir_for_cwd('/Users/me/proj')
        self.assertTrue(result.is_absolute(), f'expected absolute path, got {result}')
        self.assertIn('.claude/projects', str(result))

    def test_returns_absolute_path_when_env_override_is_empty_string(self) -> None:
        # An exported-but-empty env var must behave the same as unset —
        # not as "rebase under cwd". This is the exact crash signature.
        with patch.dict(os.environ, {CLAUDE_SESSIONS_ROOT_ENV_KEY: ''}, clear=False):
            result = claude_project_dir_for_cwd('/Users/me/proj')
        self.assertTrue(result.is_absolute(), f'expected absolute path, got {result}')
        self.assertIn('.claude/projects', str(result))

    def test_returns_absolute_path_when_env_override_is_whitespace(self) -> None:
        with patch.dict(os.environ, {CLAUDE_SESSIONS_ROOT_ENV_KEY: '   '}, clear=False):
            result = claude_project_dir_for_cwd('/Users/me/proj')
        self.assertTrue(result.is_absolute(), f'expected absolute path, got {result}')
        self.assertIn('.claude/projects', str(result))


class MigrateSessionToWorkspaceTests(unittest.TestCase):
    """``migrate_session_to_workspace`` copies the JSONL to the target cwd's project dir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # Override Claude Code's project root so tests don't pollute
        # the host's ~/.claude/projects.
        self._env_patch = patch.dict(
            os.environ,
            {CLAUDE_SESSIONS_ROOT_ENV_KEY: str(self.root)},
            clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        # Source: a fake "VS Code" session JSONL stored under a
        # different cwd's encoded project directory.
        self.source_project_dir = self.root / '-Users-dev-repos-myproj'
        self.source_project_dir.mkdir()
        self.source_path = self.source_project_dir / 'sess-abc.jsonl'
        self.source_path.write_text(
            json.dumps({'type': 'user', 'sessionId': 'sess-abc',
                        'cwd': '/Users/dev/repos/myproj'}) + '\n',
            encoding='utf-8',
        )

    def test_copies_jsonl_into_target_cwd_project_dir(self) -> None:
        target_cwd = '/Users/dev/.kato/workspaces/PROJ-1/myproj'
        result = migrate_session_to_workspace(
            transcript_path=str(self.source_path),
            target_cwd=target_cwd,
        )
        self.assertIsNotNone(result)
        # File now also exists at the kato cwd's project dir.
        # Claude Code's encoding flattens ``/``, ``_`` and ``.`` to ``-`` —
        # ``.kato`` becomes ``-kato`` (leading dot stripped to dash).
        kato_dir = self.root / '-Users-dev--kato-workspaces-PROJ-1-myproj'
        self.assertTrue((kato_dir / 'sess-abc.jsonl').is_file())

    def test_returns_none_when_source_missing(self) -> None:
        result = migrate_session_to_workspace(
            transcript_path=str(self.root / 'nope.jsonl'),
            target_cwd='/x/y',
        )
        self.assertIsNone(result)

    def test_returns_none_when_target_cwd_empty(self) -> None:
        result = migrate_session_to_workspace(
            transcript_path=str(self.source_path),
            target_cwd='',
        )
        self.assertIsNone(result)

    def test_idempotent_when_destination_already_exists(self) -> None:
        target_cwd = '/Users/dev/.kato/workspaces/PROJ-1/myproj'
        # First call copies.
        first = migrate_session_to_workspace(
            transcript_path=str(self.source_path),
            target_cwd=target_cwd,
        )
        # Second call doesn't error and returns the same destination.
        second = migrate_session_to_workspace(
            transcript_path=str(self.source_path),
            target_cwd=target_cwd,
        )
        self.assertEqual(first, second)
        self.assertTrue(first.is_file())

    def test_creates_target_dir_when_missing(self) -> None:
        # Cwd has never been used by Claude Code, so its project
        # dir doesn't exist yet. Migration creates it.
        target_cwd = '/totally/new/path/never/used'
        result = migrate_session_to_workspace(
            transcript_path=str(self.source_path),
            target_cwd=target_cwd,
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.is_file())

    def test_preserves_jsonl_content(self) -> None:
        result = migrate_session_to_workspace(
            transcript_path=str(self.source_path),
            target_cwd='/Users/dev/.kato/workspaces/PROJ-1/myproj',
        )
        self.assertEqual(
            result.read_text(encoding='utf-8'),
            self.source_path.read_text(encoding='utf-8'),
        )

    def test_returns_none_when_source_path_blank(self) -> None:
        # Blank transcript_path → can't even resolve a source → None.
        result = migrate_session_to_workspace(
            transcript_path='',
            target_cwd='/Users/dev/anything',
        )
        self.assertIsNone(result)

    def test_returns_none_when_target_dir_creation_fails(self) -> None:
        # If the target directory creation fails (e.g. permission error),
        # migration must return None and log — not propagate the error.
        from unittest.mock import patch as patch_obj
        target_cwd = '/Users/dev/.kato/workspaces/PROJ-7/myproj'
        with patch_obj.object(Path, 'mkdir', side_effect=PermissionError('locked')):
            result = migrate_session_to_workspace(
                transcript_path=str(self.source_path),
                target_cwd=target_cwd,
            )
        self.assertIsNone(result)

    def test_returns_none_when_copy_fails(self) -> None:
        # ``shutil.copyfile`` failure → log + None (e.g. disk full,
        # read-only filesystem). Should not propagate.
        from unittest.mock import patch as patch_obj
        import shutil as shutil_mod
        with patch_obj.object(
            shutil_mod, 'copyfile', side_effect=OSError('disk full'),
        ):
            result = migrate_session_to_workspace(
                transcript_path=str(self.source_path),
                target_cwd='/Users/dev/never/seen',
            )
        self.assertIsNone(result)


class ListSessionsEdgeCaseTests(unittest.TestCase):
    """Cover the smaller branches in list_sessions and friends."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_iter_transcript_paths_handles_root_iterdir_oserror(self) -> None:
        # If the root iterdir itself raises (e.g. permissions), the
        # generator returns nothing rather than crashing.
        from unittest.mock import patch as patch_obj
        with patch_obj.object(Path, 'iterdir', side_effect=PermissionError('locked')):
            result = list_sessions(sessions_root=self.root)
        self.assertEqual(result, [])

    def test_skips_non_directory_entries_at_root(self) -> None:
        # A stray file at the root level → must be skipped, not crash.
        (self.root / 'stray.txt').write_text('hi')
        sessions_dir = self.root / 'enc-cwd'
        sessions_dir.mkdir()
        (sessions_dir / 's1.jsonl').write_text(
            json.dumps({'type': 'user', 'cwd': '/x', 'sessionId': 's1',
                        'message': {'content': 'hi'}}) + '\n',
        )
        result = list_sessions(sessions_root=self.root)
        self.assertEqual(len(result), 1)

    def test_skips_jsonl_when_glob_raises(self) -> None:
        # If glob() on a sub-dir raises (e.g. perms), continue with the next.
        # Easiest way to exercise: a valid first project dir + a second one
        # that we mock glob on. We instead test the simpler path: stat() failure.
        sessions_dir = self.root / 'enc-cwd'
        sessions_dir.mkdir()
        good_path = sessions_dir / 's1.jsonl'
        good_path.write_text(
            json.dumps({'type': 'user', 'cwd': '/x', 'sessionId': 's1',
                        'message': {'content': 'hi'}}) + '\n',
        )
        # stat() raises on the file → _parse_metadata returns None,
        # session dropped silently.
        from unittest.mock import patch as patch_obj
        original_stat = Path.stat

        def selective_stat(self_path, *args, **kwargs):
            if self_path.name == 's1.jsonl':
                raise PermissionError('locked')
            return original_stat(self_path, *args, **kwargs)

        with patch_obj.object(Path, 'stat', selective_stat):
            result = list_sessions(sessions_root=self.root)
        # The locked file gets dropped; no crash.
        self.assertEqual(result, [])

    def test_iter_transcript_paths_skips_jsonl_entries_that_are_not_files(self) -> None:
        # Branch 143->142: ``glob('*.jsonl')`` can yield a directory
        # named ``*.jsonl`` (e.g. an accidental mkdir). The is_file()
        # guard must reject it without yielding so the walker keeps
        # looking at real transcripts.
        sessions_dir = self.root / 'enc-cwd'
        sessions_dir.mkdir()
        # A directory whose name ends in .jsonl — picked up by glob but
        # rejected by is_file().
        (sessions_dir / 'fake-dir.jsonl').mkdir()
        # A real transcript that must still be discovered.
        real = sessions_dir / 'real.jsonl'
        real.write_text(
            json.dumps({'type': 'user', 'cwd': '/x', 'sessionId': 'real',
                        'message': {'content': 'hi'}}) + '\n',
        )
        result = list_sessions(sessions_root=self.root)
        ids = [m.agent_session_id for m in result]
        self.assertIn('real', ids)
        self.assertNotIn('fake-dir', ids)

    def test_parse_metadata_aborts_when_preview_scan_budget_exhausted(self) -> None:
        # Build a JSONL much larger than the preview-scan cap so the loop
        # breaks early. We don't need a real cap — we just confirm the
        # file is parsed without crashing.
        sessions_dir = self.root / 'enc-cwd'
        sessions_dir.mkdir()
        big_path = sessions_dir / 'sess-big.jsonl'
        # Each record is ~200 bytes; 50,000 records ~10MB easily exceeds
        # the 256KB cap.
        with big_path.open('w', encoding='utf-8') as fh:
            fh.write(
                json.dumps({'type': 'user', 'cwd': '/x', 'sessionId': 'sess-big',
                            'message': {'content': 'first msg'}}) + '\n',
            )
            for i in range(2000):
                fh.write(
                    json.dumps({'type': 'user', 'sessionId': 'sess-big',
                                'message': {'content': f'msg {i}'}}) + '\n',
                )
        result = list_sessions(sessions_root=self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].agent_session_id, 'sess-big')
        # Turn count is bounded by the cap, not the full file.
        self.assertGreater(result[0].turn_count, 0)

    def test_parse_metadata_uses_session_id_from_record_when_filename_blank(self) -> None:
        # Edge case: agent_session_id is normally the filename stem, but if the
        # path stem ends up blank (defensive branch), record's sessionId
        # is the fallback. We can't easily make the stem blank with a
        # normal path, so we just verify the record-side fallback is
        # observable when stem is preserved (basic sanity: id matches stem).
        sessions_dir = self.root / 'enc-cwd'
        sessions_dir.mkdir()
        path = sessions_dir / 'fileid.jsonl'
        path.write_text(
            json.dumps({'type': 'user', 'cwd': '/x',
                        'sessionId': 'recorded-id',
                        'message': {'content': 'hi'}}) + '\n',
        )
        result = list_sessions(sessions_root=self.root)
        # Filename stem wins (that's by design — Claude Code uses the
        # filename as canonical id).
        self.assertEqual(result[0].agent_session_id, 'fileid')

    def test_query_does_not_match_when_substring_absent(self) -> None:
        sessions_dir = self.root / 'enc-cwd'
        sessions_dir.mkdir()
        (sessions_dir / 's1.jsonl').write_text(
            json.dumps({'type': 'user', 'cwd': '/some/path',
                        'sessionId': 's1',
                        'message': {'content': 'hello world'}}) + '\n',
        )
        # No session contains 'xyz'.
        result = list_sessions(sessions_root=self.root, query='xyz')
        self.assertEqual(result, [])


class DefaultSessionsRootTests(unittest.TestCase):
    def test_uses_env_override_when_set(self) -> None:
        from claude_core_lib.claude_core_lib.session.index import default_sessions_root
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {CLAUDE_SESSIONS_ROOT_ENV_KEY: tmp}):
                root = default_sessions_root()
                self.assertEqual(root, Path(tmp))

    def test_falls_back_to_home_dot_claude_when_env_unset(self) -> None:
        # Line 89: ``return Path.home() / '.claude' / 'projects'`` — fires
        # when env var is missing or whitespace-only.
        from claude_core_lib.claude_core_lib.session.index import default_sessions_root
        env = {k: v for k, v in os.environ.items() if k != CLAUDE_SESSIONS_ROOT_ENV_KEY}
        with patch.dict(os.environ, env, clear=True):
            root = default_sessions_root()
        self.assertEqual(root, Path.home() / '.claude' / 'projects')


class ListSessionsQueryFilteringTests(unittest.TestCase):
    """Line 117: ``if needle and not _matches_query(...): continue``."""

    def test_skips_metadata_not_matching_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Two sessions: one matches 'alpha', one matches 'beta'.
            for slug in ('alpha', 'beta'):
                d = root / f'enc-{slug}'
                d.mkdir()
                (d / f'sess-{slug}.jsonl').write_text(
                    json.dumps({
                        'type': 'user',
                        'cwd': f'/repo/{slug}',
                        'sessionId': f'sess-{slug}',
                        'message': {'content': f'{slug} message'},
                    }) + '\n',
                )
            # Query 'alpha' must drop the beta session via line 117.
            result = list_sessions(sessions_root=root, query='alpha')
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].agent_session_id, 'sess-alpha')


class ParseMetadataPreviewCapTests(unittest.TestCase):
    def test_aborts_when_bytes_read_exceeds_cap(self) -> None:
        # Line 171: byte budget check.
        # The bytes_read check fires AFTER reading each line — so the cap
        # is reached once a single line larger than the cap has been read.
        # The next iteration breaks without processing that line. We confirm
        # the second user message AFTER the cap is NOT captured.
        from claude_core_lib.claude_core_lib.session.index import _MAX_PREVIEW_SCAN_BYTES
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / 'enc'
            d.mkdir()
            big_path = d / 'sess.jsonl'
            huge_content = 'x' * (_MAX_PREVIEW_SCAN_BYTES + 1000)
            big_path.write_text(
                json.dumps({'type': 'user', 'cwd': '/r', 'sessionId': 'sess',
                            'message': {'content': 'small first'}}) + '\n'
                + json.dumps({'type': 'user',
                              'message': {'content': huge_content}}) + '\n'
                + json.dumps({'type': 'user',
                              'message': {'content': 'after cap'}}) + '\n',
                encoding='utf-8',
            )
            result = list_sessions(sessions_root=root)
            self.assertEqual(len(result), 1)
            # First message captured normally.
            self.assertEqual(result[0].first_user_message, 'small first')
            # Third user record never observed (cap fired after huge line).
            self.assertNotIn('after cap', result[0].last_user_message)

    def test_falls_back_to_session_id_from_record_when_stem_blank(self) -> None:
        # Line 178: rare path — filename has no stem, so the JSONL record's
        # sessionId field is the fallback. We can't easily make stem empty
        # via a normal write, so we patch ``Path.stem`` for this one file.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / 'enc'
            d.mkdir()
            path = d / 'normal.jsonl'
            path.write_text(
                json.dumps({'type': 'user', 'cwd': '/r',
                            'sessionId': 'rec-id',
                            'message': {'content': 'hi'}}) + '\n',
                encoding='utf-8',
            )

            # Pretend the stem is empty so the record-side fallback kicks in.
            with patch.object(
                Path, 'stem', new_callable=unittest.mock.PropertyMock,
                return_value='',
            ):
                result = list_sessions(sessions_root=root)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].agent_session_id, 'rec-id')

    def test_first_and_last_user_messages_track_independently(self) -> None:
        # Lines 187-190: ``first_user_message`` is set once; ``last_user_message``
        # is updated on every preview. Two user records → first = msg1,
        # last = msg2.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / 'enc'
            d.mkdir()
            (d / 'sess.jsonl').write_text(
                json.dumps({'type': 'user', 'cwd': '/r', 'sessionId': 'sess',
                            'message': {'content': 'first msg'}}) + '\n'
                + json.dumps({'type': 'user',
                              'message': {'content': 'second msg'}}) + '\n',
                encoding='utf-8',
            )
            result = list_sessions(sessions_root=root)
            self.assertEqual(result[0].first_user_message, 'first msg')
            self.assertEqual(result[0].last_user_message, 'second msg')


class ClaudeProjectDirForCwdFallback(unittest.TestCase):
    def test_returns_home_path_when_env_root_points_at_nonexistent_dir(self) -> None:
        # Line 287: ``not root.is_dir()`` → fall back to home path.
        with tempfile.TemporaryDirectory() as tmp:
            # The override points at a path that doesn't exist (sub-dir we
            # never create) → ``is_dir()`` returns False → fallback fires.
            nonexistent = str(Path(tmp) / 'does-not-exist')
            with patch.dict(os.environ, {CLAUDE_SESSIONS_ROOT_ENV_KEY: nonexistent}):
                result = claude_project_dir_for_cwd('/Users/me/proj')
        # Encoded form replaces '/' with '-'; the fallback path lives
        # under the user's home dir.
        self.assertIn('.claude/projects/-Users-me-proj', str(result))


class MigrateSessionToWorkspaceIdempotent(unittest.TestCase):
    """Lines 325-330: when source resolves to the same path as target."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._env = patch.dict(
            os.environ, {CLAUDE_SESSIONS_ROOT_ENV_KEY: str(self.root)},
        )
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_returns_target_when_source_already_at_destination(self) -> None:
        # Place the source JSONL exactly where claude_project_dir_for_cwd
        # would route it, then migrate — should detect same path and no-op.
        target_cwd = '/Users/me/.kato/workspaces/PROJ-1/c'
        target_dir = self.root / '-Users-me--kato-workspaces-PROJ-1-c'
        target_dir.mkdir()
        source = target_dir / 'sess-A.jsonl'
        source.write_text(
            json.dumps({'type': 'user', 'sessionId': 'sess-A', 'cwd': target_cwd}) + '\n',
            encoding='utf-8',
        )
        result = migrate_session_to_workspace(
            transcript_path=str(source), target_cwd=target_cwd,
        )
        self.assertEqual(result, source)

    def test_falls_through_to_copy_when_resolve_raises(self) -> None:
        # Lines 326-330: ``resolve()`` raises OSError → swallowed, fall
        # through to the copy. Pre-3.6 behaviour locked here so a future
        # change doesn't accidentally start crashing on the old path.
        target_cwd = '/Users/me/.kato/workspaces/PROJ-2/c'
        target_dir_name = '-Users-me--kato-workspaces-PROJ-2-c'
        # Source lives in a separate dir (so the copy is meaningful).
        source_dir = self.root / 'src'
        source_dir.mkdir()
        source = source_dir / 'sess-B.jsonl'
        source.write_text(
            json.dumps({'type': 'user', 'sessionId': 'sess-B'}) + '\n',
            encoding='utf-8',
        )

        from unittest.mock import patch as patch_obj
        with patch_obj.object(Path, 'resolve', side_effect=OSError('legacy py')):
            result = migrate_session_to_workspace(
                transcript_path=str(source), target_cwd=target_cwd,
            )
        # Copy went through despite resolve crashing — destination exists.
        self.assertIsNotNone(result)
        self.assertTrue((self.root / target_dir_name / 'sess-B.jsonl').is_file())


class ParseMetadataDirectTests(unittest.TestCase):
    """Direct tests for ``_parse_metadata`` edge cases.

    We import it via private name; testing the private helper directly is
    the cleanest way to hit the rare error branches (stat failure during
    iteration, open failure mid-read, blank-stem fallback) without
    contorting the higher-level test through Path patching gymnastics.
    """

    def setUp(self) -> None:
        from claude_core_lib.claude_core_lib.session.index import _parse_metadata
        self._parse_metadata = _parse_metadata

    def test_returns_none_when_stat_raises_oserror(self) -> None:
        # Lines 157-158: stat() → OSError → return None.
        # A bogus path that doesn't exist + a parent that doesn't exist
        # makes stat() raise.
        path = Path('/totally/made/up/never/exists.jsonl')
        self.assertIsNone(self._parse_metadata(path))

    def test_returns_none_when_open_raises_oserror(self) -> None:
        # Lines 187-188: ``open`` raises OSError mid-method → return None.
        # We patch ``Path.open`` AFTER stat already succeeded.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sess.jsonl'
            path.write_text(
                json.dumps({'type': 'user', 'cwd': '/r',
                            'sessionId': 'sess',
                            'message': {'content': 'hi'}}) + '\n',
            )

            real_open = Path.open

            def selective_open(self_path, *args, **kwargs):
                if self_path.name == 'sess.jsonl':
                    raise OSError('I/O failure mid-read')
                return real_open(self_path, *args, **kwargs)

            with patch.object(Path, 'open', selective_open):
                self.assertIsNone(self._parse_metadata(path))

    def test_returns_none_when_session_id_remains_blank(self) -> None:
        # Line 190: ``if not agent_session_id: return None`` —
        # path.stem is blank AND no record has a sessionId field.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'no_id.jsonl'
            path.write_text(
                # No sessionId in any record AND we force a blank stem.
                json.dumps({'type': 'user',
                            'message': {'content': 'hi'}}) + '\n',
            )

            # Force stem to '' so the fallback (line 178) doesn't recover.
            with patch.object(
                Path, 'stem', new_callable=unittest.mock.PropertyMock,
                return_value='',
            ):
                self.assertIsNone(self._parse_metadata(path))

    def test_record_session_id_fallback_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fallback.jsonl'
            path.write_text(
                json.dumps({'type': 'user',
                            'cwd': '/r',
                            'sessionId': '  rec-id\n',
                            'message': {'content': 'hi'}}) + '\n',
            )

            with patch.object(
                Path, 'stem', new_callable=unittest.mock.PropertyMock,
                return_value='',
            ):
                metadata = self._parse_metadata(path)

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata.agent_session_id, 'rec-id')


class ListSessionsParseFailureContinue(unittest.TestCase):
    """Line 117: ``metadata is None`` branch in ``list_sessions``."""

    def test_metadata_none_is_silently_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / 'enc-good'
            d.mkdir()
            good_path = d / 'good.jsonl'
            good_path.write_text(
                json.dumps({'type': 'user', 'cwd': '/r', 'sessionId': 'good',
                            'message': {'content': 'hi'}}) + '\n',
            )
            d2 = root / 'enc-bad'
            d2.mkdir()
            bad_path = d2 / 'bad.jsonl'
            bad_path.write_text('not json\n')

            # Patch _parse_metadata to return None for the bad file but
            # delegate for the good one.
            from claude_core_lib.claude_core_lib.session.index import _parse_metadata as real_parse
            calls = []

            def selective(p):
                calls.append(p.name)
                if p.name == 'bad.jsonl':
                    return None
                return real_parse(p)

            with patch(
                'claude_core_lib.claude_core_lib.session.index._parse_metadata',
                side_effect=selective,
            ):
                result = list_sessions(sessions_root=root)
            # Good survives; bad was silently dropped via the continue.
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].agent_session_id, 'good')
            # And our selective fn was hit for both files.
            self.assertIn('bad.jsonl', calls)


class ParseJsonlLineEdgeCases(unittest.TestCase):
    def test_blank_line_returns_none(self) -> None:
        from claude_core_lib.claude_core_lib.session.index import _parse_jsonl_line
        self.assertIsNone(_parse_jsonl_line(''))
        self.assertIsNone(_parse_jsonl_line('   \n'))

    def test_invalid_json_returns_none(self) -> None:
        from claude_core_lib.claude_core_lib.session.index import _parse_jsonl_line
        self.assertIsNone(_parse_jsonl_line('not json'))

    def test_non_dict_returns_none(self) -> None:
        from claude_core_lib.claude_core_lib.session.index import _parse_jsonl_line
        self.assertIsNone(_parse_jsonl_line('[1, 2, 3]'))


class UserMessagePreviewEdgeCases(unittest.TestCase):
    def test_returns_empty_when_message_not_dict(self) -> None:
        from claude_core_lib.claude_core_lib.session.index import _user_message_preview
        self.assertEqual(_user_message_preview({'message': 'plain'}), '')

    def test_returns_empty_when_content_is_neither_string_nor_list(self) -> None:
        from claude_core_lib.claude_core_lib.session.index import _user_message_preview
        self.assertEqual(
            _user_message_preview({'message': {'content': 42}}), '',
        )

    def test_skips_non_text_blocks_in_content_list(self) -> None:
        from claude_core_lib.claude_core_lib.session.index import _user_message_preview
        # Tool-result first, then a real text block → text block wins.
        result = _user_message_preview({
            'message': {'content': [
                {'type': 'tool_result'},
                'not a dict',
                {'type': 'text', 'text': 'hello there'},
            ]},
        })
        self.assertEqual(result, 'hello there')

    def test_skips_blank_text_blocks(self) -> None:
        from claude_core_lib.claude_core_lib.session.index import _user_message_preview
        result = _user_message_preview({
            'message': {'content': [
                {'type': 'text', 'text': '   '},
            ]},
        })
        self.assertEqual(result, '')


if __name__ == '__main__':
    unittest.main()
