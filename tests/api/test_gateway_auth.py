"""Backend accepts only gateway traffic: shared secret (constant-time) + forwarded key id."""

from __future__ import annotations

import pytest

from tests.api.conftest import GATEWAY_SECRET, KEY_ID

DATA_ROUTES = ["/loans", "/loans/abc", "/loans/abc/source", "/coverage"]


@pytest.fixture
def client(snapshot_dir, make_client):
    return make_client(snapshot_dir, auth=False)


@pytest.mark.parametrize("path", DATA_ROUTES)
def test_direct_call_without_secret_rejected(client, path):
    r = client.get(path, headers={"X-Key-Id": KEY_ID})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "gateway_auth_required"


@pytest.mark.parametrize("path", DATA_ROUTES)
def test_wrong_secret_rejected(client, path):
    r = client.get(path, headers={"X-Gateway-Secret": "wrong", "X-Key-Id": KEY_ID})
    assert r.status_code == 401


def test_missing_key_id_rejected_even_with_secret(client):
    r = client.get("/loans", headers={"X-Gateway-Secret": GATEWAY_SECRET})
    assert r.status_code == 401
    assert r.json()["code"] == "key_id_required"


@pytest.mark.parametrize("bad", [b"", b"a b", b"x" * 65, b"k;rm", b"\xd0\xba\xd0\xbb"])
def test_malformed_key_id_rejected(client, bad):
    r = client.get("/loans", headers={"X-Gateway-Secret": GATEWAY_SECRET, "X-Key-Id": bad})
    assert r.status_code == 401


def test_valid_gateway_headers_accepted(client):
    r = client.get("/loans", headers={"X-Gateway-Secret": GATEWAY_SECRET, "X-Key-Id": KEY_ID})
    assert r.status_code == 200


def test_secret_comparison_is_constant_time(monkeypatch, client):
    import hmac

    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr("backend.api.auth.hmac.compare_digest", spy)
    client.get("/loans", headers={"X-Gateway-Secret": "nope", "X-Key-Id": KEY_ID})
    assert calls and calls[0][1] == GATEWAY_SECRET.encode()


def test_non_ascii_secret_does_not_crash(client):
    r = client.get(
        "/loans",
        headers={"X-Gateway-Secret": b"s\xc3\xa9cret", "X-Key-Id": KEY_ID},
    )
    assert r.status_code == 401


def test_unauthenticated_response_leaks_nothing(client):
    r = client.get("/loans")
    assert "secret" not in r.text.lower().replace("gateway_auth_required", "")
    assert GATEWAY_SECRET not in r.text


def test_openapi_and_docs_routes_not_served(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, headers={"X-Gateway-Secret": GATEWAY_SECRET}).status_code == 404


def test_settings_reject_weak_secret(snapshot_dir):
    from backend.api.config import Settings

    with pytest.raises(ValueError, match="gateway_secret"):
        Settings(gateway_secret="short", snapshot_base=str(snapshot_dir))
