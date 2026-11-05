"""RFC 7807 problem+json errors: 400/404/405/410/413/503 (+Retry-After)/500."""

from __future__ import annotations

import pytest


def _assert_problem(r, status, code):
    assert r.status_code == status
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["status"] == status
    assert body["code"] == code
    assert body["type"].startswith("https://") or body["type"].startswith("urn:")
    assert body["title"]
    assert isinstance(body["errors"], list)
    return body


def test_503_with_retry_after_when_snapshot_missing(tmp_path, make_client):
    client = make_client(tmp_path / "missing", retry_after_seconds=7)
    for path in ("/loans", "/loans/x", "/coverage"):
        r = client.get(path)
        _assert_problem(r, 503, "snapshot_unavailable")
        assert r.headers["retry-after"] == "7"


def test_ready_is_503_when_snapshot_missing_and_live_is_200(tmp_path, make_client):
    client = make_client(tmp_path / "missing")
    assert client.get("/health/live").status_code == 200
    r = client.get("/health/ready")
    _assert_problem(r, 503, "snapshot_unavailable")
    assert "retry-after" in r.headers


def test_validation_errors_carry_field_and_message(small_snapshot, make_client):
    snap, _ = small_snapshot
    client = make_client(snap)
    body = _assert_problem(client.get("/loans", params={"limit": "abc"}), 400, "invalid_parameters")
    err = body["errors"][0]
    assert err["field"] == "limit" and err["message"] and err["code"]


def test_unknown_route_is_404_problem(small_snapshot, make_client):
    snap, _ = small_snapshot
    _assert_problem(make_client(snap).get("/nope"), 404, "not_found")


def test_method_not_allowed_is_405_problem(small_snapshot, make_client):
    snap, _ = small_snapshot
    r = make_client(snap).post("/loans")
    _assert_problem(r, 405, "method_not_allowed")


def test_unhandled_exception_is_generic_500_without_leak(small_snapshot, make_client):
    snap, _ = small_snapshot
    client = make_client(snap)

    def boom(*_a, **_k):
        raise RuntimeError("secret internals /etc/shadow")

    client.app.state.store.current = boom
    r = client.get("/loans")
    _assert_problem(r, 500, "internal_error")
    assert "shadow" not in r.text


@pytest.mark.parametrize("path", ["/loans", "/coverage"])
def test_problem_type_is_stable(small_snapshot, make_client, path):
    snap, _ = small_snapshot
    client = make_client(snap, auth=False)
    r = client.get(path)
    assert r.json()["type"] == "https://bridge-loan.invalid/problems/gateway_auth_required"
