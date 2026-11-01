"""Shared bounded retry policy helpers."""

import email.utils
import random
from datetime import datetime, timezone
from typing import Mapping

MAX_EXTERNAL_RETRIES = 2
BASE_RETRY_DELAY_SECONDS = 0.5
MAX_RETRY_DELAY_SECONDS = 5.0


def is_transient_status_code(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or (
        500 <= status_code < 600 and status_code not in {501, 505}
    )


def retry_delay_seconds(
    retry_index: int,
    headers: Mapping[str, str] | None = None,
) -> float | None:
    """Return a bounded jittered delay, or None for an excessive Retry-After."""
    exponential_cap = min(
        BASE_RETRY_DELAY_SECONDS * 2**retry_index,
        MAX_RETRY_DELAY_SECONDS,
    )
    delay = random.uniform(0.0, exponential_cap)

    if headers is None:
        return delay

    retry_after = _retry_after_seconds(headers)
    if retry_after is None:
        return delay
    if retry_after > MAX_RETRY_DELAY_SECONDS:
        return None
    return max(delay, retry_after)


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    retry_after_ms = _get_header(headers, "retry-after-ms")
    if retry_after_ms is not None:
        try:
            return max(0.0, float(retry_after_ms) / 1000)
        except ValueError:
            pass

    retry_after = _get_header(headers, "retry-after")
    if retry_after is None:
        return None

    try:
        return max(0.0, float(retry_after))
    except ValueError:
        pass

    try:
        retry_at = email.utils.parsedate_to_datetime(retry_after)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0.0, (retry_at - now).total_seconds())


def _get_header(headers: Mapping[str, str], name: str) -> str | None:
    lowered_name = name.lower()
    return next(
        (value for key, value in headers.items() if key.lower() == lowered_name),
        None,
    )
