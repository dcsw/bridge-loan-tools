"""Fetcher interface plus polite retry/backoff and rate limiting shared by all adapters."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Outcome(StrEnum):
    OK = "ok"
    CAPTCHA = "captcha"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    PAID = "paid"
    HTTP_ERROR = "http_error"
    BLOCKED_URL = "blocked_url"
    NOT_PDF = "not_pdf"


@dataclass(frozen=True)
class FetchResult:
    url: str
    outcome: Outcome
    body: bytes = b""
    status: int | None = None
    retry_after: float | None = None
    detail: str = ""


class Fetcher(ABC):
    """Fetches one URL and reports an outcome; never raises for portal-side problems."""

    @abstractmethod
    def fetch(self, url: str) -> FetchResult: ...

    def start(self) -> None:
        """Acquire resources (browser, ...). Default: nothing."""
        return None

    def close(self) -> None:
        """Release resources. Default: nothing."""
        return None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must be >= 0")

    def delay_for(self, attempt: int, retry_after: float | None) -> float:
        """Seconds to wait after failed ``attempt`` (1-based); always capped at max_delay."""
        wanted = retry_after if retry_after is not None else self.base_delay * 2 ** (attempt - 1)
        return min(wanted, self.max_delay)


class Limiter(Protocol):
    def wait(self) -> None: ...


class RateLimiter:
    """Enforces a minimum interval between requests to the portal."""

    def __init__(
        self,
        min_interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval < 0:
            raise ValueError("min_interval must be >= 0")
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self.min_interval - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


def _retryable(result: FetchResult) -> bool:
    if result.outcome in (Outcome.RATE_LIMITED, Outcome.TIMEOUT):
        return True
    return result.outcome is Outcome.HTTP_ERROR and (result.status or 0) >= 500


def fetch_politely(
    fetcher: Fetcher,
    url: str,
    *,
    policy: RetryPolicy,
    limiter: Limiter | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchResult:
    """Fetch with rate limiting and capped retry/backoff; returns the last result."""
    result = FetchResult(url=url, outcome=Outcome.HTTP_ERROR, detail="no attempt made")
    for attempt in range(1, policy.max_attempts + 1):
        if limiter is not None:
            limiter.wait()
        result = fetcher.fetch(url)
        if not _retryable(result) or attempt == policy.max_attempts:
            return result
        sleep(policy.delay_for(attempt, result.retry_after))
    return result  # pragma: no cover - loop always returns on the last attempt
