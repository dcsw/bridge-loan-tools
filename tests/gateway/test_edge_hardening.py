"""Request framing / normalization hardening of the edge (raw-socket tests).

Written first against the reviewer's smuggling probes: the edge must never forward a request whose
framing it did not itself validate, and duplicate single-valued headers must not collapse to a
different value than the one the edge made its decision on.
"""

from __future__ import annotations

import json
import socket
import time

import pytest
from edge_fixtures import TOKEN, Upstream, edge

HOST = "Host: x"


def req(*lines: str, body: bytes = b"") -> bytes:
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


def _read_response(sock: socket.socket) -> tuple[int, bytes]:
    data = b""
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
            head, sep, rest = data.partition(b"\r\n\r\n")
            if sep:
                lengths = [
                    int(line.split(b":")[1])
                    for line in head.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                ]
                if lengths and len(rest) >= lengths[0]:
                    break
    except (TimeoutError, ConnectionResetError):
        pass
    if not data:
        return 0, b""
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), body


def send(conn, payload: bytes, timeout: float = 4.0) -> tuple[int, bytes, float]:
    """Raw bytes to the edge; returns (status or 0 if closed silently, body, seconds)."""
    sock = socket.create_connection((conn.host, conn.port), timeout=timeout)
    start = time.monotonic()
    try:
        sock.sendall(payload)
        status, body = _read_response(sock)
    finally:
        sock.close()
    return status, body, time.monotonic() - start


def assert_rejected(result, status=400, code="bad_request"):
    got, body, _ = result
    assert got == status, (got, body)
    assert json.loads(body)["code"] == code
    assert Upstream.seen == [], "request must not reach the upstream"


# ---- the reviewer's probes ------------------------------------------------------------------


def test_smuggling_probe_duplicate_content_length_differing_values(make_edge):
    payload = req("POST /ok HTTP/1.1", HOST, "Content-Length: 3", "Content-Length: 1", body=b"abc")
    payload += req("GET /x HTTP/1.1", HOST)
    assert_rejected(send(make_edge(), payload))


def test_duplicate_content_length_same_values_rejected(make_edge):
    payload = req("POST /ok HTTP/1.1", HOST, "Content-Length: 3", "Content-Length: 3", body=b"abc")
    assert_rejected(send(make_edge(), payload))


def test_stall_probe_get_with_zero_then_large_content_length(make_edge):
    payload = req("GET /ok HTTP/1.1", HOST, "Content-Length: 0", "Content-Length: 40")
    result = send(make_edge(), payload)
    assert_rejected(result)
    assert result[2] < 3, "must be rejected immediately, not after the upstream timeout"


def test_content_length_with_transfer_encoding_rejected(make_edge):
    payload = req(
        "POST /ok HTTP/1.1", HOST, "Content-Length: 3", "Transfer-Encoding: chunked", body=b"abc"
    )
    assert_rejected(send(make_edge(), payload))


@pytest.mark.parametrize(
    "value",
    ["²", "٣", "-1", "+1", "1 2", "", "0x10", "1e3", "9" * 40],
    ids=["superscript2", "arabic3", "negative", "plus", "space", "empty", "hex", "exp", "huge"],
)
def test_non_strict_ascii_content_length_rejected(make_edge, value):
    payload = (
        b"POST /ok HTTP/1.1\r\nHost: x\r\nContent-Length: " + value.encode("utf-8") + b"\r\n\r\nabc"
    )
    assert_rejected(send(make_edge(), payload))


def test_forwarded_content_length_is_rebuilt_from_the_read_body(make_edge):
    payload = req("POST /ok HTTP/1.1", HOST, "Content-Length: 0003", body=b"abc")
    status, _, _ = send(make_edge(), payload)
    assert status == 200
    assert Upstream.seen[-1]["headers"]["content-length"] == "3"
    assert Upstream.seen[-1]["body"] == b"abc"


def test_truncated_body_is_never_forwarded(make_edge):
    payload = req("POST /ok HTTP/1.1", HOST, "Content-Length: 10", body=b"abc")
    status, _, _ = send(make_edge(client_timeout=0.5), payload)
    assert status in (0, 400, 408)
    assert Upstream.seen == []


# ---- duplicates of single-valued headers ------------------------------------------------------


@pytest.mark.parametrize("name", ["Host", "Origin", "Authorization", "Content-Type"])
def test_duplicate_single_valued_headers_rejected(make_edge, name):
    value = {"Origin": "http://localhost:5173", "Authorization": "Bearer abc"}.get(name, "a/b")
    lines = ["GET /ok HTTP/1.1"] + ([] if name == "Host" else [HOST])
    lines += [f"{name}: {value}", f"{name}: {value}x"]
    assert_rejected(send(make_edge(), req(*lines)))


def test_duplicate_origin_cannot_smuggle_an_unlisted_origin(make_edge):
    conn = make_edge(allowed_origins=frozenset({"http://localhost:5173"}))
    payload = req(
        "GET /ok HTTP/1.1", HOST, "Origin: http://localhost:5173", "Origin: https://evil.example"
    )
    assert_rejected(send(conn, payload))


# ---- Authorization normalization -----------------------------------------------------------------


def test_valid_authorization_is_forwarded_in_canonical_form(make_edge):
    status, _, _ = send(
        make_edge(), req("GET /ok HTTP/1.1", HOST, f"Authorization: Bearer {TOKEN}")
    )
    assert status == 200
    assert Upstream.seen[-1]["headers"]["authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    "bad",
    [
        "bearer abc",
        "BEARER abc",
        "Bearer  abc",
        "Bearer abc ",
        "Bearer a b",
        "Bearer ",
        "Basic abc",
        "Bearer a!c",
        "abc",
    ],
)
def test_formatting_variants_cannot_create_new_rate_limit_buckets(make_edge, bad):
    """Anything but exactly `Bearer <token>` is dropped, so KrakenD sees one canonical value
    (or none, which it answers with 401); variants never become distinct bucket keys."""
    status, _, _ = send(make_edge(), req("GET /ok HTTP/1.1", HOST, f"Authorization: {bad}"))
    assert status == 200  # the stub upstream does not authenticate; KrakenD would 401
    assert "authorization" not in Upstream.seen[-1]["headers"]


def test_oversize_authorization_rejected(make_edge):
    payload = req("GET /ok HTTP/1.1", HOST, "Authorization: Bearer " + "a" * 2100)
    assert_rejected(send(make_edge(), payload), status=431, code="header_too_large")


# ---- timeouts and concurrency -----------------------------------------------------------


def test_slow_body_is_cut_off_by_the_socket_timeout(make_edge):
    payload = req("POST /ok HTTP/1.1", HOST, "Content-Length: 100", body=b"ab")
    status, _, elapsed = send(make_edge(client_timeout=0.5), payload, timeout=6)
    assert status in (0, 408)
    assert elapsed < 4
    assert Upstream.seen == []


def test_saturated_edge_returns_503_json(make_edge):
    conn = make_edge(max_connections=1)
    holder = socket.create_connection((conn.host, conn.port))  # occupies the only slot
    try:
        time.sleep(0.3)
        status, body, _ = send(conn, req("GET /ok HTTP/1.1", HOST))
        assert status == 503 and json.loads(body)["code"] == "service_unavailable"
    finally:
        holder.close()
    time.sleep(0.6)  # the slot frees once the idle connection is closed
    assert send(conn, req("GET /ok HTTP/1.1", HOST))[0] == 200


# ---- request target ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "http://evil.example/loans",
        "ok",
        "*",
        "//evil.example/x",
        "/a/../b",
        "/a/./b",
        "/a/%2e%2e/b",
        "/a/%2E%2E/b",
        "/a%2Fb",
        "/a%2fb",
        "/a%5Cb",
        "/a\\b",
        "/a%00b",
    ],
)
def test_bad_request_targets_rejected(make_edge, target):
    payload = f"GET {target} HTTP/1.1\r\nHost: x\r\n\r\n".encode("latin-1")
    assert_rejected(send(make_edge(), payload))


def test_nul_in_target_rejected(make_edge):
    assert_rejected(send(make_edge(), b"GET /a\x00b HTTP/1.1\r\nHost: x\r\n\r\n"))


def test_query_string_is_forwarded_and_upstream_host_is_fixed(make_edge, upstream):
    send(make_edge(), req("GET /ok?a=1%2F2&b=%2e HTTP/1.1", "Host: evil.example"))
    seen = Upstream.seen[-1]
    assert seen["path"] == "/ok?a=1%2F2&b=%2e"
    assert seen["headers"]["host"] == f"127.0.0.1:{upstream.server_address[1]}"


# ---- logging noise ---------------------------------------------------------------------------


def test_broken_pipe_is_quiet_but_real_errors_are_reported(capsys):
    server = edge.make_server(
        edge.EdgeConfig(upstream_host="127.0.0.1", upstream_port=1), "127.0.0.1", 0
    )
    try:
        for exc in (BrokenPipeError(), ConnectionResetError()):
            try:
                raise exc
            except OSError:
                server.handle_error(None, ("127.0.0.1", 1))
        assert capsys.readouterr().err == ""
        try:
            raise ValueError("real bug")
        except ValueError:
            server.handle_error(None, ("127.0.0.1", 1))
        assert "real bug" in capsys.readouterr().err
    finally:
        server.server_close()


def test_config_reads_new_limits_from_env():
    cfg = edge.EdgeConfig.from_env(
        {"UPSTREAM": "http://k:8080", "MAX_CONNECTIONS": "7", "CLIENT_TIMEOUT": "3"}
    )
    assert cfg.max_connections == 7 and cfg.client_timeout == 3.0
