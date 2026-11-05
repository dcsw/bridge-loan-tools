"""The fake portal itself: routes behave as the fetcher tests assume."""

from __future__ import annotations

import requests

TIMEOUT = 3


def test_serves_fixture_pdf(portal):
    r = requests.get(portal.url("/pdfs/deed.pdf"), timeout=TIMEOUT)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert r.headers["Content-Type"] == "application/pdf"


def test_unknown_pdf_is_404(portal):
    assert requests.get(portal.url("/pdfs/nope.pdf"), timeout=TIMEOUT).status_code == 404
    assert requests.get(portal.url("/pdfs/../x.pdf"), timeout=TIMEOUT).status_code == 404
    assert requests.get(portal.url("/elsewhere"), timeout=TIMEOUT).status_code == 404


def test_captcha_paid_forbidden_error_notpdf(portal):
    assert "captcha" in requests.get(portal.url("/captcha"), timeout=TIMEOUT).text.lower()
    assert requests.get(portal.url("/paid"), timeout=TIMEOUT).status_code == 402
    assert requests.get(portal.url("/forbidden"), timeout=TIMEOUT).status_code == 403
    assert requests.get(portal.url("/error"), timeout=TIMEOUT).status_code == 500
    assert (
        requests.get(portal.url("/notpdf"), timeout=TIMEOUT)
        .headers["Content-Type"]
        .startswith("text/html")
    )


def test_ratelimit_counts_down_then_serves(portal):
    codes = [
        requests.get(portal.url("/ratelimit?n=2&retry_after=3"), timeout=TIMEOUT) for _ in range(3)
    ]
    assert [c.status_code for c in codes] == [429, 429, 200]
    assert codes[0].headers["Retry-After"] == "3"


def test_redirects(portal):
    r = requests.get(portal.url("/redirect/internal"), allow_redirects=False, timeout=TIMEOUT)
    assert r.status_code == 302 and "169.254.169.254" in r.headers["Location"]
    r = requests.get(portal.url("/redirect/pdf"), allow_redirects=False, timeout=TIMEOUT)
    assert r.headers["Location"] == "/pdfs/mortgage.pdf"


def test_slow_route_waits_and_stops_promptly(portal):
    import time

    start = time.monotonic()
    try:
        requests.get(portal.url("/slow?delay=30"), timeout=0.3)
    except requests.exceptions.Timeout:
        pass
    assert time.monotonic() - start < 2


def test_close_is_idempotent():
    from tests.fakeportal.server import FakePortal

    p = FakePortal().start()
    p.close()
    p.close()
