"""Second-round hardening: canonical JWTs, strict header parsing, deadlines, peer limits, routes.

Each test reproduces a probe from the adversarial review (or the rule it implies) against the
in-process edge with a recording stub upstream.
"""

from __future__ import annotations

import base64
import json
import socket
import time

import pytest
from edge_fixtures import TOKEN, Upstream, edge
from gateway_helpers import signature_variants as sig_variants
from test_edge_hardening import HOST, assert_rejected, req, send


def auth(token: str) -> str:
    return f"Authorization: Bearer {token}"


# ---- blocker 1: one token, one Authorization string, one rate-limit bucket ---------------------


def test_variants_decode_to_the_same_signature_bytes():
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    sigs = {base64.urlsafe_b64decode(pad(v.rsplit(".", 1)[1])) for v in sig_variants(TOKEN)}
    assert len(sigs) == 1, "test premise: all four spellings are the same signature"


def test_only_the_canonical_spelling_is_forwarded(make_edge):
    conn = make_edge()
    forwarded = []
    for variant in sig_variants(TOKEN):
        Upstream.seen.clear()
        status, _, _ = send(conn, req("GET /ok HTTP/1.1", HOST, auth(variant)))
        assert status == 200
        forwarded.append(Upstream.seen[-1]["headers"].get("authorization"))
    assert forwarded.count(f"Bearer {TOKEN}") == 1
    assert [f for f in forwarded if f is not None] == [f"Bearer {TOKEN}"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.rsplit(".", 1)[0],  # two segments
        lambda t: t + ".AAAA",  # four segments
        lambda t: t + "=",  # padding
        lambda t: t.replace(".", "..", 1),  # empty segment
        lambda t: t[:-1],  # signature 42 chars: not an HS256 signature
        lambda t: t + "A",  # 44 chars
        lambda t: t[:-1] + "+",  # standard-alphabet character
        lambda t: "A." + t.split(".", 1)[1],  # header segment with spare bits ("A" -> 6 bits)
        lambda t: "",
    ],
    ids=["two", "four", "padding", "empty", "short", "long", "plus", "header-bits", "empty-token"],
)
def test_malformed_jwts_are_never_forwarded_as_credentials(make_edge, mutate):
    token = mutate(TOKEN)
    status, _, _ = send(make_edge(), req("GET /ok HTTP/1.1", HOST, auth(token)))
    assert status == 200
    assert "authorization" not in Upstream.seen[-1]["headers"]


def test_peer_limiter_refills_and_bounds_its_table():
    now = [0.0]
    lim = edge.PeerLimiter(rate=1.0, burst=2, max_peers=3, clock=lambda: now[0])
    assert [lim.allow("a") for _ in range(3)] == [True, True, False]
    now[0] += 1.0
    assert lim.allow("a") is True and lim.allow("a") is False
    for peer in "bcdef":
        lim.allow(peer)
    assert len(lim.buckets) == 3, "table must stay bounded"
    assert "a" not in lim.buckets, "oldest peers are evicted first"
    assert lim.allow("a") is True, "an evicted peer simply starts with a fresh bucket"


def test_flooding_peer_gets_429_json_from_the_edge(make_edge):
    conn = make_edge(peer_rate=0.001, peer_burst=3)
    codes = [send(conn, req("GET /ok HTTP/1.1", HOST))[0] for _ in range(6)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429, 429]
    status, body, _ = send(conn, req("GET /ok HTTP/1.1", HOST))
    assert status == 429 and json.loads(body)["code"] == "rate_limited"


# ---- blocker 2: strict header parsing ---------------------------------------------------------


def test_probe_malformed_header_line_does_not_truncate_parsing(make_edge):
    payload = req(
        "POST /loans HTTP/1.1", HOST, "Bad Header: v", "Content-Length: 5",
        body=b"GET /admin HTTP/1.1\r\nHost: x\r\n\r\n",
    )  # fmt: skip
    assert_rejected(send(make_edge(), payload))


def test_probe_whitespace_before_colon_in_content_length(make_edge):
    payload = req("POST /loans HTTP/1.1", HOST, "Content-Length : 5", body=b"abcde")
    assert_rejected(send(make_edge(), payload))


def test_probe_whitespace_before_colon_in_transfer_encoding(make_edge):
    payload = req(
        "POST /loans HTTP/1.1", HOST, "Content-Length: 4", "Transfer-Encoding : chunked",
        body=b"abcd",
    )  # fmt: skip
    assert_rejected(send(make_edge(), payload))


@pytest.mark.parametrize(
    "payload",
    [
        b"GET /ok HTTP/1.1\r\nHost: x\r\n X-Folded: v\r\n\r\n",  # obs-fold
        b"GET /ok HTTP/1.1\r\nHost: x\r\n\tX-Folded: v\r\n\r\n",
        b"GET /ok HTTP/1.1\nHost: x\n\n",  # bare LF
        b"GET /ok HTTP/1.1\r\nHost: x\nX-A: 1\r\n\r\n",  # bare LF inside the block
        b"GET /ok HTTP/1.1\r\nHost: x\rX-A: 1\r\n\r\n",  # bare CR
        b"GET /ok HTTP/1.1\r\nHost: x\r\nX-A: a\x00b\r\n\r\n",  # NUL in value
        b"GET /ok HTTP/1.1\r\nHost: x\r\nX\x00A: 1\r\n\r\n",  # NUL in name
        b"GET /ok HTTP/1.1\r\nHost: x\r\nX-A: a\x01b\r\n\r\n",  # control char
        b"GET /ok HTTP/1.1\r\nHost: x\r\nX A: 1\r\n\r\n",  # space in name
        b"GET /ok HTTP/1.1\r\nHost: x\r\nX-A\r\n\r\n",  # no colon
        b"GET /ok HTTP/1.1\r\nHost: x\r\n: v\r\n\r\n",  # empty name
        b"GET /ok HTTP/1.1\r\nHost: x\r\nX-A(: 1\r\n\r\n",  # separator in name
        b"GET /ok HTTP/1.1 \r\nHost: x\r\n\r\n",  # trailing space on request line
        b"GET  /ok HTTP/1.1\r\nHost: x\r\n\r\n",  # double space
        b"get /ok HTTP/1.1\r\nHost: x\r\n\r\n",  # lowercase method
        b"\r\nGET /ok HTTP/1.1\r\nHost: x\r\n\r\n",  # leading blank line
        b"GET /ok\r\nHost: x\r\n\r\n",  # HTTP/0.9 style
        b"GET /ok HTTP/1.1\r\n\r\n",  # no Host
        b"GET /ok HTTP/1.1\r\nHost: x\r\nHost: y\r\n\r\n",
    ],
    ids=[
        "obs-fold-sp", "obs-fold-tab", "bare-lf", "bare-lf-in-block", "bare-cr", "nul-value",
        "nul-name", "ctl-value", "space-in-name", "no-colon", "empty-name", "separator-name",
        "trailing-space", "double-space", "lowercase-method", "leading-crlf", "http09",
        "no-host", "dup-host",
    ],
)  # fmt: skip
def test_malformed_requests_get_400_json_and_never_reach_the_upstream(make_edge, payload):
    assert_rejected(send(make_edge(), payload, timeout=3))


@pytest.mark.parametrize("version", ["HTTP/1.0", "HTTP/2.0", "HTTP/0.9", "HTTP/1.2"])
def test_other_http_versions_are_refused(make_edge, version):
    payload = f"GET /ok {version}\r\nHost: x\r\n\r\n".encode()
    assert_rejected(send(make_edge(), payload), status=505, code="version_not_supported")


def test_expect_100_continue_is_refused_before_any_100(make_edge):
    payload = req("POST /ok HTTP/1.1", HOST, "Content-Length: 3", "Expect: 100-continue")
    status, body, _ = send(make_edge(), payload)
    assert status == 417 and json.loads(body)["code"] == "expectation_failed"
    assert Upstream.seen == []


def test_too_many_headers_and_oversize_block_are_refused(make_edge):
    many = [f"X-H{i}: v" for i in range(101)]
    assert_rejected(
        send(make_edge(), req("GET /ok HTTP/1.1", HOST, *many)),
        status=431,
        code="header_too_large",
    )
    big = [f"X-H{i}: {'a' * 1000}" for i in range(10)]
    assert_rejected(
        send(make_edge(), req("GET /ok HTTP/1.1", HOST, *big)),
        status=431,
        code="header_too_large",
    )


def test_unknown_methods_are_405_from_the_edge(make_edge):
    assert_rejected(
        send(make_edge(), req("TRACE /ok HTTP/1.1", HOST)),
        status=405,
        code="method_not_allowed",
    )


def test_pipelined_requests_are_forwarded_exactly_as_the_client_sent_them(make_edge):
    one = req("GET /ok?n=1 HTTP/1.1", HOST)
    two = req("POST /ok?n=2 HTTP/1.1", HOST, "Content-Length: 3", body=b"abc")
    three = req("GET /ok?n=3 HTTP/1.1", HOST, "Connection: close")
    conn = make_edge()
    sock = socket.create_connection((conn.host, conn.port), timeout=5)
    sock.sendall(one + two + three)
    data = b""
    while data.count(b"HTTP/1.1 200") < 3:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    sock.close()
    assert [s["path"] for s in Upstream.seen] == ["/ok?n=1", "/ok?n=2", "/ok?n=3"]
    assert [s["body"] for s in Upstream.seen] == [b"", b"abc", b""]


def test_single_logical_request_with_bad_header_reaches_upstream_zero_times(make_edge):
    payload = req("GET /ok HTTP/1.1", HOST, "Bad Header: v") + req("GET /second HTTP/1.1", HOST)
    send(make_edge(), payload)
    assert Upstream.seen == []


def test_only_allow_listed_headers_are_forwarded(make_edge):
    payload = req(
        "GET /ok HTTP/1.1", HOST,
        "Accept: a/b", "Accept: c/d", "Accept-Language: en",
        "Cookie: s=1", "cookie: s=2", "X-Custom: 1", "Accept-Encoding: gzip",
        "Content-Type: application/json", "X-Forwarded-Host: evil", "Forwarded: for=evil",
        "X-Key-Id: spoof", "X-Gateway-Secret: spoof", "Via: 1.1 evil",
    )  # fmt: skip
    send(make_edge(), payload)
    seen = Upstream.seen[-1]["headers"]
    assert seen["accept"] == "a/b, c/d" and seen["accept-language"] == "en"
    assert seen["content-type"] == "application/json"
    assert seen["accept-encoding"] == "identity"  # http.client's own; the client's gzip is dropped
    for dropped in ("cookie", "x-custom", "x-forwarded-host", "forwarded",
                    "x-key-id", "x-gateway-secret", "via"):  # fmt: skip
        assert dropped not in seen, dropped
    assert seen["x-forwarded-for"] == "127.0.0.1"


# ---- blocker 3: total deadlines and connection limits -----------------------------------------


def drip(conn, data: bytes, interval: float, limit: float, prefix: bytes = b""):
    """Send ``prefix`` at once, then ``data`` one byte per ``interval``; return seconds until the
    server closes us (None if it never does within ``limit``)."""
    sock = socket.create_connection((conn.host, conn.port), timeout=limit)
    sock.sendall(prefix)
    start = time.monotonic()
    sock.settimeout(0.05)
    try:
        for i in range(len(data)):
            sock.sendall(data[i : i + 1])
            end = time.monotonic() + interval
            while time.monotonic() < end:
                try:
                    if sock.recv(65536) == b"":
                        return time.monotonic() - start
                except TimeoutError:
                    pass
            if time.monotonic() - start > limit:
                break
    except OSError:
        return time.monotonic() - start
    finally:
        sock.close()
    return None


def test_header_drip_is_cut_off_at_the_total_deadline(make_edge):
    conn = make_edge(client_timeout=2.0, header_timeout=1.5)
    payload = b"GET /ok HTTP/1.1\r\nHost: x\r\nX-Slow: " + b"a" * 100
    elapsed = drip(conn, payload, interval=0.3, limit=8)
    assert elapsed is not None, "drip client was never disconnected"
    assert elapsed < 3.5, f"cut off after {elapsed:.1f}s; per-read timeouts would allow far longer"
    assert Upstream.seen == []


def test_body_drip_is_cut_off_at_the_total_deadline(make_edge):
    conn = make_edge(client_timeout=2.0, body_timeout=1.5)
    head = req("POST /ok HTTP/1.1", HOST, "Content-Length: 100")
    elapsed = drip(conn, b"x" * 90, interval=0.3, limit=8, prefix=head)
    assert elapsed is not None, "body drip client was never disconnected"
    assert elapsed < 3.5, f"cut off after {elapsed:.1f}s"
    assert Upstream.seen == []


def test_idle_connection_is_closed(make_edge):
    conn = make_edge(client_timeout=0.5)
    sock = socket.create_connection((conn.host, conn.port), timeout=4)
    start = time.monotonic()
    assert sock.recv(10) == b""
    assert time.monotonic() - start < 2.5
    sock.close()


def test_peer_cannot_exceed_its_connection_share_but_others_are_served(make_edge):
    conn = make_edge(max_connections=10, max_connections_per_peer=2)
    holders = [socket.create_connection((conn.host, conn.port)) for _ in range(2)]
    try:
        time.sleep(0.3)
        status, body, _ = send(conn, req("GET /ok HTTP/1.1", HOST))
        assert status == 429 and json.loads(body)["code"] == "rate_limited"
        other = socket.socket()
        other.bind(("127.0.0.2", 0))  # a different peer address on the loopback interface
        other.settimeout(4)
        other.connect((conn.host, conn.port))
        other.sendall(req("GET /ok HTTP/1.1", HOST))
        assert other.recv(100).startswith(b"HTTP/1.1 200")
        other.close()
    finally:
        for h in holders:
            h.close()
    time.sleep(0.6)
    assert send(conn, req("GET /ok HTTP/1.1", HOST))[0] == 200


# ---- route allow-list ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/coverage", "/loans", "/loans?limit=5&town=x", "/loans/abc-1_2.3:4", "/loans/x/source",
     "/health/live", "/health/ready", "/__health"],
)  # fmt: skip
def test_known_routes_are_forwarded(make_edge, path):
    send(make_edge(strict_routes=True), req(f"GET {path} HTTP/1.1", HOST))
    assert Upstream.seen[-1]["path"] == path  # reached the upstream (the stub 404s unknown paths)


@pytest.mark.parametrize(
    "path",
    ["/", "/nope", "/loans/", "/loans//x", "/loans/a/b", "/loans/x/source/", "/coverage/",
     "/loans/a;x=1", "/loans/%252e", "/loans/%41", "/Loans", "/loans/x/Source", "/health",
     "/health/live/", "/__health/x", "/loans/" + "a" * 129],
)  # fmt: skip
def test_alternate_spellings_never_reach_a_limited_route(make_edge, path):
    status, body, _ = send(make_edge(strict_routes=True), req(f"GET {path} HTTP/1.1", HOST))
    assert status == 404 and json.loads(body)["code"] == "not_found"
    assert Upstream.seen == []


def test_from_env_enables_the_route_allow_list_and_deadlines():
    cfg = edge.EdgeConfig.from_env(
        {
            "UPSTREAM": "http://k:8080",
            "HEADER_TIMEOUT": "4",
            "BODY_TIMEOUT": "6",
            "PEER_MAX_CONNECTIONS": "3",
            "PEER_RATE": "5",
            "PEER_BURST": "9",
        }
    )
    assert cfg.strict_routes is True
    assert (cfg.header_timeout, cfg.body_timeout, cfg.max_connections_per_peer) == (4.0, 6.0, 3)
    assert (cfg.peer_rate, cfg.peer_burst) == (5.0, 9)
