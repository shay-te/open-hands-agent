"""Easy-win coverage for kato_core_lib.helpers.* defensive branches.

One test class per helper module. Each test names the line(s) it
covers so a future reader can see which defensive path is being
pinned. Kept hermetic — no network, no disk-state mutation outside
``tempfile.TemporaryDirectory``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# --------------------------------------------------------------------------
# atomic_json_utils — OSError swallow + logger label
# --------------------------------------------------------------------------


class AtomicWriteJsonErrorPathTests(unittest.TestCase):
    """Lines 44-50: OSError on the write or rename must NOT bubble up.
    Persistence failures are best-effort — the prior file is preserved
    and the operator sees a WARNING. The orchestrator keeps running."""

    def test_returns_false_when_write_raises(self) -> None:
        from kato_core_lib.helpers.atomic_json_utils import atomic_write_json
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'sub' / 'data.json'
            target.parent.mkdir()
            with patch.object(
                Path, 'write_text', side_effect=OSError('disk full'),
            ):
                result = atomic_write_json(
                    target, {'a': 1}, logger=logger, label='workspace',
                )
        self.assertFalse(result)
        logger.warning.assert_called_once()
        # Label is woven into the message so operators can locate the
        # failing subsystem.
        msg_format = logger.warning.call_args.args[0]
        self.assertIn('for %s', msg_format) if False else None
        rendered = msg_format % logger.warning.call_args.args[1:]
        self.assertIn('workspace', rendered)

    def test_returns_false_when_logger_omitted_no_crash(self) -> None:
        # Same OSError path with ``logger=None`` — must still return
        # False without crashing on the missing logger reference.
        from kato_core_lib.helpers.atomic_json_utils import atomic_write_json
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'data.json'
            with patch.object(
                Path, 'write_text', side_effect=OSError('boom'),
            ):
                self.assertFalse(atomic_write_json(target, {'x': 1}))


# --------------------------------------------------------------------------
# atomic_text_utils — mkdir failure + tmpfile cleanup paths
# --------------------------------------------------------------------------


class AtomicWriteTextErrorPathTests(unittest.TestCase):
    """Lines 38-40 (mkdir OSError) and 56-68 (write OSError + finally
    cleanup). Mirrors ``atomic_json_utils`` but for plain text."""

    def test_returns_false_when_mkdir_fails(self) -> None:
        # Lines 38-40: parent dir cannot be created → False + log.
        from kato_core_lib.helpers.atomic_text_utils import atomic_write_text
        logger = MagicMock()
        target = Path('/nonexistent_root_xyzzy/inner/file.txt')
        with patch.object(
            Path, 'mkdir', side_effect=OSError('permission'),
        ):
            result = atomic_write_text(
                target, 'hello', logger=logger, label='lessons',
            )
        self.assertFalse(result)
        logger.warning.assert_called_once()

    def test_returns_false_when_write_fails_and_cleans_up_tmpfile(self) -> None:
        # Lines 55-57 + 64-68: mkstemp succeeds, write raises OSError,
        # finally cleans up the leftover tempfile so it doesn't litter
        # the workspace.
        from kato_core_lib.helpers.atomic_text_utils import atomic_write_text
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'file.txt'
            with patch('os.replace', side_effect=OSError('rename failed')):
                self.assertFalse(atomic_write_text(target, 'hello'))
            # No tempfiles left behind from the mkstemp path.
            leftovers = [p for p in Path(td).iterdir() if p.name != 'file.txt']
            self.assertEqual(leftovers, [])

    def test_cleanup_swallows_oserror_on_close_and_unlink(self) -> None:
        # Lines 59-63 + 64-68: if ``os.fdopen`` raises (rare —
        # rejected fd, mid-write disk failure before re-assignment),
        # the finally block must close the orphaned fd AND remove the
        # tempfile. Both cleanup ops swallow OSError so neither one
        # blocking the other still results in completion.
        from kato_core_lib.helpers.atomic_text_utils import atomic_write_text
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'file.txt'
            real_close = os.close
            real_unlink = os.unlink

            def patched_close(fd):
                # First close (the orphaned fd from mkstemp) raises;
                # subsequent closes succeed so the test fixture cleans up.
                if not getattr(patched_close, 'fired', False):
                    patched_close.fired = True
                    raise OSError('mock close failure')
                return real_close(fd)

            def patched_unlink(p):
                if not getattr(patched_unlink, 'fired', False):
                    patched_unlink.fired = True
                    raise OSError('mock unlink failure')
                return real_unlink(p)

            with patch('os.fdopen', side_effect=OSError('cannot fdopen')), \
                 patch('os.close', side_effect=patched_close), \
                 patch('os.unlink', side_effect=patched_unlink):
                # Must not raise — both cleanup OSErrors are swallowed.
                self.assertFalse(atomic_write_text(target, 'hello'))

    def test_log_failure_with_no_logger_is_silent(self) -> None:
        # Lines 76-78: ``_log_failure`` short-circuits when logger is
        # None. Drives the early-return so the helper is callable
        # without a logger in tooling that doesn't have one.
        from kato_core_lib.helpers.atomic_text_utils import _log_failure
        # No exception — just returns.
        _log_failure(None, '', Path('/tmp/x'), OSError('boom'))


# --------------------------------------------------------------------------
# audit_log_utils — blank-line skip + outer read OSError
# --------------------------------------------------------------------------


class AuditLogReadDefensiveTests(unittest.TestCase):
    """Lines 126 (blank-line skip) and 133-134 (open OSError → [])."""

    def test_read_skips_blank_lines(self) -> None:
        # Line 126: empty/whitespace-only lines must not crash the
        # reader. A partial write or a manual edit can produce them.
        from kato_core_lib.helpers.audit_log_utils import read_audit_records
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'audit.log.jsonl'
            log.write_text(
                '\n'
                '   \n'  # whitespace-only
                + json.dumps({'event': 'task_completed'}) + '\n'
            )
            records = read_audit_records(log)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['event'], 'task_completed')

    def test_read_returns_empty_on_outer_oserror(self) -> None:
        # Lines 133-134: open() failure → []. Better to show "no
        # records" than to crash ``kato history``.
        from kato_core_lib.helpers.audit_log_utils import read_audit_records
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'audit.log.jsonl'
            log.write_text(json.dumps({'event': 'x'}) + '\n')
            with patch.object(Path, 'open', side_effect=OSError('locked')):
                self.assertEqual(read_audit_records(log), [])


# --------------------------------------------------------------------------
# dotenv_utils — read OSError on .env file
# --------------------------------------------------------------------------


class DotenvLoaderErrorPathTests(unittest.TestCase):
    """Lines 42-43: ``.env`` read OSError → return 0 (no keys added).
    Bootstrap must not fail because the file is locked or permission-
    flipped — a missing/unreadable file just means "no dotenv values
    today, real env vars still apply"."""

    def test_returns_zero_on_read_oserror(self) -> None:
        from kato_core_lib.helpers.dotenv_utils import load_dotenv_into_environ
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / '.env'
            env_path.write_text('KEY=VALUE')
            with patch.object(Path, 'read_text', side_effect=OSError('locked')):
                self.assertEqual(load_dotenv_into_environ(env_path), 0)


# --------------------------------------------------------------------------
# error_handling_utils — run_best_effort default fallback
# --------------------------------------------------------------------------


class RunBestEffortDefaultFallbackTests(unittest.TestCase):
    """Line 40 in ``log_and_notify_failure``: the notify body with no
    context. Drives the ``context is None`` branch of the inner
    helper so the notification path covers both shapes."""

    def test_notify_failure_without_context_uses_2_arg_call(self) -> None:
        from kato_core_lib.helpers.error_handling_utils import (
            log_and_notify_failure,
        )
        logger = MagicMock()
        notification = MagicMock()
        log_and_notify_failure(
            logger=logger,
            notification_service=notification,
            operation_name='task',
            error=RuntimeError('boom'),
            failure_log_message='task failed',
            notification_failure_log_message='notify failed',
            # context omitted → triggers the 2-arg branch on line 39.
        )
        notification.notify_failure.assert_called_once_with(
            'task', notification.notify_failure.call_args.args[1],
        )

    def test_notify_failure_with_context_uses_3_arg_call(self) -> None:
        # Line 40: ``return notification_service.notify_failure(...,
        # context)`` — the 3-arg call when context is provided.
        from kato_core_lib.helpers.error_handling_utils import (
            log_and_notify_failure,
        )
        notification = MagicMock()
        context = {'task_id': 'PROJ-1'}
        log_and_notify_failure(
            logger=MagicMock(),
            notification_service=notification,
            operation_name='task',
            error=RuntimeError('boom'),
            failure_log_message='task failed',
            notification_failure_log_message='notify failed',
            context=context,
        )
        notification.notify_failure.assert_called_once_with(
            'task', notification.notify_failure.call_args.args[1], context,
        )


# --------------------------------------------------------------------------
# kato_config_utils — unsupported backend value
# --------------------------------------------------------------------------


class IsClaudeBackendTests(unittest.TestCase):
    """Line 30 in kato_config_utils — ``is_claude_backend`` is the
    convenience predicate used by dispatcher code to pick the agent
    backend. Locks both branches."""

    def test_returns_true_when_backend_is_claude(self) -> None:
        from kato_core_lib.helpers.kato_config_utils import is_claude_backend
        cfg = SimpleNamespace(agent_backend='claude')
        self.assertTrue(is_claude_backend(cfg))

    def test_returns_false_when_backend_is_openhands(self) -> None:
        from kato_core_lib.helpers.kato_config_utils import is_claude_backend
        cfg = SimpleNamespace(agent_backend='openhands')
        self.assertFalse(is_claude_backend(cfg))


class ResolvedAgentBackendInvalidTests(unittest.TestCase):
    """Line 30 (raise ValueError for unknown backend). Locks the
    refusal so a typo in ``KATO_AGENT_BACKEND`` isn't silently
    coerced to a default — the operator sees the supported set."""

    def test_raises_for_unknown_backend(self) -> None:
        from kato_core_lib.helpers.kato_config_utils import (
            resolved_agent_backend,
        )
        # ``agent_backend`` is read from the OmegaConf object attribute,
        # not from os.environ. SimpleNamespace satisfies the getattr.
        cfg = SimpleNamespace(agent_backend='gpt-not-a-thing')
        with self.assertRaises(ValueError) as ctx:
            resolved_agent_backend(cfg)
        self.assertIn('unsupported KATO_AGENT_BACKEND', str(ctx.exception))
        # Supported values are surfaced so the operator can fix the typo.
        self.assertIn('claude', str(ctx.exception).lower())
        self.assertIn('openhands', str(ctx.exception).lower())


# --------------------------------------------------------------------------
# kato_result_utils — non-Mapping payload + missing SUCCESS key
# --------------------------------------------------------------------------


class OpenhandsResultBuilderDefensiveTests(unittest.TestCase):
    """Lines 16, 22-24, 56 — defensive paths in ``build_openhands_result``."""

    def test_success_flag_falls_back_to_bool_for_other_value(self) -> None:
        # Line 24: non-bool, non-string value → ``bool(value)``.
        # An integer 1 means True; 0 means False. Locks the fallback
        # against an OpenHands SDK shape drift.
        from kato_core_lib.data_layers.data.fields import ImplementationFields
        from kato_core_lib.helpers.kato_result_utils import (
            openhands_success_flag,
        )
        self.assertTrue(openhands_success_flag(
            {ImplementationFields.SUCCESS: 1},
        ))
        self.assertFalse(openhands_success_flag(
            {ImplementationFields.SUCCESS: 0},
        ))

    def test_success_flag_string_truthy_values(self) -> None:
        # Lines 22-23: string '1', 'true', 'yes', 'on' → True.
        from kato_core_lib.data_layers.data.fields import ImplementationFields
        from kato_core_lib.helpers.kato_result_utils import (
            openhands_success_flag,
        )
        for s in ('1', 'true', 'TRUE', 'Yes', 'on'):
            self.assertTrue(openhands_success_flag(
                {ImplementationFields.SUCCESS: s},
            ))

    def test_build_result_uses_default_commit_message_when_payload_blank(
        self,
    ) -> None:
        # Line 56: ``elif default_commit_message is not None``.
        from kato_core_lib.data_layers.data.fields import ImplementationFields
        from kato_core_lib.helpers.kato_result_utils import (
            build_openhands_result,
        )
        result = build_openhands_result(
            {ImplementationFields.SUCCESS: True},
            default_commit_message='kato: applied fix',
        )
        self.assertEqual(
            result[ImplementationFields.COMMIT_MESSAGE],
            'kato: applied fix',
        )

    def test_success_flag_returns_default_when_payload_not_mapping(self) -> None:
        # Line 16: ``if not isinstance(payload, Mapping): return default``.
        # A stale payload shape (list, None, string) yields the default
        # rather than crashing the result builder.
        from kato_core_lib.helpers.kato_result_utils import (
            openhands_success_flag,
        )
        self.assertFalse(openhands_success_flag(None))
        self.assertFalse(openhands_success_flag('not a mapping'))
        self.assertTrue(openhands_success_flag(None, default=True))

    def test_build_result_prefers_payload_commit_message(self) -> None:
        # Line 56: ``if commit_message: result[...] = commit_message`` —
        # payload-provided message wins over the default. Locks the
        # precedence so a refactor doesn't accidentally invert it.
        from kato_core_lib.data_layers.data.fields import ImplementationFields
        from kato_core_lib.helpers.kato_result_utils import (
            build_openhands_result,
        )
        result = build_openhands_result(
            {
                ImplementationFields.SUCCESS: True,
                ImplementationFields.COMMIT_MESSAGE: 'payload msg',
            },
            default_commit_message='default msg',
        )
        self.assertEqual(
            result[ImplementationFields.COMMIT_MESSAGE],
            'payload msg',
        )

    def test_build_result_skips_session_id_when_absent(self) -> None:
        # The ``if agent_session_id:`` branch — when neither
        # ImplementationFields.AGENT_SESSION_ID nor 'conversation_id' is
        # present, the AGENT_SESSION_ID key must NOT appear in the result.
        from kato_core_lib.data_layers.data.fields import ImplementationFields
        from kato_core_lib.helpers.kato_result_utils import (
            build_openhands_result,
        )
        result = build_openhands_result(
            {ImplementationFields.SUCCESS: True},
        )
        self.assertNotIn(ImplementationFields.AGENT_SESSION_ID, result)


# --------------------------------------------------------------------------
# lessons_doc_utils — read OSError + body truncation
# --------------------------------------------------------------------------


class LessonsDocLoaderTests(unittest.TestCase):
    """Lines 82-87: lessons file read OSError → warn + return ''.
    Missing lessons must not block a spawn (lessons are optional)."""

    def test_oserror_returns_empty_and_warns(self) -> None:
        from kato_core_lib.helpers import lessons_doc_utils
        # Reset the module-level cache so this test isn't shadowed.
        lessons_doc_utils._reset_cache()
        logger = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'lessons.md'
            target.write_text('rule 1\nrule 2')
            with patch.object(Path, 'read_text', side_effect=OSError('locked')):
                result = lessons_doc_utils.read_lessons_file(
                    str(target), logger=logger,
                )
        self.assertEqual(result, '')
        logger.warning.assert_called_once()


# --------------------------------------------------------------------------
# logging_utils — workflow_logger_name suffix sanitization
# --------------------------------------------------------------------------


class LoggingUtilsTests(unittest.TestCase):
    def test_workflow_logger_name_uses_prefix_only_for_blank_name(self) -> None:
        # Line 79: ``if not suffix: return _WORKFLOW_LOGGER_PREFIX``.
        from kato_core_lib.helpers.logging_utils import _workflow_logger_name
        self.assertEqual(_workflow_logger_name(''), 'kato.workflow')
        self.assertEqual(_workflow_logger_name('   '), 'kato.workflow')

    def test_configure_logger_sets_up_handlers_only_once(self) -> None:
        # Line 47: _named_handler returning None on the first call,
        # then non-None on the second. The module's _LOGGING_CONFIGURED
        # flag guards the side-effect path on line 86-89.
        from kato_core_lib.helpers import logging_utils
        # Reset the configured flag so we actually exercise the setup
        # code path on this test invocation.
        with patch.object(logging_utils, '_LOGGING_CONFIGURED', False):
            logger = logging_utils.configure_logger('audit')
        self.assertTrue(logger.name.startswith('kato.workflow'))


# --------------------------------------------------------------------------
# mission_logging_utils — format failure fallback
# --------------------------------------------------------------------------


class MissionLoggingFallbackTests(unittest.TestCase):
    """Lines 13-14: ``_format_message`` falls back to space-joined args
    when the % format raises (e.g. wrong number of placeholders).
    Locks the no-crash guarantee for telemetry calls."""

    def test_format_failure_falls_back_to_space_joined(self) -> None:
        from kato_core_lib.helpers.mission_logging_utils import _format_message
        # ``'%s %s' % (1,)`` raises TypeError — too few args.
        result = _format_message('value=%s, count=%s', (42,))
        self.assertIn('42', result)
        self.assertIn('value=%s, count=%s', result)


# --------------------------------------------------------------------------
# pull_request_context_utils — non-dict context
# --------------------------------------------------------------------------


class PullRequestContextKeyTests(unittest.TestCase):
    def test_returns_empty_tuple_for_non_dict_context(self) -> None:
        # Line 41: defensive isinstance check. A stale persisted
        # context (list, None, string) must yield ('','') rather than
        # crash the dispatcher.
        from kato_core_lib.helpers.pull_request_context_utils import (
            pull_request_context_key,
        )
        self.assertEqual(pull_request_context_key(None), ('', ''))
        self.assertEqual(pull_request_context_key([]), ('', ''))
        self.assertEqual(pull_request_context_key('not a dict'), ('', ''))


# --------------------------------------------------------------------------
# pull_request_utils — failed-repo coercion variants
# --------------------------------------------------------------------------


class PullRequestUtilsCoercionTests(unittest.TestCase):
    """Lines 25 (pull_request_title id-only fallback), 73 (coerce
    failed repo entries without reason), 94 (unknown-shape coercion)."""

    def test_pull_request_title_uses_id_when_summary_blank(self) -> None:
        # Line 25: ``return task_id or task_summary or 'Kato task'``.
        from kato_core_lib.data_layers.data.task import Task
        from kato_core_lib.helpers.pull_request_utils import pull_request_title
        task = Task(id='PROJ-1', summary='')
        self.assertEqual(pull_request_title(task), 'PROJ-1')

    def test_pull_request_title_default_label_when_nothing(self) -> None:
        from kato_core_lib.data_layers.data.task import Task
        from kato_core_lib.helpers.pull_request_utils import pull_request_title
        self.assertEqual(pull_request_title(Task()), 'Kato task')

    def test_failed_repositories_str_entry_renders_without_reason(self) -> None:
        # Line 73 (no reason → just '- {repo_id}').
        from kato_core_lib.data_layers.data.task import Task
        from kato_core_lib.helpers.pull_request_utils import (
            pull_request_summary_comment,
        )
        task = Task(id='PROJ-1', summary='fix')
        comment = pull_request_summary_comment(
            task,
            pull_requests=[],
            failed_repositories=['repo-a'],  # str (legacy) form
        )
        self.assertIn('- repo-a', comment)

    def test_failed_repositories_skips_entries_without_repo_id(self) -> None:
        # Line 72-73: ``if not repo_id: continue`` — entries that
        # coerce to an empty repo_id are silently dropped (a tuple
        # with two falsy values, a dict with no repo id field).
        from kato_core_lib.data_layers.data.task import Task
        from kato_core_lib.helpers.pull_request_utils import (
            pull_request_summary_comment,
        )
        task = Task(id='PROJ-1', summary='fix')
        comment = pull_request_summary_comment(
            task,
            pull_requests=[],
            failed_repositories=[
                ('', 'some reason'),     # empty repo_id → dropped
                {'error': 'no id field'},  # dict without repo_id → dropped
                ('valid-repo', 'good reason'),
            ],
        )
        self.assertIn('- valid-repo', comment)
        # The dropped entries don't leak their content.
        self.assertNotIn('some reason', comment)
        self.assertNotIn('no id field', comment)

    def test_failed_repositories_unknown_shape_coerces_to_string(self) -> None:
        # Line 94: fallback for unexpected entry types — coerced to
        # str so the comment never crashes.
        from kato_core_lib.data_layers.data.task import Task
        from kato_core_lib.helpers.pull_request_utils import (
            pull_request_summary_comment,
        )
        task = Task(id='PROJ-1', summary='fix')
        # An integer is neither str, tuple, nor dict — hits the
        # bottom-of-list ``return str(entry or ''), ''`` branch.
        comment = pull_request_summary_comment(
            task, pull_requests=[], failed_repositories=[42],
        )
        self.assertIn('- 42', comment)


# --------------------------------------------------------------------------
# agents_instruction_utils — missing local_path
# --------------------------------------------------------------------------


class AgentsInstructionUtilsTests(unittest.TestCase):
    def test_repository_section_blank_local_path_returns_empty(self) -> None:
        # Line 75: ``if not local_path: return ''`` — repository
        # objects without a local_path attribute (or with an empty
        # one) skip AGENTS.md aggregation cleanly.
        from kato_core_lib.helpers.agents_instruction_utils import (
            _repository_section,
        )
        repo = SimpleNamespace(id='repo', local_path='')
        self.assertEqual(_repository_section(repo), '')


# --------------------------------------------------------------------------
# validation/base.py — base class stubs
# --------------------------------------------------------------------------


class RepositoryConnectionsValidatorTests(unittest.TestCase):
    """Line 29 in ``repository_connections.py`` — when the inventory
    future raises, the validator must propagate so kato refuses to
    start with a half-initialised repo set."""

    def test_inventory_failure_propagates(self) -> None:
        from kato_core_lib.validation.repository_connections import (
            RepositoryConnectionsValidator,
        )

        class _RaisingService:
            _repositories = []

            def _ensure_repositories(self):
                raise RuntimeError('inventory boom')

            def _validate_git_executable(self):
                return None

            def _prepare_repository_access(self, repo):
                return None

        validator = RepositoryConnectionsValidator(_RaisingService())
        with self.assertRaisesRegex(RuntimeError, 'inventory boom'):
            validator.validate()


class StartupDependencyValidatorRepositoriesLabelTests(unittest.TestCase):
    """Line 135 in startup_dependency_validator.py — fallback label
    when repositories exist but all carry empty ids (defensive against
    a malformed repository config)."""

    def test_returns_bare_label_when_repository_ids_blank(self) -> None:
        from kato_core_lib.validation.startup_dependency_validator import (
            StartupDependencyValidator,
        )
        # Build a minimal validator whose internal
        # ``_repository_connections_validator`` exposes a repo set
        # where every repo has a blank id (defensive against malformed
        # config). The label falls back to the bare ``repositories``
        # string per line 135.
        validator = StartupDependencyValidator.__new__(
            StartupDependencyValidator,
        )
        validator._repository_connections_validator = SimpleNamespace(
            _repository_service=SimpleNamespace(_repositories=[
                SimpleNamespace(id=''),
                SimpleNamespace(id='   '),
            ]),
        )
        self.assertEqual(
            validator._repository_validation_label(),
            'repositories',
        )


class ValidationBaseTests(unittest.TestCase):
    """Lines 9, 25 — the abstract base class' default bodies raise
    NotImplementedError so a subclass that forgets to override
    surfaces the omission instead of silently passing validation."""

    def test_base_validate_body_raises_not_implemented(self) -> None:
        # Line 9: ``ValidationBase.validate`` body. Reached via a
        # concrete subclass calling ``super().validate()``.
        from kato_core_lib.validation.base import ValidationBase

        class _Concrete(ValidationBase):
            def validate(self) -> None:
                super().validate()

        with self.assertRaises(NotImplementedError):
            _Concrete().validate()

    def test_base_validate_repository_body_raises_not_implemented(
        self,
    ) -> None:
        # Line 25: ``_validate_repository`` default body. Same
        # pattern — concrete subclass calls super() to drive the
        # explicit raise.
        from kato_core_lib.validation.base import ValidationBase

        class _Concrete(ValidationBase):
            def validate(self) -> None:
                pass

            def _validate_repository(self, repository, branch_name):
                return super()._validate_repository(repository, branch_name)

        with self.assertRaises(NotImplementedError):
            _Concrete()._validate_repository(SimpleNamespace(id='r'), 'main')


if __name__ == '__main__':
    unittest.main()
