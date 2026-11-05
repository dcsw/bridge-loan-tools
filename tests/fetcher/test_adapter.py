"""Retry/backoff and polite rate limiting around any Fetcher."""

from __future__ import annotations

import pytest

from backend.fetcher.adapter import (
    FetchResult,
    Outcome,
    RateLimiter,
    RetryPolicy,
    fetch_politely,
)

URL = "https://portal.example.test/a.pdf"


def res(outcome, **kw):
    return FetchResult(url=URL, outcome=outcome, **kw)


class Scripted:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def fetch(self, url):
        self.calls += 1
        return self.results.pop(0)


def no_sleep(seconds):
    return None


def test_ok_returns_immediately_without_sleep():
    sleeps = []
    f = Scripted([res(Outcome.OK, body=b"%PDF-")])
    out = fetch_politely(f, URL, policy=RetryPolicy(), sleep=sleeps.append)
    assert out.outcome is Outcome.OK and f.calls == 1 and sleeps == []


@pytest.mark.parametrize(
    "terminal",
    [Outcome.CAPTCHA, Outcome.PAID, Outcome.BLOCKED_URL, Outcome.NOT_PDF],
)
def test_terminal_outcomes_are_not_retried(terminal):
    f = Scripted([res(terminal)])
    assert fetch_politely(f, URL, policy=RetryPolicy(), sleep=no_sleep).outcome is terminal
    assert f.calls == 1


def test_client_http_error_not_retried_but_5xx_is():
    f = Scripted([res(Outcome.HTTP_ERROR, status=404)])
    fetch_politely(f, URL, policy=RetryPolicy(), sleep=no_sleep)
    assert f.calls == 1
    f = Scripted([res(Outcome.HTTP_ERROR, status=503), res(Outcome.OK, body=b"%PDF-")])
    out = fetch_politely(f, URL, policy=RetryPolicy(), sleep=no_sleep)
    assert out.outcome is Outcome.OK and f.calls == 2


def test_exponential_backoff_is_capped_and_attempts_bounded():
    sleeps = []
    f = Scripted([res(Outcome.TIMEOUT)] * 5)
    policy = RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=3.0)
    out = fetch_politely(f, URL, policy=policy, sleep=sleeps.append)
    assert out.outcome is Outcome.TIMEOUT
    assert f.calls == 5
    assert sleeps == [1.0, 2.0, 3.0, 3.0]


def test_retry_after_honoured_but_capped():
    sleeps = []
    f = Scripted(
        [
            res(Outcome.RATE_LIMITED, status=429, retry_after=2.0),
            res(Outcome.RATE_LIMITED, status=429, retry_after=9999.0),
            res(Outcome.OK, body=b"%PDF-"),
        ]
    )
    policy = RetryPolicy(max_attempts=3, base_delay=0.1, max_delay=10.0)
    out = fetch_politely(f, URL, policy=policy, sleep=sleeps.append)
    assert out.outcome is Outcome.OK
    assert sleeps == [2.0, 10.0]


def test_rate_limited_forever_returns_rate_limited_after_cap():
    f = Scripted([res(Outcome.RATE_LIMITED, status=429)] * 3)
    out = fetch_politely(f, URL, policy=RetryPolicy(max_attempts=3), sleep=no_sleep)
    assert out.outcome is Outcome.RATE_LIMITED and f.calls == 3


def test_policy_rejects_nonsense():
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay=-1)


def test_rate_limiter_spaces_requests():
    now = [100.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    limiter = RateLimiter(min_interval=2.0, clock=lambda: now[0], sleep=sleep)
    limiter.wait()  # first request: no wait
    now[0] += 0.5
    limiter.wait()
    now[0] += 5.0
    limiter.wait()
    assert sleeps == [1.5]


def test_fetch_politely_calls_limiter_before_each_attempt():
    waits = []

    class Limiter:
        def wait(self):
            waits.append(1)

    f = Scripted([res(Outcome.TIMEOUT), res(Outcome.OK, body=b"%PDF-")])
    fetch_politely(f, URL, policy=RetryPolicy(), limiter=Limiter(), sleep=no_sleep)
    assert len(waits) == 2


def test_rate_limiter_rejects_negative_interval():
    with pytest.raises(ValueError):
        RateLimiter(min_interval=-1)
