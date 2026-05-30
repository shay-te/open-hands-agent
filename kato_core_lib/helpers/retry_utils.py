from __future__ import annotations

import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from kato_core_lib.helpers.logging_utils import configure_logger

TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}
TRANSIENT_EXCEPTION_NAMES = {
    'ConnectionError',
    'ConnectTimeout',
    'ReadTimeout',
    'Timeout',
    'TimeoutError',
}

logger = configure_logger('retry_utils')


def retry_count(value: object, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(default))


def is_retryable_exception(exc: Exception) -> bool:
    return exc.__class__.__name__ in TRANSIENT_EXCEPTION_NAMES or isinstance(
        exc,
        (ConnectionError, TimeoutError),
    )


def is_retryable_response(response: object) -> bool:
    return getattr(response, 'status_code', None) in TRANSIENT_STATUS_CODES


def run_with_retry(operation, max_retries: int, *, operation_name: str = 'request'):
    last_response = None
    last_attempt = max_retries - 1

    for attempt in range(max_retries):
        try:
            response = operation()
        except Exception as exc:
            if attempt == last_attempt or not is_retryable_exception(exc):
                raise
            retry_delay_seconds = _retry_delay_seconds(attempt)
            service_name, method, url = _operation_details(operation_name)
            logger.warning(
                '%s connection failed; retrying in %.1fs (attempt %s/%s).\n'
                '%s (%s %s).',
                service_name,
                retry_delay_seconds,
                attempt + 2,
                max_retries,
                _retry_exception_summary(exc),
                method,
                url,
            )
            time.sleep(retry_delay_seconds)
            continue

        last_response = response
        if attempt < last_attempt and is_retryable_response(response):
            retry_delay_seconds = _retry_delay_seconds(attempt, response)
            service_name, method, url = _operation_details(operation_name)
            logger.warning(
                '%s request returned status %s; retrying in %.1fs (attempt %s/%s).\n'
                'Received retryable response from %s %s.',
                service_name,
                getattr(response, 'status_code', 'unknown'),
                retry_delay_seconds,
                attempt + 2,
                max_retries,
                method,
                url,
            )
            time.sleep(retry_delay_seconds)
            continue
        return response

    return last_response


def _retry_delay_seconds(attempt: int, response: object | None = None) -> float:
    retry_after = _retry_after_seconds(response)
    if retry_after is not None:
        return retry_after
    base_delay = float(2 ** attempt)
    return random.uniform(base_delay, base_delay * 2.0)


def _retry_after_seconds(response: object | None) -> float | None:
    if getattr(response, 'status_code', None) != 429:
        return None
    headers = getattr(response, 'headers', None)
    if not hasattr(headers, 'get'):
        return None
    retry_after = headers.get('Retry-After')
    if retry_after is None:
        return None

    retry_after_text = str(retry_after).strip()
    if not retry_after_text:
        return None
    try:
        return max(0.0, float(retry_after_text))
    except ValueError:
        pass

    try:
        retry_after_time = parsedate_to_datetime(retry_after_text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if retry_after_time.tzinfo is None:
        retry_after_time = retry_after_time.replace(tzinfo=timezone.utc)
    return max(0.0, (retry_after_time - datetime.now(timezone.utc)).total_seconds())


def _operation_details(operation_name: str) -> tuple[str, str, str]:
    match = re.match(r'^(\S+)\s+([A-Z]+)\s+(\S+)$', str(operation_name or '').strip())
    if not match:
        return ('Request', 'request', str(operation_name or 'request').strip() or 'request')
    client_name, method, url = match.groups()
    return (_service_name_from_client_name(client_name), method, url)


def _service_name_from_client_name(client_name: str) -> str:
    normalized_name = str(client_name or '').strip()
    if normalized_name.endswith('Client'):
        normalized_name = normalized_name[:-6]
    return normalized_name or 'Request'


def _retry_exception_summary(exc: Exception) -> str:
    error_text = str(exc)
    if (
        'Remote end closed connection without response' in error_text
        or 'RemoteDisconnected' in error_text
        or 'Connection aborted' in error_text
    ):
        return 'Remote server closed connection'
    if 'Read timed out' in error_text or 'read timeout=' in error_text:
        return 'Request timed out'
    if 'ConnectTimeout' in error_text or 'connect timeout' in error_text.lower():
        return 'Connection timed out'
    if 'Name or service not known' in error_text or 'Temporary failure in name resolution' in error_text:
        return 'Could not resolve remote host'
    if error_text:
        return error_text.rstrip('.')
    return exc.__class__.__name__
