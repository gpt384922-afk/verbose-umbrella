from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class RetryError(Exception):
    """Raised when retry policy is exhausted."""


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_delay: float,
    max_delay: float,
    retryable: Callable[[Exception], bool],
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await func()
        except Exception as exc:  # noqa: BLE001 - delegated via predicate
            if not retryable(exc):
                raise
            last_error = exc
            if attempt >= attempts:
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            await asyncio.sleep(delay + random.uniform(0.0, 0.3))

    raise RetryError(str(last_error) if last_error else "retry exhausted") from last_error
