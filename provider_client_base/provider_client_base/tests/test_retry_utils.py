import time
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import MagicMock, patch

from provider_client_base.provider_client_base.helpers.retry_utils import (
    TRANSIENT_EXCEPTION_NAMES,
    TRANSIENT_STATUS_CODES,
    _bounded_jitter,
    _operation_details,
    _retry_after_seconds,
    _retry_delay_seconds,
    _retry_exception_summary,
    _service_name_from_client_name,
    is_retryable_exception,
    is_retryable_response,
    run_with_retry,
)


def _mock_response(status_code: int, headers: dict | None = None):
    r = MagicMock()
    r.status_code = status_code
    r.headers = headers or {}
    return r


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TransientConstantsTests(unittest.TestCase):
    def test_transient_status_codes_contains_expected(self):
        self.assertEqual(TRANSIENT_STATUS_CODES, {408, 429, 500, 502, 503, 504})

    def test_transient_exception_names_contains_expected(self):
        self.assertIn('ConnectionError', TRANSIENT_EXCEPTION_NAMES)
        self.assertIn('ConnectTimeout', TRANSIENT_EXCEPTION_NAMES)
        self.assertIn('ReadTimeout', TRANSIENT_EXCEPTION_NAMES)
        self.assertIn('Timeout', TRANSIENT_EXCEPTION_NAMES)
        self.assertIn('TimeoutError', TRANSIENT_EXCEPTION_NAMES)


# ---------------------------------------------------------------------------
# is_retryable_exception
# ---------------------------------------------------------------------------


class IsRetryableExceptionTests(unittest.TestCase):
    def test_connection_error_is_retryable(self):
        self.assertTrue(is_retryable_exception(ConnectionError('oops')))

    def test_timeout_error_is_retryable(self):
        self.assertTrue(is_retryable_exception(TimeoutError('oops')))

    def test_named_connect_timeout_is_retryable(self):
        exc = type('ConnectTimeout', (Exception,), {})('boom')
        self.assertTrue(is_retryable_exception(exc))

    def test_named_read_timeout_is_retryable(self):
        exc = type('ReadTimeout', (Exception,), {})('boom')
        self.assertTrue(is_retryable_exception(exc))

    def test_named_timeout_is_retryable(self):
        exc = type('Timeout', (Exception,), {})('boom')
        self.assertTrue(is_retryable_exception(exc))

    def test_named_timeout_error_is_retryable(self):
        exc = type('TimeoutError', (Exception,), {})('boom')
        self.assertTrue(is_retryable_exception(exc))

    def test_value_error_is_not_retryable(self):
        self.assertFalse(is_retryable_exception(ValueError('bad')))

    def test_runtime_error_is_not_retryable(self):
        self.assertFalse(is_retryable_exception(RuntimeError('bad')))

    def test_generic_exception_is_not_retryable(self):
        self.assertFalse(is_retryable_exception(Exception('generic')))


# ---------------------------------------------------------------------------
# is_retryable_response
# ---------------------------------------------------------------------------


class IsRetryableResponseTests(unittest.TestCase):
    def test_408_is_retryable(self):
        self.assertTrue(is_retryable_response(_mock_response(408)))

    def test_429_is_retryable(self):
        self.assertTrue(is_retryable_response(_mock_response(429)))

    def test_500_is_retryable(self):
        self.assertTrue(is_retryable_response(_mock_response(500)))

    def test_502_is_retryable(self):
        self.assertTrue(is_retryable_response(_mock_response(502)))

    def test_503_is_retryable(self):
        self.assertTrue(is_retryable_response(_mock_response(503)))

    def test_504_is_retryable(self):
        self.assertTrue(is_retryable_response(_mock_response(504)))

    def test_200_is_not_retryable(self):
        self.assertFalse(is_retryable_response(_mock_response(200)))

    def test_201_is_not_retryable(self):
        self.assertFalse(is_retryable_response(_mock_response(201)))

    def test_404_is_not_retryable(self):
        self.assertFalse(is_retryable_response(_mock_response(404)))

    def test_401_is_not_retryable(self):
        self.assertFalse(is_retryable_response(_mock_response(401)))

    def test_none_is_not_retryable(self):
        self.assertFalse(is_retryable_response(None))

    def test_object_without_status_code_is_not_retryable(self):
        self.assertFalse(is_retryable_response(object()))


# ---------------------------------------------------------------------------
# _retry_after_seconds
# ---------------------------------------------------------------------------


class RetryAfterSecondsTests(unittest.TestCase):
    def test_non_429_returns_none(self):
        self.assertIsNone(_retry_after_seconds(_mock_response(503)))

    def test_none_response_returns_none(self):
        self.assertIsNone(_retry_after_seconds(None))

    def test_429_no_header_returns_none(self):
        r = _mock_response(429, headers={})
        self.assertIsNone(_retry_after_seconds(r))

    def test_429_numeric_header_returns_float(self):
        r = _mock_response(429, headers={'Retry-After': '30'})
        self.assertEqual(_retry_after_seconds(r), 30.0)

    def test_429_zero_numeric_returns_zero(self):
        r = _mock_response(429, headers={'Retry-After': '0'})
        self.assertEqual(_retry_after_seconds(r), 0.0)

    def test_429_negative_numeric_clamped_to_zero(self):
        r = _mock_response(429, headers={'Retry-After': '-10'})
        self.assertEqual(_retry_after_seconds(r), 0.0)

    def test_429_float_numeric_header(self):
        r = _mock_response(429, headers={'Retry-After': '2.5'})
        self.assertEqual(_retry_after_seconds(r), 2.5)

    def test_429_http_date_header_returns_positive_float(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=60)
        r = _mock_response(429, headers={'Retry-After': format_datetime(future)})
        result = _retry_after_seconds(r)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0.0)
        self.assertLessEqual(result, 70.0)

    def test_429_past_http_date_clamped_to_zero(self):
        past = datetime.now(timezone.utc) - timedelta(seconds=60)
        r = _mock_response(429, headers={'Retry-After': format_datetime(past)})
        result = _retry_after_seconds(r)
        self.assertEqual(result, 0.0)

    def test_429_invalid_header_returns_none(self):
        r = _mock_response(429, headers={'Retry-After': 'not-a-date-or-number'})
        self.assertIsNone(_retry_after_seconds(r))

    def test_429_blank_header_returns_none(self):
        r = _mock_response(429, headers={'Retry-After': '   '})
        self.assertIsNone(_retry_after_seconds(r))

    def test_headers_without_get_returns_none(self):
        r = _mock_response(429)
        r.headers = 'not-a-mapping'
        self.assertIsNone(_retry_after_seconds(r))


# ---------------------------------------------------------------------------
# _retry_delay_seconds
# ---------------------------------------------------------------------------


class RetryDelaySecondsTests(unittest.TestCase):
    def test_no_response_uses_exponential_backoff(self):
        with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', return_value=2.0):
            delay = _retry_delay_seconds(0)
        self.assertEqual(delay, 2.0)

    def test_attempt_1_base_delay_is_2(self):
        with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', side_effect=lambda lo, hi: lo) as m:
            delay = _retry_delay_seconds(1)
        m.assert_called_once_with(2.0, 4.0)
        self.assertEqual(delay, 2.0)

    def test_attempt_2_base_delay_is_4(self):
        with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', side_effect=lambda lo, hi: lo) as m:
            delay = _retry_delay_seconds(2)
        m.assert_called_once_with(4.0, 8.0)

    def test_retry_after_header_taken_as_minimum_with_bounded_jitter(self):
        # Honoring ``Retry-After`` is the load-bearing behavior; the
        # tiny jitter on top spreads parallel clients so they don't
        # all wake at the same instant and re-hit the same window.
        # Jitter is bounded at min(25% of base, 10s) so an
        # ``Retry-After: 45`` lands in [45, 45 + 10].
        r = _mock_response(429, headers={'Retry-After': '45'})
        with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', side_effect=lambda lo, hi: hi) as m:
            delay = _retry_delay_seconds(0, r)
        # Jitter cap is min(45 * 0.25, 10) = 10.
        m.assert_called_once_with(0.0, 10.0)
        self.assertEqual(delay, 55.0)

    def test_retry_after_header_jitter_capped_by_fraction_when_small(self):
        # ``Retry-After: 4`` → 25% jitter = 1s, which is below the
        # 10s ceiling; jitter range is therefore [0, 1].
        r = _mock_response(429, headers={'Retry-After': '4'})
        with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', side_effect=lambda lo, hi: hi) as m:
            delay = _retry_delay_seconds(0, r)
        m.assert_called_once_with(0.0, 1.0)
        self.assertEqual(delay, 5.0)

    def test_429_without_retry_after_uses_long_backoff(self):
        # The bug the user reported: 1/2/4s exponential never escapes
        # a real rate-limit window. 429 without ``Retry-After`` now
        # gets the dedicated 15s / 30s / 60s schedule (with jitter
        # x1.0-1.5).
        r = _mock_response(429)
        with patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.random.uniform',
            side_effect=lambda lo, hi: lo,
        ) as m:
            delay_0 = _retry_delay_seconds(0, r)
            delay_1 = _retry_delay_seconds(1, r)
            delay_2 = _retry_delay_seconds(2, r)
            # Capped at 120s; attempt 3 and beyond also stay at 120.
            delay_3 = _retry_delay_seconds(3, r)
            delay_huge = _retry_delay_seconds(10, r)
        # attempt 0: base 15, range [15, 22.5]
        self.assertEqual(delay_0, 15.0)
        # attempt 1: base 30, range [30, 45]
        self.assertEqual(delay_1, 30.0)
        # attempt 2: base 60, range [60, 90]
        self.assertEqual(delay_2, 60.0)
        # attempts 3+: capped at 120.
        self.assertEqual(delay_3, 120.0)
        self.assertEqual(delay_huge, 120.0)
        self.assertEqual(m.call_count, 5)

    def test_non_429_response_uses_short_exponential_backoff(self):
        # 5xx blips still use the fast 1/2/4s schedule — they're
        # usually transient node failures that recover quickly.
        r = _mock_response(503)
        with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', return_value=1.5):
            delay = _retry_delay_seconds(0, r)
        self.assertEqual(delay, 1.5)


# ---------------------------------------------------------------------------
# _bounded_jitter
# ---------------------------------------------------------------------------


class BoundedJitterTests(unittest.TestCase):
    def test_zero_base_returns_zero_without_calling_random(self):
        # Line 119: ``upper <= 0`` early-returns 0.0 so we never call
        # ``random.uniform(0, 0)``. Locks the non-negative jitter
        # contract — a zero-second ``Retry-After`` must not crash or
        # add negative slack.
        with patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.random.uniform',
        ) as m:
            self.assertEqual(_bounded_jitter(0.0), 0.0)
        m.assert_not_called()

    def test_negative_base_returns_zero(self):
        # Defensive: a negative base (shouldn't happen, but a bad
        # ``Retry-After`` string parser could feed one in) must also
        # short-circuit to 0.0 rather than asking ``random.uniform``
        # for a negative range.
        with patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.random.uniform',
        ) as m:
            self.assertEqual(_bounded_jitter(-5.0), 0.0)
        m.assert_not_called()

    def test_positive_base_uses_random_uniform(self):
        with patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.random.uniform',
            return_value=1.5,
        ) as m:
            self.assertEqual(_bounded_jitter(10.0), 1.5)
        m.assert_called_once()


# ---------------------------------------------------------------------------
# _operation_details
# ---------------------------------------------------------------------------


class OperationDetailsTests(unittest.TestCase):
    def test_parses_standard_format(self):
        service, method, url = _operation_details('GitHubClient GET https://api.github.com/repos')
        self.assertEqual(service, 'GitHub')
        self.assertEqual(method, 'GET')
        self.assertEqual(url, 'https://api.github.com/repos')

    def test_strips_client_suffix(self):
        service, _, _ = _operation_details('BitbucketClient POST https://api.bitbucket.org')
        self.assertEqual(service, 'Bitbucket')

    def test_plain_string_fallback(self):
        service, method, url = _operation_details('my-operation')
        self.assertEqual(service, 'Request')
        self.assertEqual(method, 'request')
        self.assertEqual(url, 'my-operation')

    def test_empty_string_fallback(self):
        service, method, url = _operation_details('')
        self.assertEqual(service, 'Request')
        self.assertEqual(method, 'request')
        self.assertEqual(url, 'request')

    def test_none_fallback(self):
        service, method, url = _operation_details(None)  # type: ignore[arg-type]
        self.assertEqual(service, 'Request')

    def test_lowercase_method_not_matched(self):
        service, method, url = _operation_details('SomeClient get https://example.com')
        self.assertEqual(service, 'Request')

    def test_delete_method_parsed(self):
        service, method, url = _operation_details('JiraClient DELETE https://jira.example.com/issue/1')
        self.assertEqual(service, 'Jira')
        self.assertEqual(method, 'DELETE')


# ---------------------------------------------------------------------------
# _service_name_from_client_name
# ---------------------------------------------------------------------------


class ServiceNameFromClientNameTests(unittest.TestCase):
    def test_strips_client_suffix(self):
        self.assertEqual(_service_name_from_client_name('GitHubClient'), 'GitHub')

    def test_no_suffix_unchanged(self):
        self.assertEqual(_service_name_from_client_name('GitHub'), 'GitHub')

    def test_empty_string_returns_request(self):
        self.assertEqual(_service_name_from_client_name(''), 'Request')

    def test_none_returns_request(self):
        self.assertEqual(_service_name_from_client_name(None), 'Request')  # type: ignore[arg-type]

    def test_whitespace_only_returns_request(self):
        self.assertEqual(_service_name_from_client_name('   '), 'Request')

    def test_just_client_suffix_returns_request(self):
        self.assertEqual(_service_name_from_client_name('Client'), 'Request')

    def test_strips_whitespace_around_name(self):
        self.assertEqual(_service_name_from_client_name('  GitHubClient  '), 'GitHub')


# ---------------------------------------------------------------------------
# _retry_exception_summary
# ---------------------------------------------------------------------------


class RetryExceptionSummaryTests(unittest.TestCase):
    def test_remote_disconnected(self):
        summary = _retry_exception_summary(Exception('Remote end closed connection without response'))
        self.assertEqual(summary, 'Remote server closed connection')

    def test_connection_aborted(self):
        summary = _retry_exception_summary(Exception('Connection aborted.'))
        self.assertEqual(summary, 'Remote server closed connection')

    def test_remote_disconnected_keyword(self):
        summary = _retry_exception_summary(Exception('RemoteDisconnected: something'))
        self.assertEqual(summary, 'Remote server closed connection')

    def test_read_timed_out(self):
        summary = _retry_exception_summary(Exception('Read timed out. (read timeout=30)'))
        self.assertEqual(summary, 'Request timed out')

    def test_read_timeout_param(self):
        summary = _retry_exception_summary(Exception('read timeout=10'))
        self.assertEqual(summary, 'Request timed out')

    def test_connect_timeout(self):
        summary = _retry_exception_summary(Exception('ConnectTimeout raised'))
        self.assertEqual(summary, 'Connection timed out')

    def test_connect_timeout_lowercase(self):
        summary = _retry_exception_summary(Exception('connect timeout occurred'))
        self.assertEqual(summary, 'Connection timed out')

    def test_name_resolution_failure(self):
        summary = _retry_exception_summary(Exception('Name or service not known'))
        self.assertEqual(summary, 'Could not resolve remote host')

    def test_temporary_name_resolution_failure(self):
        summary = _retry_exception_summary(Exception('Temporary failure in name resolution'))
        self.assertEqual(summary, 'Could not resolve remote host')

    def test_generic_error_returns_message(self):
        summary = _retry_exception_summary(Exception('something went wrong'))
        self.assertEqual(summary, 'something went wrong')

    def test_trailing_period_stripped(self):
        summary = _retry_exception_summary(Exception('oops.'))
        self.assertEqual(summary, 'oops')

    def test_empty_message_returns_class_name(self):
        exc = Exception('')
        summary = _retry_exception_summary(exc)
        self.assertEqual(summary, 'Exception')


# ---------------------------------------------------------------------------
# run_with_retry
# ---------------------------------------------------------------------------


class RunWithRetrySuccessTests(unittest.TestCase):
    def test_returns_response_on_first_attempt(self):
        response = _mock_response(200)
        result = run_with_retry(lambda: response, max_retries=3)
        self.assertIs(result, response)

    def test_returns_response_on_second_attempt_after_transient_error(self):
        response = _mock_response(200)
        calls = [0]

        def op():
            calls[0] += 1
            if calls[0] == 1:
                raise type('ReadTimeout', (Exception,), {})('timeout')
            return response

        with patch('provider_client_base.provider_client_base.helpers.retry_utils.time.sleep'):
            result = run_with_retry(op, max_retries=3)
        self.assertIs(result, response)
        self.assertEqual(calls[0], 2)

    def test_retries_on_retryable_status_code(self):
        good = _mock_response(200)
        bad = _mock_response(503)
        calls = [0]

        def op():
            calls[0] += 1
            return bad if calls[0] == 1 else good

        with patch('provider_client_base.provider_client_base.helpers.retry_utils.time.sleep'):
            with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', return_value=0.1):
                result = run_with_retry(op, max_retries=3)
        self.assertIs(result, good)

    def test_retries_up_to_max_on_retryable_status(self):
        bad = _mock_response(503)
        calls = [0]

        def op():
            calls[0] += 1
            return bad

        with patch('provider_client_base.provider_client_base.helpers.retry_utils.time.sleep'):
            with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', return_value=0.1):
                result = run_with_retry(op, max_retries=3)
        self.assertIs(result, bad)
        self.assertEqual(calls[0], 3)

    def test_raises_non_retryable_exception_immediately(self):
        calls = [0]

        def op():
            calls[0] += 1
            raise ValueError('bad value')

        with self.assertRaises(ValueError):
            run_with_retry(op, max_retries=3)
        self.assertEqual(calls[0], 1)

    def test_raises_retryable_exception_after_max_retries(self):
        def op():
            raise type('ReadTimeout', (Exception,), {})('timeout')

        with patch('provider_client_base.provider_client_base.helpers.retry_utils.time.sleep'):
            with self.assertRaises(Exception):
                run_with_retry(op, max_retries=2)

    def test_respects_retry_after_header_on_429(self):
        good = _mock_response(200)
        bad = _mock_response(429, headers={'Retry-After': '5'})
        calls = [0]

        def op():
            calls[0] += 1
            return bad if calls[0] == 1 else good

        # Pin the jitter so the assertion is deterministic — real
        # ``run_with_retry`` adds 0-to-25%-of-base jitter on top of
        # the server's ``Retry-After`` (capped at 10s) so parallel
        # clients don't all wake at the same instant. Force the
        # jitter to its upper bound here so the test is exact.
        with patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.time.sleep',
        ) as mock_sleep, patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.random.uniform',
            side_effect=lambda lo, hi: hi,
        ):
            result = run_with_retry(op, max_retries=3)
        # ``Retry-After: 5`` + jitter cap of min(5 * 0.25, 10) = 1.25.
        mock_sleep.assert_called_once_with(5.0 + 1.25)
        self.assertIs(result, good)

    def test_max_retries_one_returns_first_response(self):
        response = _mock_response(503)
        with patch('provider_client_base.provider_client_base.helpers.retry_utils.time.sleep'):
            result = run_with_retry(lambda: response, max_retries=1)
        self.assertIs(result, response)

    def test_operation_name_used_in_log(self):
        bad = _mock_response(503)
        good = _mock_response(200)
        calls = [0]

        def op():
            calls[0] += 1
            return bad if calls[0] == 1 else good

        with patch('provider_client_base.provider_client_base.helpers.retry_utils.time.sleep'):
            with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', return_value=0.1):
                with patch('provider_client_base.provider_client_base.helpers.retry_utils.logger') as mock_logger:
                    run_with_retry(op, max_retries=3, operation_name='MyClient GET https://example.com')
        mock_logger.warning.assert_called_once()
        log_msg = mock_logger.warning.call_args[0][0]
        self.assertIn('%s', log_msg)


# ---------------------------------------------------------------------------
# Flow tests
# ---------------------------------------------------------------------------


class RunWithRetryFlowTests(unittest.TestCase):
    def test_successful_first_attempt_no_sleep(self):
        response = _mock_response(200)
        with patch('provider_client_base.provider_client_base.helpers.retry_utils.time.sleep') as mock_sleep:
            run_with_retry(lambda: response, max_retries=3)
        mock_sleep.assert_not_called()

    def test_exception_then_success_sleeps_once(self):
        good = _mock_response(200)
        calls = [0]

        def op():
            calls[0] += 1
            if calls[0] == 1:
                raise type('ConnectionError', (Exception,), {})('down')
            return good

        with patch('provider_client_base.provider_client_base.helpers.retry_utils.time.sleep') as mock_sleep:
            with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', return_value=1.0):
                result = run_with_retry(op, max_retries=3)
        self.assertIs(result, good)
        mock_sleep.assert_called_once()

    def test_all_retries_exhausted_returns_last_retryable_response(self):
        bad = _mock_response(500)

        with patch('provider_client_base.provider_client_base.helpers.retry_utils.time.sleep'):
            with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', return_value=0.0):
                result = run_with_retry(lambda: bad, max_retries=3)
        self.assertIs(result, bad)

    def test_full_retry_with_retry_after_header(self):
        headers = {'Retry-After': '10'}
        bad = _mock_response(429, headers=headers)
        good = _mock_response(200)
        calls = [0]

        def op():
            calls[0] += 1
            if calls[0] < 3:
                return bad
            return good

        # ``run_with_retry`` adds bounded jitter on top of the server's
        # ``Retry-After`` so parallel clients don't all wake at the
        # same instant. Force the jitter mock to its lower bound (0)
        # so each sleep call is exactly ``Retry-After`` seconds.
        with patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.time.sleep',
        ) as mock_sleep, patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.random.uniform',
            side_effect=lambda lo, hi: lo,
        ):
            result = run_with_retry(op, max_retries=3)
        self.assertIs(result, good)
        self.assertEqual(mock_sleep.call_count, 2)
        for call in mock_sleep.call_args_list:
            self.assertEqual(call[0][0], 10.0)
