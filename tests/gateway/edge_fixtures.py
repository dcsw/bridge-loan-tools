"""Shared fixtures for the edge tests: a fake KrakenD-like upstream and in-process edge servers."""

from __future__ import annotations

import http.client
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from gateway_helpers import load_edge, sign_jwt

edge = load_edge()
# a real, canonical gateway token (the edge only forwards canonical HS256-shaped JWTs)
TOKEN = sign_jwt("k" * 64, "key-alpha")


class Upstream(BaseHTTPRequestHandler):
    """Behaviour is selected by the request path."""

    protocol_version = "HTTP/1.1"
    seen: list[dict] = []

    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", headers=None, content_length=True):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if content_length:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def handle_any(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.seen.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
            }
        )
        p = self.path.split("?")[0]
        krakend_hdrs = {"X-Krakend": "Version 3.0.3", "Server": "KrakenD"}
        if p == "/ok":
            self._send(200, b'{"ok":true}', {"Content-Type": "application/json", **krakend_hdrs})
        elif p.startswith("/empty/"):
            self._send(int(p.rsplit("/", 1)[1]), b"", krakend_hdrs)
        elif p == "/problem":
            body = json.dumps({"type": "t", "title": "x", "status": 503, "code": "snapshot"})
            self._send(
                503,
                body.encode(),
                {"Content-Type": "application/problem+json", "Retry-After": "5"},
            )
        elif p == "/plain/404":
            self._send(404, b"404 page not found", {"Content-Type": "text/plain"})
        elif p == "/secheaders/401":
            self._send(401, b"", {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"})
        elif p == "/own429":
            self._send(429, b'{"code":"backend_limit"}', {"Content-Type": "application/json"})
        elif p == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"%PDF-1.7 chunk1 ")
            self.wfile.write(b"chunk2")
            self.close_connection = True
        elif p == "/slow":
            time.sleep(1.5)
            self._send(200, b"late")
        elif p == "/hop":
            self._send(200, b"x", {"Connection": "close", "Keep-Alive": "timeout=5"})
        else:
            self._send(404, b"")

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_OPTIONS = handle_any


def _serve(handler) -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def upstream() -> Iterator[ThreadingHTTPServer]:
    Upstream.seen = []
    yield from _serve(Upstream)


@pytest.fixture
def make_edge(upstream):
    servers = []

    def _make(**overrides) -> http.client.HTTPConnection:
        cfg = edge.EdgeConfig(
            upstream_host="127.0.0.1",
            upstream_port=upstream.server_address[1],
            **overrides,
        )
        server = edge.make_server(cfg, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)

    yield _make
    for s in servers:
        s.shutdown()
        s.server_close()
