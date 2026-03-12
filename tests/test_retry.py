import unittest
from unittest.mock import Mock, patch

import bootstrap  # noqa: F401

from openhands_agent.client.retry_utils import (
    _retry_delay_seconds,
    is_retryable_exception,
    is_retryable_response,
    run_with_retry,
)


class ConnectTimeout(Exception):
    pass


class PermanentFailure(Exception):
    pass


class RetryTests(unittest.TestCase):
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

        with patch('openhands_agent.client.retry_utils.time.sleep') as mock_sleep:
            result = run_with_retry(operation, 2)

        self.assertEqual(result, 'ok')
        mock_sleep.assert_called_once_with(1.0)

    def test_retry_delay_uses_retry_after_header_for_rate_limits(self) -> None:
        response = type(
            'Response',
            (),
            {'status_code': 429, 'headers': {'Retry-After': '3'}},
        )()

        self.assertEqual(_retry_delay_seconds(0, response), 3.0)
