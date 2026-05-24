from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

logger = logging.getLogger('retry_utils')

TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}
TRANSIENT_EXCEPTION_NAMES = {
    'ConnectionError',
    'ConnectTimeout',
    'ReadTimeout',
    'Timeout',
    'TimeoutError',
}


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


# 429 (rate-limit) responses need a MUCH longer backoff than 5xx
# blips. A 5xx is usually a momentary node failure that recovers in
# seconds; a 429 means we hit a quota window measured in minutes
# (Bitbucket, GitHub, GitLab all use 60-3600s windows). With the
# 1/2/4s exponential the 5xx path uses, three retries totalled ~7s
# of waiting and never escaped the window — the operator-reported
# "PR lookup failed" errors. This schedule is sized for real
# provider quota windows.
_RATE_LIMIT_BASE_DELAY_SECONDS = 15.0
_RATE_LIMIT_MAX_DELAY_SECONDS = 120.0
# Add up to ~25% jitter (capped at 10s) on top of any wait. When
# several parallel clients all hit 429 at once and the server tells
# every one of them "wait 60s", they would otherwise all wake at the
# same instant and immediately re-hit the limit. The jitter spreads
# the herd across a small window so the recovery sticks.
_JITTER_FRACTION = 0.25
_JITTER_MAX_SECONDS = 10.0


def _retry_delay_seconds(attempt: int, response: object | None = None) -> float:
    retry_after = _retry_after_seconds(response)
    if retry_after is not None:
        return retry_after + _bounded_jitter(retry_after)
    if getattr(response, 'status_code', None) == 429:
        # 429 without a Retry-After hint — pick a sensible default
        # for "wait long enough that the quota window can clear".
        base_delay = min(
            _RATE_LIMIT_BASE_DELAY_SECONDS * (2 ** attempt),
            _RATE_LIMIT_MAX_DELAY_SECONDS,
        )
        return random.uniform(base_delay, base_delay * 1.5)
    base_delay = float(2 ** attempt)
    return random.uniform(base_delay, base_delay * 2.0)


def _bounded_jitter(base_seconds: float) -> float:
    upper = min(base_seconds * _JITTER_FRACTION, _JITTER_MAX_SECONDS)
    if upper <= 0:
        return 0.0
    return random.uniform(0.0, upper)


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
