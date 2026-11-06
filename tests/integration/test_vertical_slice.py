"""WU11 DoD 1-2: clean state -> stack -> pipeline -> API via KrakenD -> UI; rerun is identical.

Slow and Docker-backed: opt in with ``make e2e`` (RUN_E2E=1). Uses ``.env.e2e`` and non-default
ports so a developer's own stack and ``.env`` are left alone.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="set RUN_E2E=1 (make e2e)")

import pipeline  # noqa: E402  (tests dirs share one import root)


@pytest.fixture(scope="module")
def slice_run():
    pipeline.clean()
    pipeline.stack_up()
    out = pipeline.pipeline()
    jwt = pipeline.token("e2e")
    return {"out": out, "jwt": jwt, "first": pipeline.stable_view(jwt)}


def test_core_stack_is_healthy(slice_run):
    ps = pipeline.run([*pipeline.COMPOSE, "ps", "--format", "{{.Service}} {{.Health}}"])
    health = dict(line.split(" ", 1) for line in ps.strip().splitlines())
    for service in ("minio", "postgres", "lakekeeper", "api", "krakend", "gateway"):
        assert health.get(service) == "healthy", health


def test_gateway_rejects_missing_token(slice_run):
    import urllib.error
    import urllib.request

    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f"http://127.0.0.1:{pipeline.GATEWAY_PORT}/loans", timeout=10)  # noqa: S310
    assert err.value.code == 401


def test_api_returns_fixture_loan_as_bridge_loan(slice_run):
    loans = slice_run["first"]["loans"]
    assert loans["total"] == 1 and loans["unmapped_count"] == 0
    props = loans["features"][0]["properties"]
    assert props["is_bridge_loan"] is True
    assert props["address_norm"] == "12525 OBRIEN SQUARES SUITE 947 MATTAPOISETT MA"
    assert props["geocode_status"] == "matched"
    assert props["classifier_version"] == "placeholder-all-true"
    assert loans["features"][0]["geometry"]["coordinates"] == [-70.8161, 41.6532]


def test_ui_shows_pin_and_table_row(slice_run):
    from playwright.sync_api import sync_playwright

    pipeline.web_up(pipeline.token("e2e-web"))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        problems: list[str] = []
        page.on("console", lambda m: problems.append(m.text) if m.type() == "error" else None)
        page.goto(f"http://127.0.0.1:{pipeline.WEB_PORT}/")
        page.wait_for_selector("button.pin", timeout=30_000)
        assert page.locator("button.pin").count() == 1
        assert "12525 OBRIEN SQUARES SUITE 947" in page.locator("button.pin").first.get_attribute(
            "aria-label"
        )
        row = page.locator("#loan-table tbody tr")
        row.first.wait_for(timeout=10_000)
        assert row.count() == 1
        assert "LEE PRODUCT CAPITAL LLC" in row.first.inner_text()
        browser.close()
    assert problems == []


def test_full_rerun_is_identical(slice_run):
    pipeline.pipeline()
    assert pipeline.stable_view(slice_run["jwt"]) == slice_run["first"]
