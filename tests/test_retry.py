import unittest
from unittest.mock import Mock, patch


from kato.helpers.retry_utils import (
    _retry_delay_seconds,
    is_retryable_exception,
    is_retryable_response,
    retry_count,
    run_with_retry,
)


class ConnectTimeout(Exception):
    pass


class PermanentFailure(Exception):
    pass


class RetryTests(unittest.TestCase):
    def test_retry_count_enforces_minimum_one(self) -> None:
        self.assertEqual(retry_count(5), 5)
        self.assertEqual(retry_count(0), 1)
        self.assertEqual(retry_count(-3), 1)

    def test_retry_count_falls_back_to_default_for_invalid_values(self) -> None:
        self.assertEqual(retry_count('bad', default=4), 4)
        self.assertEqual(retry_count(None, default=0), 1)

    def test_is_retryable_exception_accepts_known_timeout_names(self) -> None:
        self.assertTrue(is_retryable_exception(ConnectTimeout('timeout')))
        self.assertTrue(is_retryable_exception(TimeoutError('timeout')))

    def test_is_retryable_exception_rejects_non_transient_errors(self) -> None:
        self.assertFalse(is_retryable_exception(PermanentFailure('bad request')))
        self.assertFalse(is_retryable_exception(ValueError('bad value')))

    def test_is_retryable_response_accepts_transient_status_codes(self) -> None:
        self.assertTrue(is_retryable_response(type('Response', (), {'status_code': 503})()))
        self.assertTrue(is_retryable_response(type('Response', (), {'status_code': 429})()))

    def test_is_retryable_response_rejects_non_transient_status_codes(self) -> None:
        self.assertFalse(is_retryable_response(type('Response', (), {'status_code': 400})()))
        self.assertFalse(is_retryable_response(type('Response', (), {})()))

    def test_run_with_retry_sleeps_before_retrying_exceptions(self) -> None:
        operation = Mock(side_effect=[ConnectTimeout('timeout'), 'ok'])

        with patch('kato.helpers.retry_utils.random.uniform', return_value=1.5) as mock_uniform, patch(
            'kato.helpers.retry_utils.time.sleep'
        ) as mock_sleep:
            result = run_with_retry(operation, 2)

        self.assertEqual(result, 'ok')
        mock_uniform.assert_called_once_with(1.0, 2.0)
        mock_sleep.assert_called_once_with(1.5)

    def test_run_with_retry_raises_after_exhausting_all_retries(self) -> None:
        operation = Mock(side_effect=ConnectTimeout('always fails'))

        with patch('kato.helpers.retry_utils.time.sleep') as mock_sleep:
            with self.assertRaises(ConnectTimeout):
                run_with_retry(operation, 3)

        self.assertEqual(operation.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_retry_delay_uses_retry_after_header_for_rate_limits(self) -> None:
        response = type(
            'Response',
            (),
            {'status_code': 429, 'headers': {'Retry-After': '3'}},
        )()

        with patch('kato.helpers.retry_utils.random.uniform') as mock_uniform:
            self.assertEqual(_retry_delay_seconds(0, response), 3.0)

        mock_uniform.assert_not_called()

    def test_retry_delay_falls_back_to_exponential_backoff_without_retry_after(self) -> None:
        response = type(
            'Response',
            (),
            {'status_code': 429, 'headers': {}},
        )()

        with patch('kato.helpers.retry_utils.random.uniform', return_value=5.5) as mock_uniform:
            self.assertEqual(_retry_delay_seconds(2, response), 5.5)

        mock_uniform.assert_called_once_with(4.0, 8.0)

    def test_retry_delay_falls_back_to_exponential_backoff_for_invalid_retry_after(self) -> None:
        response = type(
            'Response',
            (),
            {'status_code': 429, 'headers': {'Retry-After': 'abc'}},
        )()

        with patch('kato.helpers.retry_utils.random.uniform', return_value=3.25) as mock_uniform:
            self.assertEqual(_retry_delay_seconds(1, response), 3.25)

        mock_uniform.assert_called_once_with(2.0, 4.0)

    def test_retry_delay_falls_back_to_exponential_backoff_for_non_rate_limited_response(self) -> None:
        response = type(
            'Response',
            (),
            {'status_code': 503, 'headers': {'Retry-After': '3'}},
        )()

        with patch('kato.helpers.retry_utils.random.uniform', return_value=1.75) as mock_uniform:
            self.assertEqual(_retry_delay_seconds(0, response), 1.75)

        mock_uniform.assert_called_once_with(1.0, 2.0)
