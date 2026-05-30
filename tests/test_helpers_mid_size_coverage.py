"""Coverage for mid-size kato_core_lib helpers.

Targets the remaining defensive / spinner / retry-policy branches.
Each test names the line(s) it pins so a future reader can see which
behaviour the assertion locks down.
"""

from __future__ import annotations

import io
import logging
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# --------------------------------------------------------------------------
# subprocess_utils — module-level constants
# --------------------------------------------------------------------------


class SubprocessUtilsConstantsTests(unittest.TestCase):
    """The module exposes ``SAFE_TEXT_KWARGS`` as a dict spread into
    every text-mode subprocess call site. Lock the values so a
    Windows-side cp1252 default can't sneak back in by accident."""

    def test_safe_text_kwargs_pin_utf8_and_replace(self) -> None:
        from kato_core_lib.helpers.subprocess_utils import SAFE_TEXT_KWARGS
        self.assertEqual(SAFE_TEXT_KWARGS['encoding'], 'utf-8')
        self.assertEqual(SAFE_TEXT_KWARGS['errors'], 'replace')


# --------------------------------------------------------------------------
# text_utils — Mapping defensive paths
# --------------------------------------------------------------------------


class TextUtilsMappingTests(unittest.TestCase):
    """Lines 37-40 + 44-47: ``dict_from_mapping`` /
    ``list_from_mapping`` must return clean empties for non-Mapping
    input and for keys whose values aren't dict/list. Used widely as
    a parse-safety layer for OpenHands/external payloads."""

    def test_dict_from_mapping_returns_empty_for_non_mapping(self) -> None:
        from kato_core_lib.helpers.text_utils import dict_from_mapping
        self.assertEqual(dict_from_mapping(None, 'x'), {})
        self.assertEqual(dict_from_mapping('not a mapping', 'x'), {})
        self.assertEqual(dict_from_mapping(['list'], 'x'), {})

    def test_dict_from_mapping_returns_empty_when_value_not_dict(self) -> None:
        # Line 40: ``value if isinstance(value, dict) else {}`` —
        # extracted value isn't a dict (e.g. payload corruption) → {}.
        from kato_core_lib.helpers.text_utils import dict_from_mapping
        self.assertEqual(dict_from_mapping({'x': 'not a dict'}, 'x'), {})
        self.assertEqual(dict_from_mapping({'x': 42}, 'x'), {})

    def test_dict_from_mapping_passes_through_dict_value(self) -> None:
        from kato_core_lib.helpers.text_utils import dict_from_mapping
        self.assertEqual(
            dict_from_mapping({'x': {'inner': 1}}, 'x'),
            {'inner': 1},
        )

    def test_list_from_mapping_returns_empty_for_non_mapping(self) -> None:
        from kato_core_lib.helpers.text_utils import list_from_mapping
        self.assertEqual(list_from_mapping(None, 'x'), [])
        self.assertEqual(list_from_mapping('not a mapping', 'x'), [])

    def test_list_from_mapping_returns_empty_when_value_not_list(self) -> None:
        # Line 47: ``value if isinstance(value, list) else []``.
        from kato_core_lib.helpers.text_utils import list_from_mapping
        self.assertEqual(list_from_mapping({'x': 'not a list'}, 'x'), [])
        self.assertEqual(list_from_mapping({'x': {'dict': 1}}, 'x'), [])

    def test_list_from_mapping_passes_through_list_value(self) -> None:
        from kato_core_lib.helpers.text_utils import list_from_mapping
        self.assertEqual(list_from_mapping({'x': [1, 2, 3]}, 'x'), [1, 2, 3])


# --------------------------------------------------------------------------
# runtime_identity_utils — fingerprint + CLI main
# --------------------------------------------------------------------------


class RuntimeIdentityUtilsTests(unittest.TestCase):
    """Lines 32, 52-62, 66: directory-not-a-dir skip; ``main`` CLI
    entry point; ``__main__`` block. These compute the source
    fingerprint used to detect 'kato has been mid-air patched' state."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_runtime_source_fingerprint_skips_missing_optional_dir(self) -> None:
        # Line 32: ``if not directory.is_dir(): continue`` — the root
        # may not have all of _ROOT_DIRS present (e.g. a checkout
        # without ``scripts/``); fingerprint must not crash.
        # Create only one of the optional source files so we get a
        # deterministic non-empty fingerprint.
        (self.root / 'AGENTS.md').write_text('rules', encoding='utf-8')
        from kato_core_lib.helpers.runtime_identity_utils import (
            runtime_source_fingerprint,
        )
        fp = runtime_source_fingerprint(self.root)
        # 64 hex chars (sha256.hexdigest).
        self.assertRegex(fp, r'^[0-9a-f]{64}$')

    def test_main_prints_fingerprint_and_returns_zero(self) -> None:
        # Lines 52-62: argparse + print fingerprint + return 0.
        (self.root / 'AGENTS.md').write_text('rules', encoding='utf-8')
        captured = io.StringIO()
        from kato_core_lib.helpers.runtime_identity_utils import main
        with patch.object(sys, 'stdout', new=captured):
            rc = main(['--root', str(self.root)])
        self.assertEqual(rc, 0)
        # The printed line is a 64-char sha256 hex digest.
        output = captured.getvalue().strip()
        self.assertRegex(output, r'^[0-9a-f]{64}$')

    def test_module_as_script_entry_point_calls_main_with_systemexit(
        self,
    ) -> None:
        # Line 66: ``if __name__ == '__main__': raise SystemExit(main())``.
        # Drive via runpy so the module-as-script entry is genuinely
        # exercised (not just simulated).
        import runpy
        (self.root / 'AGENTS.md').write_text('rules', encoding='utf-8')
        argv_backup = sys.argv
        sys.argv = ['runtime_identity_utils', '--root', str(self.root)]
        try:
            with patch.object(sys, 'stdout', new=io.StringIO()), \
                 self.assertRaises(SystemExit) as ctx:
                runpy.run_module(
                    'kato_core_lib.helpers.runtime_identity_utils',
                    run_name='__main__',
                )
            self.assertEqual(ctx.exception.code, 0)
        finally:
            sys.argv = argv_backup


# --------------------------------------------------------------------------
# retry_utils — every branch of the retry policy
# --------------------------------------------------------------------------


class RetryUtilsTests(unittest.TestCase):
    """Lines 89, 105, 112, 122-124, 151, 153, 155, 158 — retry policy
    paths. Each named branch is a different "operator sees a clear
    retry message" path that kato relies on for transient failures."""

    def test_run_with_retry_returns_last_response_after_exhausting(
        self,
    ) -> None:
        # Line 87: when all attempts succeed but every response is
        # retryable, the function returns the LAST observed response.
        from kato_core_lib.helpers.retry_utils import run_with_retry
        responses = [
            SimpleNamespace(status_code=503),
            SimpleNamespace(status_code=503),
            SimpleNamespace(status_code=503),
        ]
        it = iter(responses)
        with patch('time.sleep'):
            result = run_with_retry(lambda: next(it), 3)
        self.assertIs(result, responses[-1])

    def test_run_with_retry_returns_none_when_max_retries_zero(self) -> None:
        # Line 89: the for-loop never iterates → fall through to
        # ``return last_response`` (which stays at its initial None).
        # Defensive: a 0-retries configuration shouldn't crash; it
        # should just return None so callers can branch on a missing
        # response.
        from kato_core_lib.helpers.retry_utils import run_with_retry
        op = MagicMock()
        result = run_with_retry(op, 0)
        self.assertIsNone(result)
        op.assert_not_called()

    def test_retry_after_seconds_replaces_naive_tz_with_utc(self) -> None:
        # Line 123: when parsedate_to_datetime returns a naive datetime
        # (no zone info in the Retry-After header), we attach UTC so
        # the subtraction below uses a consistent zone. Without this,
        # ``aware - naive`` would raise TypeError.
        from kato_core_lib.helpers.retry_utils import _retry_after_seconds
        # Patch parsedate_to_datetime to return a naive datetime so we
        # hit the explicit branch deterministically (the email parser's
        # behaviour for zone-free input varies across Python versions).
        future_naive = datetime.utcnow() + timedelta(seconds=5)
        with patch(
            'kato_core_lib.helpers.retry_utils.parsedate_to_datetime',
            return_value=future_naive,
        ):
            response = SimpleNamespace(
                status_code=429,
                headers={'Retry-After': 'irrelevant'},
            )
            delay = _retry_after_seconds(response)
        # Within a small window of "5 seconds from now".
        self.assertGreaterEqual(delay, 0.0)
        self.assertLessEqual(delay, 10.0)

    def test_retry_after_seconds_returns_none_for_non_429(self) -> None:
        # Line 101: only 429 carries Retry-After semantics; for other
        # retryable codes we fall through to exponential backoff.
        from kato_core_lib.helpers.retry_utils import _retry_delay_seconds
        delay = _retry_delay_seconds(
            attempt=0,
            response=SimpleNamespace(status_code=503),
        )
        # Exponential backoff: 2^0 → between 1.0 and 2.0.
        self.assertGreaterEqual(delay, 1.0)
        self.assertLessEqual(delay, 2.0)

    def test_retry_after_seconds_skips_when_headers_missing_get(self) -> None:
        # Line 105: headers without a .get attribute → None. Defensive
        # against odd response objects (e.g. a string instead of dict).
        from kato_core_lib.helpers.retry_utils import _retry_after_seconds
        response = SimpleNamespace(status_code=429, headers='not a mapping')
        self.assertIsNone(_retry_after_seconds(response))

    def test_retry_after_seconds_skips_blank_header(self) -> None:
        # Line 112: whitespace-only Retry-After is treated as missing.
        from kato_core_lib.helpers.retry_utils import _retry_after_seconds
        response = SimpleNamespace(
            status_code=429,
            headers={'Retry-After': '   '},
        )
        self.assertIsNone(_retry_after_seconds(response))

    def test_retry_after_seconds_parses_http_date(self) -> None:
        # Lines 118-124: parsedate_to_datetime path. A future HTTP
        # date returns the wait-until-that-time delay.
        from kato_core_lib.helpers.retry_utils import _retry_after_seconds
        future = datetime.now(timezone.utc) + timedelta(seconds=5)
        # RFC-1123 format: "Thu, 14 May 2026 12:00:00 GMT"
        http_date = future.strftime('%a, %d %b %Y %H:%M:%S GMT')
        response = SimpleNamespace(
            status_code=429,
            headers={'Retry-After': http_date},
        )
        delay = _retry_after_seconds(response)
        # Within a small window of "5 seconds from now".
        self.assertGreaterEqual(delay, 0.0)
        self.assertLessEqual(delay, 10.0)

    def test_retry_after_seconds_returns_none_on_invalid_http_date(
        self,
    ) -> None:
        # Line 121: ``except (TypeError, ValueError, IndexError,
        # OverflowError): return None`` — malformed date string.
        from kato_core_lib.helpers.retry_utils import _retry_after_seconds
        response = SimpleNamespace(
            status_code=429,
            headers={'Retry-After': 'definitely not a date'},
        )
        self.assertIsNone(_retry_after_seconds(response))

    def test_retry_exception_summary_remote_disconnect(self) -> None:
        # Line 149: ``Remote server closed connection`` summary.
        from kato_core_lib.helpers.retry_utils import _retry_exception_summary
        exc = RuntimeError('Remote end closed connection without response')
        self.assertEqual(
            _retry_exception_summary(exc),
            'Remote server closed connection',
        )

    def test_retry_exception_summary_read_timeout(self) -> None:
        # Line 151: ``Request timed out`` summary.
        from kato_core_lib.helpers.retry_utils import _retry_exception_summary
        exc = RuntimeError('Read timed out after 60 seconds')
        self.assertEqual(_retry_exception_summary(exc), 'Request timed out')

    def test_retry_exception_summary_connect_timeout(self) -> None:
        # Line 153: ``Connection timed out`` summary.
        from kato_core_lib.helpers.retry_utils import _retry_exception_summary
        exc = RuntimeError('ConnectTimeout while opening socket')
        self.assertEqual(_retry_exception_summary(exc), 'Connection timed out')

    def test_retry_exception_summary_dns_failure(self) -> None:
        # Line 155: DNS resolution failure summary.
        from kato_core_lib.helpers.retry_utils import _retry_exception_summary
        exc = RuntimeError('Name or service not known')
        self.assertEqual(
            _retry_exception_summary(exc),
            'Could not resolve remote host',
        )

    def test_retry_exception_summary_falls_back_to_class_name(self) -> None:
        # Line 158: empty error text → class name fallback.
        from kato_core_lib.helpers.retry_utils import _retry_exception_summary

        class _CustomTimeout(Exception):
            def __str__(self):
                return ''

        self.assertEqual(
            _retry_exception_summary(_CustomTimeout()),
            '_CustomTimeout',
        )


# --------------------------------------------------------------------------
# review_comment_utils — _coerce_optional_int + body prefix variants
# --------------------------------------------------------------------------


class ReviewCommentUtilsTests(unittest.TestCase):
    """Lines 131-132, 178-179, 259, 326, 330, 333, 347-350 — defensive
    paths in the review-comment helpers used by the agent."""

    def test_coerce_optional_int_returns_empty_for_non_numeric(self) -> None:
        # Lines 131-132: TypeError/ValueError on int(...) → ''.
        from kato_core_lib.helpers.review_comment_utils import (
            _coerce_optional_int,
        )
        self.assertEqual(_coerce_optional_int('not a number'), '')
        self.assertEqual(_coerce_optional_int(object()), '')

    def test_coerce_optional_int_returns_empty_for_non_positive(self) -> None:
        # Line 134: ``if n <= 0: return ''`` — line numbers must be 1+.
        from kato_core_lib.helpers.review_comment_utils import (
            _coerce_optional_int,
        )
        self.assertEqual(_coerce_optional_int(0), '')
        self.assertEqual(_coerce_optional_int(-5), '')

    def test_is_kato_reply_recognises_small_wrapper(self) -> None:
        # Lines 177-178: the leading ``<small>`` wrapper is also
        # stripped so kato's own auto-replies (sometimes rendered
        # smaller with ``<small>``) still match the prefix check.
        from kato_core_lib.helpers.review_comment_utils import (
            is_kato_review_comment_reply,
            KATO_REVIEW_COMMENT_FIXED_PREFIX,
        )
        comment = SimpleNamespace(
            body=f'<small>{KATO_REVIEW_COMMENT_FIXED_PREFIX}123 on PR',
        )
        self.assertTrue(is_kato_review_comment_reply(comment))

    def test_did_nothing_summary_surfaces_error_field(self) -> None:
        # Line 326: ``if error: return DID_NOTHING_PREFIX + truncated``.
        from kato_core_lib.helpers.review_comment_utils import (
            review_comment_reply_body,
            ReviewReplyTemplate,
        )
        body = review_comment_reply_body({
            'error': 'agent timed out after 5 minutes',
        })
        self.assertIn('agent timed out', body)
        self.assertIn(ReviewReplyTemplate.DID_NOTHING_PREFIX, body)

    def test_did_nothing_summary_surfaces_result_hint(self) -> None:
        # Line 330: when no ``error`` but there is a ``result`` or
        # ``message`` or ``summary`` text, surface that instead.
        from kato_core_lib.helpers.review_comment_utils import (
            review_comment_reply_body,
        )
        body = review_comment_reply_body({
            'result': "I reviewed the code and there's nothing to fix.",
        })
        self.assertIn("nothing to fix", body)

    def test_did_nothing_summary_pipeline_failure_with_no_message(
        self,
    ) -> None:
        # Lines 331-336: success=False AND no error AND no message →
        # the explicit "pipeline reported failure" stub.
        from kato_core_lib.helpers.review_comment_utils import (
            review_comment_reply_body,
        )
        from kato_core_lib.data_layers.data.fields import ImplementationFields
        body = review_comment_reply_body({
            ImplementationFields.SUCCESS: False,
        })
        self.assertIn('pipeline reported failure', body)

    def test_is_question_comment_rejects_non_question_start_word(self) -> None:
        # Line 259: ``if not lowered.startswith(_QUESTION_START_WORDS):
        # return False``. Defensive — a body that ends with ? but
        # doesn't start with what/why/how/etc. is treated as a fix
        # request (the conservative default), not a question.
        from kato_core_lib.helpers.review_comment_utils import (
            is_question_comment,
        )
        comment = SimpleNamespace(body='this is wrong?')
        self.assertFalse(is_question_comment(comment))

    def test_truncate_caps_long_text(self) -> None:
        # Lines 347-350: truncation cap so a multi-page transcript
        # doesn't drown the review-comment thread.
        from kato_core_lib.helpers.review_comment_utils import _truncate
        # ``_DID_NOTHING_REASON_MAX_CHARS`` is 600 — make a 700-char
        # input and verify truncation + ellipsis suffix.
        long_text = 'x' * 700
        result = _truncate(long_text)
        self.assertLess(len(result), len(long_text))
        self.assertTrue(result.endswith('…'))


# --------------------------------------------------------------------------
# status_broadcaster_utils — empty-message null entry, wait_for_new
# --------------------------------------------------------------------------


class StatusBroadcasterTests(unittest.TestCase):
    """Lines 32, 55, 74, 77-78, 86-95, 116-118, 160 — the live status
    feed used by the planning UI's SSE endpoint."""

    def test_status_entry_to_dict_serializes_fields(self) -> None:
        # Line 32: ``StatusEntry.to_dict()`` round-trip.
        from kato_core_lib.helpers.status_broadcaster_utils import StatusEntry
        entry = StatusEntry(
            sequence=1, epoch=1234.5, level='INFO',
            logger='kato', message='hello',
        )
        d = entry.to_dict()
        self.assertEqual(d['sequence'], 1)
        self.assertEqual(d['message'], 'hello')

    def test_publish_returns_null_entry_for_blank_message(self) -> None:
        # Line 55: ``if not normalized: return _NULL_ENTRY``. Avoids
        # filling the buffer with empty noise — log records with empty
        # bodies (e.g. malformed format strings) are discarded.
        from kato_core_lib.helpers.status_broadcaster_utils import (
            StatusBroadcaster,
        )
        broadcaster = StatusBroadcaster()
        entry = broadcaster.publish(level='INFO', logger_name='x', message='   ')
        self.assertEqual(entry.sequence, 0)
        self.assertEqual(entry.message, '')

    def test_recent_filters_by_since_sequence(self) -> None:
        # Lines 73-74: ``[entry for entry in self._buffer if entry.sequence > since_sequence]``.
        from kato_core_lib.helpers.status_broadcaster_utils import (
            StatusBroadcaster,
        )
        broadcaster = StatusBroadcaster()
        broadcaster.publish(level='INFO', logger_name='x', message='first')
        broadcaster.publish(level='INFO', logger_name='x', message='second')
        # since_sequence > 0 → only entries newer than that.
        recent = broadcaster.recent(since_sequence=1)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0].message, 'second')

    def test_latest_sequence_returns_current_value(self) -> None:
        # Lines 77-78: ``return self._sequence`` under the lock.
        from kato_core_lib.helpers.status_broadcaster_utils import (
            StatusBroadcaster,
        )
        broadcaster = StatusBroadcaster()
        self.assertEqual(broadcaster.latest_sequence(), 0)
        broadcaster.publish(level='INFO', logger_name='x', message='hi')
        self.assertEqual(broadcaster.latest_sequence(), 1)

    def test_wait_for_new_returns_empty_on_timeout(self) -> None:
        # Lines 86-91: timeout expires without any new entry → [].
        from kato_core_lib.helpers.status_broadcaster_utils import (
            StatusBroadcaster,
        )
        broadcaster = StatusBroadcaster()
        # No publish — wait briefly and observe the empty return.
        result = broadcaster.wait_for_new(since_sequence=0, timeout=0.01)
        self.assertEqual(result, [])

    def test_wait_for_new_wakes_on_publish(self) -> None:
        # Lines 92-95: blocks until publish() bumps the sequence,
        # then returns the new entries.
        from kato_core_lib.helpers.status_broadcaster_utils import (
            StatusBroadcaster,
        )
        broadcaster = StatusBroadcaster()

        def publish_after_delay():
            time.sleep(0.05)
            broadcaster.publish(
                level='INFO', logger_name='x', message='delayed',
            )

        thread = threading.Thread(target=publish_after_delay)
        thread.start()
        try:
            result = broadcaster.wait_for_new(since_sequence=0, timeout=1.0)
            self.assertGreaterEqual(len(result), 1)
            self.assertEqual(result[-1].message, 'delayed')
        finally:
            thread.join()

    def test_wait_for_new_returns_filtered_when_since_positive(self) -> None:
        # Line 95: when ``since_sequence > 0``, return the filtered
        # list (not the whole buffer).
        from kato_core_lib.helpers.status_broadcaster_utils import (
            StatusBroadcaster,
        )
        broadcaster = StatusBroadcaster()
        # Pre-fill so wait_for_new doesn't block (sequence > since).
        broadcaster.publish(level='INFO', logger_name='x', message='one')
        broadcaster.publish(level='INFO', logger_name='x', message='two')
        # since=1 → only the 2nd entry.
        result = broadcaster.wait_for_new(since_sequence=1, timeout=0.1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].message, 'two')

    def test_broadcast_handler_handles_get_message_failure(self) -> None:
        # Lines 116-118: ``record.getMessage()`` raises (rare —
        # malformed format string + args) → handleError + return.
        from kato_core_lib.helpers.status_broadcaster_utils import (
            StatusBroadcaster, StatusBroadcastHandler,
        )
        broadcaster = StatusBroadcaster()
        handler = StatusBroadcastHandler(broadcaster)
        bad_record = MagicMock()
        bad_record.getMessage.side_effect = ValueError('bad format')
        # Should not raise. The handleError path is exercised.
        with patch.object(handler, 'handleError') as mock_handle_error:
            handler.emit(bad_record)
        mock_handle_error.assert_called_once_with(bad_record)
        # Nothing was published.
        self.assertEqual(broadcaster.latest_sequence(), 0)

    def test_install_broadcast_handler_replaces_prior_handler(self) -> None:
        # Line 160: ``target.removeHandler(existing)`` — re-installing
        # the same broadcaster must replace, not duplicate.
        from kato_core_lib.helpers.status_broadcaster_utils import (
            StatusBroadcaster, install_status_broadcast_handler,
        )
        broadcaster = StatusBroadcaster()
        root = logging.getLogger('test_status_broadcaster_reinstall')
        # First install.
        install_status_broadcast_handler(broadcaster, root_logger=root)
        first_count = sum(
            1 for h in root.handlers
            if h.__class__.__name__ == 'StatusBroadcastHandler'
        )
        # Re-install with the same broadcaster — should replace, not add.
        install_status_broadcast_handler(broadcaster, root_logger=root)
        second_count = sum(
            1 for h in root.handlers
            if h.__class__.__name__ == 'StatusBroadcastHandler'
        )
        # Always exactly one handler bound to this broadcaster on root.
        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 1)
        # Cleanup.
        for h in list(root.handlers):
            if h.__class__.__name__ == 'StatusBroadcastHandler':
                root.removeHandler(h)


# --------------------------------------------------------------------------
# shell_status_utils — spinner branches
# --------------------------------------------------------------------------


class _TtyStream(io.StringIO):
    def isatty(self) -> bool:
        return True


class ShellStatusUtilsTests(unittest.TestCase):
    """Lines 21, 59-85, 95, 99-100, 147-149, 162, 164, 167-176 —
    every branch of the inline-status spinners (TTY + non-TTY)."""

    def test_sleep_with_countdown_spinner_short_circuits(self) -> None:
        # Line 59: ``if total_seconds <= 0: return``.
        from kato_core_lib.helpers.shell_status_utils import (
            sleep_with_countdown_spinner,
        )
        sleeper = MagicMock()
        sleep_with_countdown_spinner(0, status_text='Waiting', sleep_fn=sleeper)
        sleeper.assert_not_called()

    def test_sleep_with_countdown_spinner_non_tty_fallback(self) -> None:
        # Lines 62-65: non-TTY → plain sleep_fn.
        from kato_core_lib.helpers.shell_status_utils import (
            sleep_with_countdown_spinner,
        )
        sleeper = MagicMock()
        non_tty = io.StringIO()
        sleep_with_countdown_spinner(
            1.5, status_text='Waiting', sleep_fn=sleeper, stream=non_tty,
        )
        sleeper.assert_called_once_with(1.5)

    def test_sleep_with_countdown_spinner_renders_countdown_value(self) -> None:
        # Lines 67-85: spinner loop with countdown branch (default
        # path, line 74: ``displayed = max(1, int(ceil(remaining_seconds)))``).
        from kato_core_lib.helpers.shell_status_utils import (
            sleep_with_countdown_spinner,
        )
        tty = _TtyStream()
        sleep_with_countdown_spinner(
            0.5,
            status_text='Cooling down',
            sleep_fn=lambda _s: None,
            stream=tty,
        )
        out = tty.getvalue()
        self.assertIn('Cooling down', out)
        # At least one numeric frame.
        self.assertRegex(out, r'\d')

    def test_sleep_with_countdown_spinner_uses_explicit_countdown_arg(
        self,
    ) -> None:
        # Lines 75-76: when caller passes ``countdown_seconds``, it
        # pins the displayed value (used by outer-loop heartbeat).
        from kato_core_lib.helpers.shell_status_utils import (
            sleep_with_countdown_spinner,
        )
        tty = _TtyStream()
        sleep_with_countdown_spinner(
            0.3,
            status_text='Outer-loop',
            sleep_fn=lambda _s: None,
            stream=tty,
            countdown_seconds=42,
        )
        # The explicit countdown value appears in the spinner output.
        self.assertIn('42', tty.getvalue())

    def test_sleep_with_warmup_countdown_short_circuits(self) -> None:
        # Line 95: ``if total_seconds <= 0: return``.
        from kato_core_lib.helpers.shell_status_utils import (
            sleep_with_warmup_countdown,
        )
        sleeper = MagicMock()
        sleep_with_warmup_countdown(0, sleep_fn=sleeper)
        sleeper.assert_not_called()

    def test_sleep_with_warmup_countdown_non_tty(self) -> None:
        # Lines 97-100: non-TTY → plain sleep.
        from kato_core_lib.helpers.shell_status_utils import (
            sleep_with_warmup_countdown,
        )
        sleeper = MagicMock()
        non_tty = io.StringIO()
        sleep_with_warmup_countdown(1.0, sleep_fn=sleeper, stream=non_tty)
        sleeper.assert_called_once_with(1.0)

    def test_sleep_with_warmup_countdown_renders_seconds_label(self) -> None:
        # Lines 102-122: the spinner. Hits both singular (1 second)
        # and plural (>1 second) branches by spanning ~1.5 → 0.
        from kato_core_lib.helpers.shell_status_utils import (
            sleep_with_warmup_countdown,
        )
        tty = _TtyStream()
        sleep_with_warmup_countdown(
            1.5, sleep_fn=lambda _s: None, stream=tty,
        )
        out = tty.getvalue()
        self.assertIn('Waiting', out)
        self.assertIn('Kato', out)


# --------------------------------------------------------------------------
# lessons_data_access — read/write fail-safes + path validation
# --------------------------------------------------------------------------


class LessonsDataAccessDefensiveTests(unittest.TestCase):
    """Defensive paths in ``LessonsDataAccess`` — bad FS state must
    degrade to "no lessons available" rather than crash the spawn.
    Lines 56, 60, 64, 74-76, 118-119, 130-134, 160-161, 199, 215-216."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name).resolve()
        from kato_core_lib.data_layers.data_access.lessons_data_access import (
            LessonsDataAccess,
        )
        self.da = LessonsDataAccess(self.state_dir)

    def test_read_global_swallows_oserror_and_logs(self) -> None:
        # Lines 74-76: OSError on read_text → log + return ''.
        self.da._global_path.write_text('content', encoding='utf-8')
        with patch.object(Path, 'read_text', side_effect=OSError('locked')):
            self.assertEqual(self.da.read_global(), '')

    def test_last_compacted_returns_none_for_malformed_timestamp(self) -> None:
        # Lines 118-119: fromisoformat raises on a bad timestamp →
        # return None (treat as "never compacted") rather than crash.
        self.da._global_path.write_text(
            '<!-- last_compacted: not-an-iso-date -->\nbody\n',
            encoding='utf-8',
        )
        self.assertIsNone(self.da.last_compacted_at())

    def test_read_per_task_swallows_oserror(self) -> None:
        # Lines 130-134: OSError on read_text → log + return None.
        per_task = self.da._per_task_dir / 'PROJ-1.md'
        per_task.parent.mkdir(parents=True, exist_ok=True)
        per_task.write_text('content', encoding='utf-8')
        with patch.object(Path, 'read_text', side_effect=OSError('locked')):
            self.assertIsNone(self.da.read_per_task('PROJ-1'))

    def test_delete_per_task_swallows_unlink_oserror(self) -> None:
        # Lines 160-161: unlink raises → log + continue. No crash.
        per_task = self.da._per_task_dir / 'PROJ-2.md'
        per_task.parent.mkdir(parents=True, exist_ok=True)
        per_task.write_text('content', encoding='utf-8')
        with patch.object(Path, 'unlink', side_effect=OSError('denied')):
            # Should not raise.
            self.da.delete_per_task('PROJ-2')

    def test_normalize_task_id_rejects_none(self) -> None:
        # Line 199: ``if task_id is None: return ''`` — defensive
        # against a caller passing None where a string was expected.
        from kato_core_lib.data_layers.data_access.lessons_data_access import (
            LessonsDataAccess,
        )
        self.assertEqual(LessonsDataAccess._normalize_task_id(None), '')

    def test_normalize_task_id_rejects_path_escape(self) -> None:
        # Path-escape defense: ``..``, ``/``, ``\``, null bytes are
        # forbidden so a caller can't write outside the lessons dir.
        from kato_core_lib.data_layers.data_access.lessons_data_access import (
            LessonsDataAccess,
        )
        for bad in ('../escape', 'foo/bar', 'foo\\bar', 'evil\x00name', '.', '..'):
            self.assertEqual(LessonsDataAccess._normalize_task_id(bad), '')

    def test_read_first_line_swallows_oserror(self) -> None:
        # Lines 215-216: open() raises → return ''.
        from kato_core_lib.data_layers.data_access.lessons_data_access import (
            LessonsDataAccess,
        )
        bogus = self.state_dir / 'nope.md'
        bogus.write_text('hi', encoding='utf-8')
        with patch.object(Path, 'open', side_effect=OSError('cannot open')):
            self.assertEqual(LessonsDataAccess._read_first_line(bogus), '')

    def test_write_per_task_rejects_invalid_id(self) -> None:
        # Lines 140-144: write_per_task with an invalid id (rejected
        # by _normalize_task_id) → warn + return False rather than
        # writing to an unexpected path.
        self.assertFalse(self.da.write_per_task('../escape', 'content'))

    def test_strip_timestamp_header_returns_empty_for_empty_text(self) -> None:
        # Line 222: ``if not text: return ''``.
        from kato_core_lib.data_layers.data_access.lessons_data_access import (
            strip_timestamp_header,
        )
        self.assertEqual(strip_timestamp_header(''), '')


if __name__ == '__main__':
    unittest.main()
