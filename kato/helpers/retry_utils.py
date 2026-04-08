import logging
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}
TRANSIENT_EXCEPTION_NAMES = {
    'ConnectionError',
    'ConnectTimeout',
    'ReadTimeout',
    'Timeout',
    'TimeoutError',
}

logger = logging.getLogger(__name__)


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


def run_with_retry(operation, max_retries: int):
    last_response = None
    last_attempt = max_retries - 1

    for attempt in range(max_retries):
        try:
            response = operation()
        except Exception as exc:
            if attempt == last_attempt or not is_retryable_exception(exc):
                raise
            logger.warning(
                'retrying after transient exception on attempt %s/%s: %s',
                attempt + 1,
                max_retries,
                exc,
            )
            time.sleep(_retry_delay_seconds(attempt))
            continue

        last_response = response
        if attempt < last_attempt and is_retryable_response(response):
            logger.warning(
                'retrying after transient response on attempt %s/%s with status %s',
                attempt + 1,
                max_retries,
                getattr(response, 'status_code', 'unknown'),
            )
            time.sleep(_retry_delay_seconds(attempt, response))
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
