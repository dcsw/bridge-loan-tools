"""Unit tests for the thin header+body-adding edge in front of KrakenD (ADR-0005 spike result).

The edge runs in-process against a fake upstream that mimics KrakenD CE: empty-body 4xx/5xx for
gateway-generated errors, passthrough bodies for everything else.
"""

from __future__ import annotations

import http.client
import json
import threading

import pytest
from edge_fixtures import TOKEN, Upstream, edge


def get(conn, path, method="GET", headers=None, body=None):
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    return resp, resp.read()


def test_passthrough_200_untouched(make_edge):
    resp, body = get(make_edge(), "/ok")
    assert resp.status == 200 and body == b'{"ok":true}'
    assert resp.getheader("Content-Type") == "application/json"


def test_problem_json_passes_through_untouched(make_edge):
    resp, body = get(make_edge(), "/problem")
    assert resp.status == 503
    assert resp.getheader("Content-Type") == "application/problem+json"
    assert resp.getheader("Retry-After") == "5"
    assert json.loads(body) == {"type": "t", "title": "x", "status": 503, "code": "snapshot"}


def test_empty_429_gets_json_body_and_retry_after(make_edge):
    resp, body = get(make_edge(retry_after_seconds=3), "/empty/429")
    assert resp.status == 429
    assert resp.getheader("Content-Type") == "application/json"
    assert resp.getheader("Retry-After") == "3"
    data = json.loads(body)
    assert data["code"] == "rate_limited" and data["retry_after_seconds"] == 3
    assert data["status"] == 429


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "bad_request"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
        (405, "method_not_allowed"),
        (500, "gateway_error"),
        (503, "service_unavailable"),
        (504, "gateway_timeout"),
        (418, "gateway_error"),
    ],
)
def test_empty_gateway_errors_get_json_code(make_edge, status, code):
    resp, body = get(make_edge(), f"/empty/{status}")
    assert resp.status == status
    assert resp.getheader("Content-Type") == "application/json"
    data = json.loads(body)
    assert data["code"] == code and data["status"] == status
    assert "retry_after_seconds" not in data
    assert resp.getheader("Retry-After") is None


def test_krakend_plain_text_error_is_replaced_with_json(make_edge):
    resp, body = get(make_edge(), "/plain/404")
    assert resp.status == 404
    assert resp.getheader("Content-Type") == "application/json"
    assert json.loads(body)["code"] == "not_found"


def test_security_headers_survive_error_rewrite(make_edge):
    resp, body = get(make_edge(), "/secheaders/401")
    assert resp.status == 401 and json.loads(body)["code"] == "unauthorized"
    assert resp.getheader("X-Content-Type-Options") == "nosniff"
    assert resp.getheader("X-Frame-Options") == "DENY"


def test_edge_generated_errors_carry_security_headers(make_edge):
    resp, _ = get(make_edge(max_body_bytes=1), "/ok", method="POST", body=b"xx")
    assert resp.status == 413
    assert resp.getheader("X-Content-Type-Options") == "nosniff"
    assert resp.getheader("X-Frame-Options") == "DENY"


def test_unlisted_origin_is_rejected_before_forwarding(make_edge):
    conn = make_edge(allowed_origins=frozenset({"http://localhost:5173"}))
    resp, body = get(conn, "/ok", headers={"Origin": "https://evil.example"})
    assert resp.status == 403
    assert json.loads(body)["code"] == "origin_not_allowed"
    assert Upstream.seen == []


def test_listed_origin_and_originless_requests_pass(make_edge):
    conn = make_edge(allowed_origins=frozenset({"http://localhost:5173"}))
    assert get(conn, "/ok", headers={"Origin": "http://localhost:5173"})[0].status == 200
    assert get(conn, "/ok")[0].status == 200


def test_origin_allow_list_is_read_from_krakend_config(tmp_path):
    cfg = tmp_path / "k.json"
    cfg.write_text(
        json.dumps({"extra_config": {"security/cors": {"allow_origins": ["http://a:1"]}}})
    )
    parsed = edge.EdgeConfig.from_env({"UPSTREAM": "http://k:8080", "CORS_CONFIG": str(cfg)})
    assert parsed.allowed_origins == frozenset({"http://a:1"})


def test_wildcard_origin_in_config_is_refused(tmp_path):
    cfg = tmp_path / "k.json"
    cfg.write_text(json.dumps({"extra_config": {"security/cors": {"allow_origins": ["*"]}}}))
    with pytest.raises(ValueError, match="wildcard"):
        edge.EdgeConfig.from_env({"UPSTREAM": "http://k:8080", "CORS_CONFIG": str(cfg)})


def test_backend_429_with_body_is_untouched(make_edge):
    resp, body = get(make_edge(), "/own429")
    assert resp.status == 429 and body == b'{"code":"backend_limit"}'
    assert resp.getheader("Retry-After") is None


def test_server_fingerprint_headers_stripped(make_edge):
    resp, _ = get(make_edge(), "/ok")
    assert resp.getheader("X-Krakend") is None
    assert resp.getheader("Server") is None


def test_hop_by_hop_response_headers_not_forwarded(make_edge):
    resp, _ = get(make_edge(), "/hop")
    assert resp.getheader("Keep-Alive") is None


def test_streams_close_delimited_upstream_body(make_edge):
    resp, body = get(make_edge(), "/stream")
    assert resp.status == 200 and body == b"%PDF-1.7 chunk1 chunk2"
    assert resp.getheader("Content-Type") == "application/pdf"


def test_head_has_no_body_and_is_not_rewritten(make_edge):
    resp, body = get(make_edge(), "/ok", method="HEAD")
    assert resp.status == 200 and body == b""


def test_forwards_method_path_query_and_end_user_headers(make_edge, upstream):
    resp, _ = get(
        make_edge(),
        "/ok?town=x&limit=5",
        headers={"Authorization": f"Bearer {TOKEN}", "Accept": "a/b"},
    )
    assert resp.status == 200
    seen = Upstream.seen[-1]
    assert seen["path"] == "/ok?town=x&limit=5"
    assert seen["headers"]["authorization"] == f"Bearer {TOKEN}"
    assert seen["headers"]["accept"] == "a/b"


def test_client_supplied_gateway_headers_are_dropped(make_edge):
    get(
        make_edge(),
        "/ok",
        headers={"X-Gateway-Secret": "evil", "X-Key-Id": "evil", "X-Forwarded-For": "6.6.6.6"},
    )
    seen = Upstream.seen[-1]["headers"]
    assert "x-gateway-secret" not in seen
    assert "x-key-id" not in seen
    assert seen["x-forwarded-for"] == "127.0.0.1"


def test_oversize_content_length_rejected_before_forwarding(make_edge):
    conn = make_edge(max_body_bytes=10)
    resp, body = get(conn, "/ok", method="POST", body=b"x" * 11)
    assert resp.status == 413
    assert json.loads(body)["code"] == "payload_too_large"
    assert Upstream.seen == []


def test_body_within_limit_is_forwarded(make_edge):
    resp, _ = get(make_edge(max_body_bytes=10), "/ok", method="POST", body=b"x" * 10)
    assert resp.status == 200
    assert Upstream.seen[-1]["body"] == b"x" * 10


def test_chunked_request_body_rejected(make_edge):
    conn = make_edge()
    conn.putrequest("POST", "/ok")
    conn.putheader("Transfer-Encoding", "chunked")
    conn.endheaders()
    conn.send(b"3\r\nabc\r\n0\r\n\r\n")
    resp = conn.getresponse()
    assert resp.status == 411
    assert json.loads(resp.read())["code"] == "length_required"
    assert Upstream.seen == []


def test_invalid_content_length_rejected(make_edge):
    conn = make_edge()
    conn.putrequest("POST", "/ok")
    conn.putheader("Content-Length", "abc")
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 400
    assert json.loads(resp.read())["code"] == "bad_request"


def test_upstream_down_gives_json_502():
    cfg = edge.EdgeConfig(upstream_host="127.0.0.1", upstream_port=1)
    server = edge.make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        resp, body = get(conn, "/ok")
        assert resp.status == 502
        assert json.loads(body)["code"] == "bad_gateway"
        assert resp.getheader("Content-Type") == "application/json"
    finally:
        server.shutdown()
        server.server_close()


def test_upstream_timeout_gives_json_504(make_edge):
    resp, body = get(make_edge(upstream_timeout=0.3), "/slow")
    assert resp.status == 504
    assert json.loads(body)["code"] == "gateway_timeout"


def test_config_from_env():
    cfg = edge.EdgeConfig.from_env(
        {"UPSTREAM": "http://krakend:8080", "MAX_BODY_BYTES": "99", "RETRY_AFTER_SECONDS": "2"}
    )
    assert (cfg.upstream_host, cfg.upstream_port) == ("krakend", 8080)
    assert cfg.max_body_bytes == 99 and cfg.retry_after_seconds == 2


@pytest.mark.parametrize("bad", ["krakend:8080", "https://krakend:8080", "http://krakend"])
def test_config_rejects_bad_upstream(bad):
    with pytest.raises(ValueError):
        edge.EdgeConfig.from_env({"UPSTREAM": bad})


def test_main_serves_until_interrupted(monkeypatch):
    started = {}

    class FakeServer:
        def serve_forever(self):
            started["served"] = True

        def server_close(self):
            started["closed"] = True

    monkeypatch.setattr(edge, "make_server", lambda cfg, host, port: FakeServer())
    monkeypatch.setenv("UPSTREAM", "http://krakend:8080")
    assert edge.main() == 0
    assert started == {"served": True, "closed": True}
