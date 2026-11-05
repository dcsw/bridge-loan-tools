"""Playwright adapter against the local fake portal (no live portal contact)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.fetcher.adapter import Fetcher, Outcome, RetryPolicy, fetch_politely
from backend.fetcher.manifest import fetch_to_drop
from backend.fetcher.playwright import PlaywrightFetcher

PDFS = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"


@pytest.fixture
def fetcher(portal_allowlist):
    with PlaywrightFetcher(portal_allowlist, timeout_s=1.5) as f:
        yield f


def test_is_a_fetcher(fetcher):
    assert isinstance(fetcher, Fetcher)


@pytest.mark.parametrize("name", ["mortgage", "deed", "discharge", "multi_instrument"])
def test_fetches_fixture_pdfs_byte_for_byte(fetcher, portal, name):
    res = fetcher.fetch(portal.url(f"/pdfs/{name}.pdf"))
    assert res.outcome is Outcome.OK
    assert res.body == (PDFS / f"{name}.pdf").read_bytes()
    assert res.status == 200


def test_follows_allowed_redirect(fetcher, portal):
    res = fetcher.fetch(portal.url("/redirect/pdf"))
    assert res.outcome is Outcome.OK and res.body.startswith(b"%PDF")


@pytest.mark.parametrize("path", ["/redirect/internal", "/redirect/loopback-other"])
def test_redirect_to_internal_address_is_blocked(fetcher, portal, path):
    res = fetcher.fetch(portal.url(path))
    assert res.outcome is Outcome.BLOCKED_URL
    assert portal.hits.get("/latest/meta-data/", 0) == 0


def test_redirect_loop_is_capped(portal_allowlist, portal):
    with PlaywrightFetcher(portal_allowlist, timeout_s=1.5, max_redirects=3) as f:
        res = f.fetch(portal.url("/redirect/loop"))
    assert res.outcome is Outcome.HTTP_ERROR
    assert portal.hits["/redirect/loop"] == 4


def test_url_outside_allowlist_is_blocked_without_request(fetcher):
    assert fetcher.fetch("http://169.254.169.254/latest/meta-data/").outcome is Outcome.BLOCKED_URL
    assert fetcher.fetch("https://evil.example.org/x.pdf").outcome is Outcome.BLOCKED_URL


def test_captcha_page_detected(fetcher, portal):
    assert fetcher.fetch(portal.url("/captcha")).outcome is Outcome.CAPTCHA


def test_timeout_detected(fetcher, portal):
    assert fetcher.fetch(portal.url("/slow?delay=5")).outcome is Outcome.TIMEOUT


def test_429_parses_retry_after(fetcher, portal):
    res = fetcher.fetch(portal.url("/ratelimit?n=99&retry_after=7"))
    assert (res.outcome, res.status, res.retry_after) == (Outcome.RATE_LIMITED, 429, 7.0)


def test_429_then_success_via_politeness_wrapper(fetcher, portal):
    sleeps = []
    res = fetch_politely(
        fetcher,
        portal.url("/ratelimit?n=2&retry_after=0"),
        policy=RetryPolicy(max_attempts=3, base_delay=0.0),
        sleep=sleeps.append,
    )
    assert res.outcome is Outcome.OK and len(sleeps) == 2


def test_paid_forbidden_error_and_non_pdf(fetcher, portal):
    assert fetcher.fetch(portal.url("/paid")).outcome is Outcome.PAID
    forbidden = fetcher.fetch(portal.url("/forbidden"))
    assert (forbidden.outcome, forbidden.status) == (Outcome.HTTP_ERROR, 403)
    assert fetcher.fetch(portal.url("/error")).status == 500
    assert fetcher.fetch(portal.url("/notpdf")).outcome is Outcome.NOT_PDF


def test_oversize_body_is_rejected(portal_allowlist, portal):
    with PlaywrightFetcher(portal_allowlist, timeout_s=5, max_bytes=100_000) as f:
        res = f.fetch(portal.url("/pdfs/oversize.pdf"))
    assert res.outcome is Outcome.HTTP_ERROR and "max_bytes" in res.detail


def test_connection_refused_is_a_failed_fetch_not_a_crash(portal_allowlist):
    with PlaywrightFetcher(portal_allowlist, timeout_s=1.5) as f:
        res = f.fetch("http://127.0.0.1:9/never.pdf")
    assert res.outcome is Outcome.HTTP_ERROR and res.status is None


def test_fetch_before_start_raises(portal_allowlist):
    with pytest.raises(RuntimeError, match="not started"):
        PlaywrightFetcher(portal_allowlist).fetch("http://127.0.0.1/x")


def test_end_to_end_mixed_run_writes_rows_with_gap_reasons_and_never_crashes(
    fetcher, portal, tmp_path
):
    urls = [
        portal.url("/pdfs/mortgage.pdf"),
        portal.url("/captcha"),
        portal.url("/slow?delay=5"),
        portal.url("/ratelimit?n=99&retry_after=0"),
        portal.url("/paid"),
        portal.url("/redirect/internal"),
    ]
    rows = fetch_to_drop(
        fetcher,
        urls,
        town="mattapoisett",
        drop_dir=tmp_path,
        policy=RetryPolicy(max_attempts=2, base_delay=0.0),
        sleep=lambda s: None,
        now=lambda: datetime(2026, 11, 5, tzinfo=UTC),
    )
    assert [(r.status, r.gap_reason) for r in rows] == [
        ("fetched", None),
        ("gap", "blocked"),
        ("gap", "failed"),
        ("gap", "blocked"),
        ("gap", "paid"),
        ("gap", "blocked"),
    ]
    sha = hashlib.sha256((PDFS / "mortgage.pdf").read_bytes()).hexdigest()
    assert (tmp_path / f"{sha}.pdf").exists()
