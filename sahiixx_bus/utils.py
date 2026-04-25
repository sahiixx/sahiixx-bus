"""Utility helpers for sahiixx-bus."""

from __future__ import annotations

import asyncio
import json
import random
import re
import string
import time
import unicodedata
from typing import Any, Awaitable, Callable


async def async_retry(
    coro: Callable[[], Awaitable[Any]],
    retries: int = 3,
    backoff: float = 1.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Any:
    """Retry an async callable with exponential backoff.

    Args:
        coro: Async callable with no arguments.
        retries: Maximum number of attempts.
        backoff: Initial backoff in seconds.
        exceptions: Tuple of exception types to catch and retry.

    Returns:
        Result of ``coro``.

    Raises:
        The last encountered exception if all retries fail.
    """
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return await coro()
        except exceptions as exc:
            last_exc = exc
            if attempt < retries - 1:
                await asyncio.sleep(backoff * (2 ** attempt) + random.random())
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Retry loop exited without result or exception")


def sanitize_input(raw: str, max_length: int = 10000) -> str:
    """Strip control characters, collapse whitespace, and enforce length.

    Args:
        raw: Raw user or agent input.
        max_length: Maximum allowed length after truncation.

    Returns:
        Cleaned string.
    """
    # Remove control chars except common whitespace
    cleaned = "".join(
        ch for ch in raw if unicodedata.category(ch)[0] != "C" or ch in ("\n", "\r", "\t")
    )
    # Collapse multiple whitespace to single space
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:max_length].strip()


def generate_id(prefix: str = "sxx") -> str:
    """Generate a short random identifier.

    Args:
        prefix: Identifier prefix.

    Returns:
        String like ``sxx-aB3dEfGh``.
    """
    suffix = "".join(random.choices(string.ascii_letters + string.digits, k=8))
    return f"{prefix}-{suffix}"


def now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def safe_json_loads(raw: str) -> dict:
    """Safely parse JSON with fallback to empty dict.

    Args:
        raw: JSON string.

    Returns:
        Parsed dict, or empty dict on failure.
    """
    try:
        return json.loads(raw)
    except Exception:
        return {}
