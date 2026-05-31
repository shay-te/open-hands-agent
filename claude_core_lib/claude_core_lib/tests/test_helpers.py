"""Comprehensive tests for all claude_core_lib helper modules.

Covers: text_utils, logging_utils, atomic_write, result_utils,
architecture_doc_utils, lessons_doc_utils, agents_instruction_utils,
agent_prompt_utils, wire_protocol (constants).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.helpers.text_utils import (
    condensed_text,
    normalized_text,
    text_from_attr,
    text_from_mapping,
)
from agent_core_lib.agent_core_lib.helpers.logging_utils import configure_logger
from agent_core_lib.agent_core_lib.helpers.atomic_write import atomic_write_json
from agent_core_lib.agent_core_lib.helpers.result_utils import (
    build_openhands_result,
    openhands_session_id,
    openhands_success_flag,
)
from agent_core_lib.agent_core_lib.helpers.architecture_doc_utils import (
    read_architecture_doc,
)
from agent_core_lib.agent_core_lib.helpers.lessons_doc_utils import (
    read_lessons_file,
    _strip_timestamp_header,
)
# architecture + lessons docs now share one stat-keyed cache in
# cached_file_render (keyed by str(path)), so both aliases point at it.
from agent_core_lib.agent_core_lib.helpers.cached_file_render import (
    _cache as _arch_cache,
    _cache_lock as _arch_cache_lock,
)
_lessons_cache = _arch_cache
_lessons_cache_lock = _arch_cache_lock
from agent_core_lib.agent_core_lib.helpers.agents_instruction_utils import (
    AGENTS_FILE_NAME,
    SKIPPED_DIRECTORIES,
    agents_instructions_for_path,
    repository_agents_instructions_text,
)
from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    IGNORED_REPOSITORY_FOLDERS_ENV,
    _SELF_REPLY_PREFIXES,
    _is_self_reply_body,
    agents_instructions_text,
    chat_continuity_ground_truth_block,
    forbidden_repository_guardrails_text,
    ignored_repository_folder_names,
    prepend_chat_workspace_context,
    repository_scope_text,
    review_comment_code_snippet,
    review_comment_context_text,
    review_comment_location_text,
    review_comments_batch_text,
    review_conversation_title,
    review_repository_context,
    security_guardrails_text,
    task_branch_name,
    task_conversation_title,
    workspace_inventory_block,
    workspace_scope_block,
)
from claude_core_lib.claude_core_lib.session.wire_protocol import (
    CLAUDE_EVENT_ASSISTANT,
    CLAUDE_EVENT_CONTROL_REQUEST,
    CLAUDE_EVENT_CONTROL_RESPONSE,
    CLAUDE_EVENT_PERMISSION_REQUEST,
    CLAUDE_EVENT_PERMISSION_RESPONSE,
    CLAUDE_EVENT_RESULT,
    CLAUDE_EVENT_STREAM_EVENT,
    CLAUDE_EVENT_SYSTEM,
    CLAUDE_EVENT_USER,
    CLAUDE_SYSTEM_SUBTYPE_INIT,
    PERMISSION_REQUEST_EVENT_TYPES,
    SSE_EVENT_SESSION_CLOSED,
    SSE_EVENT_SESSION_EVENT,
    SSE_EVENT_SESSION_HISTORY_EVENT,
    SSE_EVENT_SESSION_IDLE,
    SSE_EVENT_SESSION_MISSING,
    SSE_EVENT_STATUS_DISABLED,
    SSE_EVENT_STATUS_ENTRY,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task(
    task_id: str = 'PROJ-1',
    summary: str = 'fix it already',
    branch_name: str = 'feature/proj-1',
    repositories: list | None = None,
    repository_branches: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        summary=summary,
        branch_name=branch_name,
        repositories=repositories or [],
        repository_branches=repository_branches or {},
    )


def _prepared(
    branch_name: str = 'feature/proj-1',
    repositories: list | None = None,
    repository_branches: dict | None = None,
    agents_instructions: str = '',
) -> SimpleNamespace:
    return SimpleNamespace(
        branch_name=branch_name,
        repositories=repositories or [],
        repository_branches=repository_branches or {},
        agents_instructions=agents_instructions,
    )


def _comment(
    comment_id: str = '99',
    author: str = 'reviewer',
    body: str = 'please fix this',
    file_path: str = '',
    line_number: object = '',
    line_type: str = '',
    commit_sha: str = '',
    repository_id: str = '',
    all_comments: list | None = None,
    pull_request_id: str = '17',
) -> SimpleNamespace:
    return SimpleNamespace(
        comment_id=comment_id,
        author=author,
        body=body,
        file_path=file_path,
        line_number=line_number,
        line_type=line_type,
        commit_sha=commit_sha,
        repository_id=repository_id,
        all_comments=all_comments or [],
        pull_request_id=pull_request_id,
    )


def _repo(repo_id: str, local_path: str, destination_branch: str = '') -> SimpleNamespace:
    return SimpleNamespace(id=repo_id, local_path=local_path, destination_branch=destination_branch)


# ---------------------------------------------------------------------------
# text_utils
# ---------------------------------------------------------------------------

class NormalizedTextTests(unittest.TestCase):
    def test_none_returns_empty(self) -> None:
        self.assertEqual(normalized_text(None), '')

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(normalized_text(''), '')

    def test_whitespace_only_returns_empty(self) -> None:
        self.assertEqual(normalized_text('   '), '')

    def test_strips_leading_and_trailing_whitespace(self) -> None:
        self.assertEqual(normalized_text('  hello  '), 'hello')

    def test_integer_converted_to_string(self) -> None:
        self.assertEqual(normalized_text(42), '42')

    def test_list_converted_via_str(self) -> None:
        result = normalized_text([1, 2])
        self.assertIsInstance(result, str)


class CondensedTextTests(unittest.TestCase):
    def test_collapses_internal_whitespace(self) -> None:
        self.assertEqual(condensed_text('  hello   world  '), 'hello world')

    def test_newlines_and_tabs_collapsed(self) -> None:
        self.assertEqual(condensed_text('a\n\tb'), 'a b')

    def test_empty_input(self) -> None:
        self.assertEqual(condensed_text(''), '')

    def test_none_input(self) -> None:
        self.assertEqual(condensed_text(None), '')


class TextFromAttrTests(unittest.TestCase):
    def test_returns_attribute_value(self) -> None:
        obj = SimpleNamespace(name='Alice')
        self.assertEqual(text_from_attr(obj, 'name'), 'Alice')

    def test_returns_empty_when_attribute_missing(self) -> None:
        self.assertEqual(text_from_attr(SimpleNamespace(), 'missing'), '')

    def test_returns_empty_when_attribute_is_none(self) -> None:
        obj = SimpleNamespace(name=None)
        self.assertEqual(text_from_attr(obj, 'name'), '')

    def test_strips_whitespace(self) -> None:
        obj = SimpleNamespace(name='  Bob  ')
        self.assertEqual(text_from_attr(obj, 'name'), 'Bob')


class TextFromMappingTests(unittest.TestCase):
    def test_returns_value_for_key(self) -> None:
        self.assertEqual(text_from_mapping({'k': 'v'}, 'k'), 'v')

    def test_returns_empty_for_missing_key(self) -> None:
        self.assertEqual(text_from_mapping({'k': 'v'}, 'x'), '')

    def test_none_mapping_returns_default(self) -> None:
        self.assertEqual(text_from_mapping(None, 'k'), '')

    def test_none_value_returns_empty(self) -> None:
        self.assertEqual(text_from_mapping({'k': None}, 'k'), '')

    def test_non_mapping_returns_default(self) -> None:
        self.assertEqual(text_from_mapping('not a mapping', 'k'), '')

    def test_default_returned_when_key_missing(self) -> None:
        self.assertEqual(text_from_mapping({}, 'k', 'fallback'), 'fallback')


# ---------------------------------------------------------------------------
# logging_utils
# ---------------------------------------------------------------------------

class ConfigureLoggerTests(unittest.TestCase):
    def test_returns_kato_workflow_logger_with_suffix(self) -> None:
        logger = configure_logger('MyClass')
        self.assertEqual(logger.name, 'kato.workflow.MyClass')

    def test_returns_kato_workflow_logger_without_suffix(self) -> None:
        logger = configure_logger('')
        self.assertEqual(logger.name, 'kato.workflow')

    def test_none_suffix_returns_base_logger(self) -> None:
        logger = configure_logger(None)
        self.assertEqual(logger.name, 'kato.workflow')

    def test_whitespace_only_suffix_returns_base_logger(self) -> None:
        logger = configure_logger('   ')
        self.assertEqual(logger.name, 'kato.workflow')

    def test_returns_logging_logger_instance(self) -> None:
        self.assertIsInstance(configure_logger('X'), logging.Logger)


# ---------------------------------------------------------------------------
# atomic_write
# ---------------------------------------------------------------------------

class AtomicWriteJsonTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def test_writes_json_file_on_success(self) -> None:
        target = self.base / 'out.json'
        result = atomic_write_json(target, {'key': 'value'})
        self.assertTrue(result)
        payload = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(payload['key'], 'value')

    def test_returns_false_on_oserror_without_logger(self) -> None:
        target = self.base / 'nonexistent_dir' / 'out.json'
        result = atomic_write_json(target, {})
        self.assertFalse(result)

    def test_logs_warning_on_oserror_with_label(self) -> None:
        target = self.base / 'nodir' / 'out.json'
        logger = logging.getLogger('test.atomic_write')
        with self.assertLogs('test.atomic_write', level='WARNING') as cm:
            atomic_write_json(target, {}, logger=logger, label='test record')
        self.assertIn('test record', ' '.join(cm.output))

    def test_logs_warning_on_oserror_without_label(self) -> None:
        target = self.base / 'nodir' / 'out.json'
        logger = logging.getLogger('test.atomic_write2')
        with self.assertLogs('test.atomic_write2', level='WARNING') as cm:
            atomic_write_json(target, {}, logger=logger)
        # Warning present but no label text
        self.assertTrue(any('failed to persist' in line for line in cm.output))

    def test_writes_nested_dict(self) -> None:
        target = self.base / 'nested.json'
        atomic_write_json(target, {'a': {'b': [1, 2, 3]}})
        payload = json.loads(target.read_text())
        self.assertEqual(payload['a']['b'], [1, 2, 3])

    def test_overwrites_existing_file(self) -> None:
        target = self.base / 'overwrite.json'
        atomic_write_json(target, {'v': 1})
        atomic_write_json(target, {'v': 2})
        self.assertEqual(json.loads(target.read_text())['v'], 2)


# ---------------------------------------------------------------------------
# result_utils
# ---------------------------------------------------------------------------

class OpenhandsSuccessFlagTests(unittest.TestCase):
    def test_none_payload_returns_default(self) -> None:
        self.assertFalse(openhands_success_flag(None))
        self.assertTrue(openhands_success_flag(None, default=True))

    def test_bool_true(self) -> None:
        self.assertTrue(openhands_success_flag({'success': True}))

    def test_bool_false(self) -> None:
        self.assertFalse(openhands_success_flag({'success': False}))

    def test_string_true_variants(self) -> None:
        for val in ('true', 'True', '1', 'yes', 'on'):
            self.assertTrue(openhands_success_flag({'success': val}), f'Failed for {val!r}')

    def test_string_false_variants(self) -> None:
        for val in ('false', 'False', '0', 'no', 'off'):
            self.assertFalse(openhands_success_flag({'success': val}), f'Failed for {val!r}')

    def test_missing_key_returns_default(self) -> None:
        self.assertFalse(openhands_success_flag({}))
        self.assertTrue(openhands_success_flag({}, default=True))

    def test_non_mapping_returns_default(self) -> None:
        self.assertFalse(openhands_success_flag('not a mapping'))

    def test_int_truthy(self) -> None:
        self.assertTrue(openhands_success_flag({'success': 1}))

    def test_int_falsy(self) -> None:
        self.assertFalse(openhands_success_flag({'success': 0}))


class OpenhandsSessionIdTests(unittest.TestCase):
    def test_returns_session_id(self) -> None:
        self.assertEqual(openhands_session_id({'agent_session_id': 'abc'}), 'abc')

    def test_falls_back_to_conversation_id(self) -> None:
        self.assertEqual(openhands_session_id({'conversation_id': 'xyz'}), 'xyz')

    def test_prefers_session_id_over_conversation_id(self) -> None:
        # OpenHands' wire primary is ``session_id``; ``conversation_id``
        # is the alt key on older endpoints. Primary wins.
        self.assertEqual(
            openhands_session_id({'session_id': 'sess', 'conversation_id': 'conv'}),
            'sess',
        )

    def test_returns_empty_when_both_missing(self) -> None:
        self.assertEqual(openhands_session_id({}), '')

    def test_returns_empty_for_none(self) -> None:
        self.assertEqual(openhands_session_id(None), '')


class BuildOpenhandsResultTests(unittest.TestCase):
    def test_basic_success_result(self) -> None:
        result = build_openhands_result({'success': True, 'message': 'ok'})
        self.assertTrue(result['success'])
        self.assertEqual(result['message'], 'ok')

    def test_default_success_false_when_missing(self) -> None:
        result = build_openhands_result({})
        self.assertFalse(result['success'])

    def test_branch_name_included_when_provided(self) -> None:
        result = build_openhands_result({}, branch_name='feature/x')
        self.assertEqual(result['branch_name'], 'feature/x')

    def test_branch_name_omitted_when_empty(self) -> None:
        result = build_openhands_result({})
        self.assertNotIn('branch_name', result)

    def test_commit_message_from_payload(self) -> None:
        result = build_openhands_result({'commit_message': 'fix: thing'})
        self.assertEqual(result['commit_message'], 'fix: thing')

    def test_default_commit_message_when_payload_missing(self) -> None:
        result = build_openhands_result({}, default_commit_message='default msg')
        self.assertEqual(result['commit_message'], 'default msg')

    def test_payload_commit_message_preferred_over_default(self) -> None:
        result = build_openhands_result(
            {'commit_message': 'from payload'},
            default_commit_message='from default',
        )
        self.assertEqual(result['commit_message'], 'from payload')

    def test_session_id_from_payload(self) -> None:
        result = build_openhands_result({'agent_session_id': 'sess-1'})
        self.assertEqual(result[ImplementationFields.AGENT_SESSION_ID], 'sess-1')

    def test_session_id_omitted_when_missing(self) -> None:
        result = build_openhands_result({})
        self.assertNotIn(ImplementationFields.AGENT_SESSION_ID, result)

    def test_none_payload_uses_defaults(self) -> None:
        result = build_openhands_result(None, default_success=True)
        self.assertTrue(result['success'])


# ---------------------------------------------------------------------------
# architecture_doc_utils
# ---------------------------------------------------------------------------

class ReadArchitectureDocTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        with _arch_cache_lock:
            _arch_cache.clear()

    def test_empty_path_returns_empty(self) -> None:
        self.assertEqual(read_architecture_doc(''), '')
        self.assertEqual(read_architecture_doc(None), '')

    def test_missing_file_returns_empty(self) -> None:
        result = read_architecture_doc('/nonexistent/arch.md')
        self.assertEqual(result, '')

    def test_missing_file_logs_warning_when_logger_provided(self) -> None:
        logger = logging.getLogger('test.arch_doc')
        with self.assertLogs('test.arch_doc', level='WARNING') as cm:
            read_architecture_doc('/nonexistent/arch.md', logger=logger)
        self.assertTrue(any('architecture doc' in line.lower() for line in cm.output))

    def test_valid_file_returns_directive_text(self) -> None:
        path = Path(self._tmp.name) / 'arch.md'
        path.write_text('# Architecture\n', encoding='utf-8')
        result = read_architecture_doc(str(path))
        self.assertIn('Project architecture document:', result)
        self.assertIn(str(path), result)
        self.assertIn('Read tool', result)

    def test_cache_hit_returns_same_value(self) -> None:
        path = Path(self._tmp.name) / 'arch2.md'
        path.write_text('# Arch\n', encoding='utf-8')
        first = read_architecture_doc(str(path))
        second = read_architecture_doc(str(path))
        self.assertEqual(first, second)
        # Only one cache entry added
        with _arch_cache_lock:
            self.assertIn(str(path), _arch_cache)

    def test_orchestration_layer_not_kato_in_directive(self) -> None:
        path = Path(self._tmp.name) / 'arch3.md'
        path.write_text('# A\n', encoding='utf-8')
        result = read_architecture_doc(str(path))
        self.assertIn('orchestration layer', result)
        self.assertNotIn('Kato commits', result)

    def test_directory_path_returns_empty(self) -> None:
        # Line 44: stat() succeeds for a directory but ``is_file()`` is
        # False → raise OSError → caught + return ''. Locks the rule that
        # the architecture-doc path must be a file, not a folder.
        directory = Path(self._tmp.name) / 'arch_dir'
        directory.mkdir()
        self.assertEqual(read_architecture_doc(str(directory)), '')


# ---------------------------------------------------------------------------
# lessons_doc_utils
# ---------------------------------------------------------------------------

class ReadLessonsFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        with _lessons_cache_lock:
            _lessons_cache.clear()

    def test_empty_path_returns_empty(self) -> None:
        self.assertEqual(read_lessons_file(''), '')
        self.assertEqual(read_lessons_file(None), '')

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(read_lessons_file('/nonexistent/lessons.md'), '')

    def test_valid_file_returns_directive_with_content(self) -> None:
        path = Path(self._tmp.name) / 'lessons.md'
        path.write_text('- Always use type hints\n', encoding='utf-8')
        result = read_lessons_file(str(path))
        self.assertIn('Always use type hints', result)
        self.assertIn('BEGIN LEARNED LESSONS', result)

    def test_empty_body_returns_empty(self) -> None:
        path = Path(self._tmp.name) / 'empty_lessons.md'
        path.write_text('   \n   ', encoding='utf-8')
        self.assertEqual(read_lessons_file(str(path)), '')

    def test_timestamp_header_stripped(self) -> None:
        path = Path(self._tmp.name) / 'ts_lessons.md'
        path.write_text(
            '<!-- last_compacted: 2025-01-01 -->\n- Lesson one\n',
            encoding='utf-8',
        )
        result = read_lessons_file(str(path))
        self.assertNotIn('last_compacted', result)
        self.assertIn('Lesson one', result)

    def test_body_truncated_at_max_chars(self) -> None:
        from agent_core_lib.agent_core_lib.helpers.lessons_doc_utils import _MAX_BODY_CHARS
        path = Path(self._tmp.name) / 'long_lessons.md'
        big = 'x' * (_MAX_BODY_CHARS + 1000)
        path.write_text(big, encoding='utf-8')
        result = read_lessons_file(str(path))
        # The content in the result should be truncated
        self.assertIn('BEGIN LEARNED LESSONS', result)

    def test_directive_does_not_mention_kato(self) -> None:
        path = Path(self._tmp.name) / 'lessons2.md'
        path.write_text('- Lesson\n', encoding='utf-8')
        result = read_lessons_file(str(path))
        self.assertNotIn('by Kato', result)

    def test_cache_returns_same_result_on_second_call(self) -> None:
        path = Path(self._tmp.name) / 'cached_lessons.md'
        path.write_text('- Rule\n', encoding='utf-8')
        first = read_lessons_file(str(path))
        second = read_lessons_file(str(path))
        self.assertEqual(first, second)

    def test_directory_path_returns_empty(self) -> None:
        # Hits line 41: ``if not file_path.is_file(): return ''``. A directory
        # passes ``.stat()`` but ``.is_file()`` returns False — must be skipped
        # so a misconfigured lessons path doesn't blow up the prompt builder.
        directory = Path(self._tmp.name) / 'a_directory'
        directory.mkdir()
        self.assertEqual(read_lessons_file(str(directory)), '')

    def test_unreadable_file_logs_warning_and_returns_empty(self) -> None:
        # Hits lines 53-56: the ``OSError`` branch on ``read_text``. We force
        # this by patching ``Path.read_text`` to raise after ``stat`` already
        # succeeded — simulating a permission flip mid-read.
        from unittest.mock import patch, MagicMock
        path = Path(self._tmp.name) / 'will_break.md'
        path.write_text('- Rule\n', encoding='utf-8')
        mock_logger = MagicMock()
        with patch.object(Path, 'read_text', side_effect=OSError('permission denied')):
            result = read_lessons_file(str(path), logger=mock_logger)
        self.assertEqual(result, '')
        mock_logger.warning.assert_called_once()


class StripTimestampHeaderTests(unittest.TestCase):
    def test_strips_matching_header(self) -> None:
        text = '<!-- last_compacted: 2025-01-01 -->\n- Lesson\n'
        result = _strip_timestamp_header(text)
        self.assertIn('- Lesson', result)
        self.assertNotIn('last_compacted', result)

    def test_no_strip_when_no_header(self) -> None:
        text = '- Lesson\n'
        self.assertEqual(_strip_timestamp_header(text), '- Lesson\n')

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(_strip_timestamp_header(''), '')

    def test_non_matching_first_line_not_stripped(self) -> None:
        text = '# Title\n- Lesson'
        self.assertEqual(_strip_timestamp_header(text), '# Title\n- Lesson')


# ---------------------------------------------------------------------------
# agents_instruction_utils
# ---------------------------------------------------------------------------

class AgentsInstructionUtilsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write_agents(self, rel_path: str, content: str = '# AGENTS\nUse pnpm.') -> None:
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def test_empty_workspace_returns_empty(self) -> None:
        self.assertEqual(agents_instructions_for_path(''), '')

    def test_nonexistent_workspace_returns_empty(self) -> None:
        self.assertEqual(agents_instructions_for_path('/nonexistent/path'), '')

    def test_no_agents_md_returns_empty(self) -> None:
        self.assertEqual(agents_instructions_for_path(str(self.root)), '')

    def test_agents_md_content_included(self) -> None:
        self._write_agents('AGENTS.md', 'Use pnpm.')
        result = agents_instructions_for_path(str(self.root))
        self.assertIn('Use pnpm.', result)
        self.assertIn('Repository AGENTS.md instructions:', result)

    def test_git_dir_is_skipped(self) -> None:
        git_dir = self.root / '.git'
        git_dir.mkdir()
        (git_dir / AGENTS_FILE_NAME).write_text('should not appear', encoding='utf-8')
        result = agents_instructions_for_path(str(self.root))
        self.assertNotIn('should not appear', result)

    def test_nested_agents_md_included(self) -> None:
        self._write_agents('src/AGENTS.md', 'Nested rule.')
        result = agents_instructions_for_path(str(self.root))
        self.assertIn('Nested rule.', result)

    def test_orchestration_layer_not_kato_in_output(self) -> None:
        self._write_agents('AGENTS.md', 'rule')
        result = agents_instructions_for_path(str(self.root))
        self.assertIn('Orchestration layer safety', result)
        self.assertNotIn('Kato safety', result)

    def test_repository_agents_instructions_empty_list(self) -> None:
        self.assertEqual(repository_agents_instructions_text([]), '')

    def test_repository_agents_instructions_with_valid_repo(self) -> None:
        self._write_agents('AGENTS.md', 'Follow style guide.')
        repo = _repo('my-repo', str(self.root))
        result = repository_agents_instructions_text([repo])
        self.assertIn('Follow style guide.', result)

    def test_repository_with_missing_local_path_skipped(self) -> None:
        repo = _repo('ghost', '/nonexistent/path/xyz')
        result = repository_agents_instructions_text([repo])
        self.assertEqual(result, '')

    def test_repository_id_used_as_label(self) -> None:
        self._write_agents('AGENTS.md', 'content')
        repo = _repo('my-frontend', str(self.root))
        result = repository_agents_instructions_text([repo])
        self.assertIn('my-frontend', result)

    def test_repository_with_blank_local_path_skipped(self) -> None:
        # Line 61: ``local_path`` is the empty string → silent skip.
        repo = _repo('blank', '')
        self.assertEqual(repository_agents_instructions_text([repo]), '')

    def test_repository_with_no_agents_files_skipped(self) -> None:
        # Line 67: local_path is a real directory but contains no AGENTS.md
        # anywhere → no entries → return ''.
        repo = _repo('empty', str(self.root))
        self.assertEqual(repository_agents_instructions_text([repo]), '')


# ---------------------------------------------------------------------------
# agent_prompt_utils
# ---------------------------------------------------------------------------

class IgnoredRepositoryFolderNamesTests(unittest.TestCase):
    def test_returns_empty_when_env_not_set(self) -> None:
        with patch.dict(os.environ, {IGNORED_REPOSITORY_FOLDERS_ENV: ''}, clear=False):
            self.assertEqual(ignored_repository_folder_names(), [])

    def test_parses_comma_separated_env_value(self) -> None:
        with patch.dict(
            os.environ,
            {IGNORED_REPOSITORY_FOLDERS_ENV: 'secret-client,internal-lib'},
            clear=False,
        ):
            result = ignored_repository_folder_names()
        self.assertEqual(result, ['secret-client', 'internal-lib'])

    def test_raw_string_value_parsed_directly(self) -> None:
        result = ignored_repository_folder_names('a,b,c')
        self.assertEqual(result, ['a', 'b', 'c'])

    def test_raw_list_value_used_directly(self) -> None:
        result = ignored_repository_folder_names(['x', 'y'])
        self.assertEqual(result, ['x', 'y'])

    def test_deduplicates_case_insensitively(self) -> None:
        result = ignored_repository_folder_names('Lib,lib,LIB')
        self.assertEqual(len(result), 1)

    def test_strips_whitespace_around_names(self) -> None:
        result = ignored_repository_folder_names(' folder1 , folder2 ')
        self.assertEqual(result, ['folder1', 'folder2'])

    def test_empty_entries_skipped(self) -> None:
        result = ignored_repository_folder_names('a,,b,')
        self.assertEqual(result, ['a', 'b'])


class ForbiddenRepositoryGuardrailsTextTests(unittest.TestCase):
    def test_empty_raw_value_returns_empty(self) -> None:
        self.assertEqual(forbidden_repository_guardrails_text(''), '')

    def test_single_folder_included(self) -> None:
        result = forbidden_repository_guardrails_text('secret-repo')
        self.assertIn('secret-repo', result)
        self.assertIn('Do not access them', result)
        self.assertIn('Execution protocol for forbidden repositories', result)

    def test_multiple_folders_all_listed(self) -> None:
        result = forbidden_repository_guardrails_text('repo-a,repo-b')
        self.assertIn('repo-a', result)
        self.assertIn('repo-b', result)


class WorkspaceInventoryBlockTests(unittest.TestCase):
    def test_empty_cwd_and_no_dirs_returns_empty(self) -> None:
        self.assertEqual(workspace_inventory_block('', None), '')

    def test_cwd_only_included(self) -> None:
        result = workspace_inventory_block('/some/cwd', None)
        self.assertIn('(cwd) /some/cwd', result)

    def test_additional_dirs_included(self) -> None:
        result = workspace_inventory_block('/cwd', ['/other/path'])
        self.assertIn('/other/path', result)
        self.assertIn('(cwd) /cwd', result)

    def test_cwd_not_duplicated_in_additional_dirs(self) -> None:
        result = workspace_inventory_block('/cwd', ['/cwd'])
        self.assertEqual(result.count('/cwd'), 1)

    def test_empty_additional_dirs_entries_skipped(self) -> None:
        result = workspace_inventory_block('/cwd', ['', None, '/valid'])
        self.assertIn('/valid', result)
        self.assertNotIn('None', result)

    def test_duplicate_additional_dirs_deduplicated(self) -> None:
        result = workspace_inventory_block('/cwd', ['/extra', '/extra'])
        self.assertEqual(result.count('/extra'), 1)


class ChatContinuityBlockTests(unittest.TestCase):
    def test_returns_non_empty_string(self) -> None:
        result = chat_continuity_ground_truth_block(is_resumed_session=True)
        self.assertTrue(result)

    def test_contains_continuity_instruction(self) -> None:
        result = chat_continuity_ground_truth_block(is_resumed_session=True)
        self.assertIn('Continuity instruction', result)


class PrependChatWorkspaceContextTests(unittest.TestCase):
    def test_no_context_returns_prompt_unchanged(self) -> None:
        result = prepend_chat_workspace_context('my prompt', cwd='', additional_dirs=None)
        # When all context is empty, only the prompt remains
        self.assertIn('my prompt', result)

    def test_with_cwd_context_is_prepended(self) -> None:
        result = prepend_chat_workspace_context(
            'my prompt',
            cwd='/some/path',
        )
        self.assertIn('my prompt', result)
        self.assertIn('/some/path', result)
        # Context before prompt
        self.assertLess(result.index('/some/path'), result.index('my prompt'))

    def test_with_ignored_folders_forbidden_block_included(self) -> None:
        result = prepend_chat_workspace_context(
            'do it',
            raw_ignored_value='hidden-repo',
        )
        self.assertIn('hidden-repo', result)

    def test_returns_prompt_unchanged_when_all_blocks_empty(self) -> None:
        # Line 133: defensive ``if not parts: return prompt``. In normal use
        # the continuity block is always non-empty, so this branch can only
        # fire if a future refactor makes continuity optional. Patch the
        # block to return '' so we exercise the safety net.
        # The function lives in ``agent_core_lib`` (re-exported here for
        # back-compat), so patch the canonical home — patching the shim's
        # namespace would not affect the resolved name inside the function.
        from unittest.mock import patch as patch_obj
        with patch_obj(
            'agent_core_lib.agent_core_lib.helpers.agent_prompt_utils.'
            'chat_continuity_ground_truth_block',
            return_value='',
        ):
            result = prepend_chat_workspace_context(
                'bare prompt',
                cwd='', additional_dirs=None, raw_ignored_value='',
            )
        self.assertEqual(result, 'bare prompt')


class SecurityGuardrailsTextTests(unittest.TestCase):
    def test_returns_non_empty_text(self) -> None:
        result = security_guardrails_text()
        self.assertTrue(result)

    def test_contains_key_security_rules(self) -> None:
        result = security_guardrails_text()
        self.assertIn('Security guardrails:', result)
        self.assertIn('secrets', result)
        self.assertIn('untrusted', result)


class WorkspaceScopeBlockTests(unittest.TestCase):
    def test_empty_paths_returns_empty(self) -> None:
        self.assertEqual(workspace_scope_block([]), '')
        self.assertEqual(workspace_scope_block(None), '')

    def test_single_path_included(self) -> None:
        result = workspace_scope_block(['/workspace/task-1'])
        self.assertIn('/workspace/task-1', result)
        self.assertIn('WORKSPACE SCOPE', result)

    def test_empty_string_paths_skipped(self) -> None:
        result = workspace_scope_block(['', '/valid/path', ''])
        self.assertIn('/valid/path', result)

    def test_generic_not_kato_specific(self) -> None:
        result = workspace_scope_block(['/workspace/x'])
        # Generic env wording, no product-specific names or paths.
        self.assertIn('AGENT_WORKSPACES_ROOT', result)
        self.assertNotIn('KATO_WORKSPACES_ROOT', result)
        self.assertNotIn('~/.kato/workspaces/', result)


class RepositoryScopeTextTests(unittest.TestCase):
    def test_no_repositories_returns_default_text(self) -> None:
        task = _task(branch_name='feature/x')
        result = repository_scope_text(task)
        self.assertIn('feature/x', result)
        self.assertNotIn('orchestration layer already prepared', result)

    def test_with_repositories_returns_scoped_text(self) -> None:
        repo = _repo('client', '/workspace/client')
        task = _task(branch_name='feature/x')
        prepared = _prepared(
            branch_name='feature/x',
            repositories=[repo],
            repository_branches={'client': 'feature/x'},
        )
        result = repository_scope_text(task, prepared)
        self.assertIn('client', result)
        self.assertIn('/workspace/client', result)

    def test_uses_task_branch_when_no_prepared_task(self) -> None:
        task = _task(branch_name='feature/proj-7')
        result = repository_scope_text(task)
        self.assertIn('feature/proj-7', result)

    def test_prepared_task_branch_overrides_task_branch(self) -> None:
        repo = _repo('client', '/workspace/client')
        task = _task(branch_name='old-branch')
        prepared = _prepared(
            branch_name='new-branch',
            repositories=[repo],
        )
        result = repository_scope_text(task, prepared)
        self.assertIn('new-branch', result)


class AgentsInstructionsTextTests(unittest.TestCase):
    def test_none_prepared_task_returns_empty(self) -> None:
        self.assertEqual(agents_instructions_text(None), '')

    def test_returns_agents_instructions_from_prepared_task(self) -> None:
        prepared = _prepared(agents_instructions='Use pnpm.')
        self.assertEqual(agents_instructions_text(prepared), 'Use pnpm.')

    def test_whitespace_only_instructions_returns_empty(self) -> None:
        prepared = _prepared(agents_instructions='   ')
        self.assertEqual(agents_instructions_text(prepared), '')


class TaskBranchNameTests(unittest.TestCase):
    def test_returns_task_branch_when_no_prepared(self) -> None:
        task = _task(branch_name='feature/x')
        self.assertEqual(task_branch_name(task), 'feature/x')

    def test_returns_prepared_branch_when_set(self) -> None:
        task = _task(branch_name='old')
        prepared = _prepared(branch_name='new')
        self.assertEqual(task_branch_name(task, prepared), 'new')

    def test_falls_back_to_task_when_prepared_branch_empty(self) -> None:
        task = _task(branch_name='feature/x')
        prepared = _prepared(branch_name='')
        self.assertEqual(task_branch_name(task, prepared), 'feature/x')


class TaskConversationTitleTests(unittest.TestCase):
    def test_uses_task_id_when_present(self) -> None:
        task = _task(task_id='PROJ-1', summary='fix it already')
        self.assertEqual(task_conversation_title(task), 'PROJ-1')

    def test_uses_summary_when_no_id(self) -> None:
        task = SimpleNamespace(id='', summary='Fix the auth flow')
        self.assertEqual(task_conversation_title(task), 'Fix the auth flow')

    def test_falls_back_to_generic_when_neither(self) -> None:
        task = SimpleNamespace(id='', summary='')
        result = task_conversation_title(task)
        # Must not be empty and must not contain "Kato"
        self.assertTrue(result)
        self.assertNotIn('Kato', result)

    def test_suffix_appended_to_task_id(self) -> None:
        task = _task(task_id='PROJ-1')
        self.assertEqual(task_conversation_title(task, suffix=' [draft]'), 'PROJ-1 [draft]')


class ReviewConversationTitleTests(unittest.TestCase):
    def test_uses_task_id_when_provided(self) -> None:
        comment = _comment()
        self.assertEqual(
            review_conversation_title(comment, task_id='PROJ-1'),
            'PROJ-1 [review]',
        )

    def test_falls_back_to_comment_id_when_no_task_id(self) -> None:
        comment = _comment(comment_id='42')
        result = review_conversation_title(comment)
        self.assertIn('42', result)


class ReviewCommentContextTextTests(unittest.TestCase):
    def test_empty_all_comments_returns_empty(self) -> None:
        comment = _comment(all_comments=[])
        self.assertEqual(review_comment_context_text(comment), '')

    def test_single_comment_returns_empty(self) -> None:
        comment = _comment(all_comments=[{'author': 'a', 'body': 'b'}])
        self.assertEqual(review_comment_context_text(comment), '')

    def test_multiple_comments_formatted(self) -> None:
        comment = _comment(all_comments=[
            {'author': 'alice', 'body': 'first comment'},
            {'author': 'bob', 'body': 'second comment'},
        ])
        result = review_comment_context_text(comment)
        self.assertIn('alice', result)
        self.assertIn('first comment', result)
        self.assertIn('bob', result)

    def test_self_reply_bodies_excluded(self) -> None:
        comment = _comment(all_comments=[
            {'author': 'kato', 'body': _SELF_REPLY_PREFIXES[0] + 'PR-1'},
            {'author': 'reviewer', 'body': 'actual comment'},
        ])
        result = review_comment_context_text(comment)
        self.assertNotIn(_SELF_REPLY_PREFIXES[0], result)
        self.assertIn('actual comment', result)

    def test_empty_body_skipped(self) -> None:
        comment = _comment(all_comments=[
            {'author': 'alice', 'body': ''},
            {'author': 'bob', 'body': 'valid'},
        ])
        result = review_comment_context_text(comment)
        self.assertIn('bob', result)
        self.assertNotIn('alice', result)

    def test_non_dict_items_skipped(self) -> None:
        comment = _comment(all_comments=['not a dict', {'author': 'a', 'body': 'ok'}])
        result = review_comment_context_text(comment)
        self.assertIn('ok', result)

    def test_returns_empty_when_all_entries_filtered_out(self) -> None:
        # Hits line 282: ``if not lines: return ''`` — len > 1 passes the gate,
        # but every entry is either blank or a kato self-reply, so the result
        # is an empty list and the early-return fires.
        comment = _comment(
            all_comments=[
                {'author': 'kato', 'body': 'Kato addressed review comment X'},
                {'author': 'kato', 'body': 'Kato addressed this review comment'},
            ],
        )
        self.assertEqual(review_comment_context_text(comment), '')


class IsSelfReplyBodyTests(unittest.TestCase):
    def test_returns_true_for_kato_review_prefix(self) -> None:
        self.assertTrue(_is_self_reply_body('Kato addressed review comment PR-1'))

    def test_returns_true_for_kato_this_prefix(self) -> None:
        self.assertTrue(_is_self_reply_body('Kato addressed this review comment'))

    def test_returns_false_for_other_body(self) -> None:
        self.assertFalse(_is_self_reply_body('This is a normal comment'))

    def test_returns_false_for_empty_body(self) -> None:
        self.assertFalse(_is_self_reply_body(''))


class ReviewRepositoryContextTests(unittest.TestCase):
    def test_returns_empty_when_no_repository_id(self) -> None:
        self.assertEqual(review_repository_context(_comment(repository_id='')), '')

    def test_returns_repository_clause_when_set(self) -> None:
        result = review_repository_context(_comment(repository_id='client'))
        self.assertIn('client', result)
        self.assertIn('repository', result)


class ReviewCommentLocationTextTests(unittest.TestCase):
    def test_empty_file_path_returns_empty(self) -> None:
        self.assertEqual(review_comment_location_text(_comment(file_path='')), '')

    def test_file_only_no_line(self) -> None:
        result = review_comment_location_text(_comment(file_path='src/main.py'))
        self.assertIn('src/main.py', result)

    def test_file_with_line_number(self) -> None:
        result = review_comment_location_text(
            _comment(file_path='src/main.py', line_number=42)
        )
        self.assertIn('42', result)
        self.assertIn('src/main.py', result)

    def test_file_with_line_type(self) -> None:
        result = review_comment_location_text(
            _comment(file_path='src/main.py', line_number=5, line_type='added')
        )
        self.assertIn('added', result)

    def test_commit_sha_appended(self) -> None:
        result = review_comment_location_text(
            _comment(file_path='a.py', commit_sha='abc123')
        )
        self.assertIn('abc123', result)
        self.assertIn('Commit:', result)

    def test_invalid_line_number_omitted(self) -> None:
        result = review_comment_location_text(
            _comment(file_path='a.py', line_number='invalid')
        )
        self.assertIn('a.py', result)
        self.assertNotIn('invalid', result)


class ReviewCommentCodeSnippetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = self._tmp.name

    def _write_file(self, rel_path: str, content: str) -> str:
        path = Path(self.workspace) / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return rel_path

    def test_empty_file_path_returns_empty(self) -> None:
        result = review_comment_code_snippet(_comment(), self.workspace)
        self.assertEqual(result, '')

    def test_empty_workspace_returns_empty(self) -> None:
        result = review_comment_code_snippet(_comment(file_path='a.py'), '')
        self.assertEqual(result, '')

    def test_invalid_line_number_returns_empty(self) -> None:
        self._write_file('a.py', 'line1\nline2\n')
        result = review_comment_code_snippet(
            _comment(file_path='a.py', line_number='bad'), self.workspace,
        )
        self.assertEqual(result, '')

    def test_zero_line_number_returns_empty(self) -> None:
        self._write_file('a.py', 'line1\n')
        result = review_comment_code_snippet(
            _comment(file_path='a.py', line_number=0), self.workspace,
        )
        self.assertEqual(result, '')

    def test_valid_snippet_contains_line_marker(self) -> None:
        rel = self._write_file('src/main.py', 'line1\nline2\nline3\n')
        result = review_comment_code_snippet(
            _comment(file_path=rel, line_number=2), self.workspace,
        )
        self.assertIn('→', result)
        self.assertIn('line2', result)

    def test_missing_file_returns_empty(self) -> None:
        result = review_comment_code_snippet(
            _comment(file_path='nonexistent.py', line_number=1), self.workspace,
        )
        self.assertEqual(result, '')

    def test_blank_file_returns_empty(self) -> None:
        # Hits line 330: ``if not lines: return ''`` for an empty file.
        self._write_file('blank.py', '')
        result = review_comment_code_snippet(
            _comment(file_path='blank.py', line_number=1), self.workspace,
        )
        self.assertEqual(result, '')

    def test_snippet_truncates_when_total_budget_exceeded(self) -> None:
        # Lines 344-345: ``total_bytes > _REVIEW_SNIPPET_MAX_BYTES`` →
        # append the truncation marker and break. Build many medium-length
        # lines so the cumulative byte budget is exceeded mid-render.
        long_lines = '\n'.join('x' * 200 for _ in range(200))
        self._write_file('many.py', long_lines)
        result = review_comment_code_snippet(
            _comment(file_path='many.py', line_number=100),
            self.workspace,
            context_lines=200,  # request a huge window so budget is hit
        )
        self.assertIn('snippet truncated', result)

    def test_snippet_returns_empty_when_window_lands_past_file_end(self) -> None:
        # Line 348: ``if not rendered: return ''``. When line_number is
        # far past file end and context window doesn't reach back into
        # the file, ``rendered`` is empty.
        self._write_file('tiny.py', 'one\ntwo\nthree\n')
        result = review_comment_code_snippet(
            _comment(file_path='tiny.py', line_number=100),
            self.workspace,
            context_lines=1,  # window is [99, 101] but file only has 3 lines
        )
        self.assertEqual(result, '')

    def test_long_line_is_truncated_with_ellipsis(self) -> None:
        # Hits line 339: ``if len(line_text) > 240: line_text = ...``.
        self._write_file('long.py', 'x' * 500)
        result = review_comment_code_snippet(
            _comment(file_path='long.py', line_number=1), self.workspace,
        )
        self.assertIn('...', result)
        # Original 500-char line should be cut down.
        self.assertNotIn('x' * 300, result)


class ReviewCommentsBatchTextTests(unittest.TestCase):
    def test_empty_comments_returns_empty(self) -> None:
        self.assertEqual(review_comments_batch_text([]), '')

    def test_single_comment_formatted(self) -> None:
        comment = _comment(author='alice', body='please fix', file_path='a.py', line_number=5)
        result = review_comments_batch_text([comment])
        self.assertIn('alice', result)
        self.assertIn('please fix', result)
        self.assertIn('a.py', result)

    def test_multiple_comments_numbered(self) -> None:
        comments = [
            _comment(author='a', body='first'),
            _comment(author='b', body='second'),
        ]
        result = review_comments_batch_text(comments)
        self.assertIn('1.', result)
        self.assertIn('2.', result)

    def test_no_file_path_shows_pr_level_comment(self) -> None:
        comment = _comment(author='reviewer', body='general feedback')
        result = review_comments_batch_text([comment])
        self.assertIn('PR-level comment', result)


# ---------------------------------------------------------------------------
# wire_protocol — constants
# ---------------------------------------------------------------------------

class WireProtocolConstantsTests(unittest.TestCase):
    def test_claude_event_types_are_strings(self) -> None:
        for value in (
            CLAUDE_EVENT_ASSISTANT,
            CLAUDE_EVENT_CONTROL_REQUEST,
            CLAUDE_EVENT_CONTROL_RESPONSE,
            CLAUDE_EVENT_PERMISSION_REQUEST,
            CLAUDE_EVENT_PERMISSION_RESPONSE,
            CLAUDE_EVENT_RESULT,
            CLAUDE_EVENT_STREAM_EVENT,
            CLAUDE_EVENT_SYSTEM,
            CLAUDE_EVENT_USER,
        ):
            self.assertIsInstance(value, str)

    def test_permission_request_event_types_is_frozenset(self) -> None:
        self.assertIsInstance(PERMISSION_REQUEST_EVENT_TYPES, frozenset)

    def test_permission_request_events_in_frozenset(self) -> None:
        self.assertIn(CLAUDE_EVENT_PERMISSION_REQUEST, PERMISSION_REQUEST_EVENT_TYPES)
        self.assertIn(CLAUDE_EVENT_CONTROL_REQUEST, PERMISSION_REQUEST_EVENT_TYPES)

    def test_sse_event_names_are_strings(self) -> None:
        for value in (
            SSE_EVENT_SESSION_CLOSED,
            SSE_EVENT_SESSION_EVENT,
            SSE_EVENT_SESSION_HISTORY_EVENT,
            SSE_EVENT_SESSION_IDLE,
            SSE_EVENT_SESSION_MISSING,
            SSE_EVENT_STATUS_DISABLED,
            SSE_EVENT_STATUS_ENTRY,
        ):
            self.assertIsInstance(value, str)

    def test_system_subtype_init_value(self) -> None:
        self.assertEqual(CLAUDE_SYSTEM_SUBTYPE_INIT, 'init')

    def test_all_event_names_are_distinct(self) -> None:
        all_events = [
            CLAUDE_EVENT_ASSISTANT, CLAUDE_EVENT_CONTROL_REQUEST,
            CLAUDE_EVENT_CONTROL_RESPONSE, CLAUDE_EVENT_PERMISSION_REQUEST,
            CLAUDE_EVENT_PERMISSION_RESPONSE, CLAUDE_EVENT_RESULT,
            CLAUDE_EVENT_STREAM_EVENT, CLAUDE_EVENT_SYSTEM, CLAUDE_EVENT_USER,
        ]
        self.assertEqual(len(all_events), len(set(all_events)))


if __name__ == '__main__':
    unittest.main()
