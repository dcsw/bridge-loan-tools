#!/usr/bin/env python3
"""Thin, strict edge in front of KrakenD CE (ADR-0005).

KrakenD CE generates some errors (router-level 429, JWT 401, 404, timeouts) with an EMPTY body (or
gin's plain-text one) and no way to add headers (spike results in ADR-0005). This edge forwards
requests to KrakenD and adds what CE cannot:

* error responses (status >= 400) with an empty or text/plain body get a JSON body
  ``{"code", "message", "status"}``; a 429 also gets ``retry_after_seconds`` and ``Retry-After``.
  The API never emits empty or text/plain errors (problem+json only), so backend errors pass through
  untouched;
* its own HTTP/1.1 request parser. The stdlib's tolerant parser silently stops at malformed header
  lines, which lets a request be framed differently here than in KrakenD (request smuggling). This
  parser rejects anything ambiguous with a JSON 4xx and closes the connection (see ADR-0005);
* a route allow-list, an ``Origin`` allow-list read from krakend.json, a request size limit,
  canonical-only JWTs (so one token cannot occupy several rate-limit buckets), header allow-list
  forwarding, total per-phase deadlines against slow clients, per-peer and global connection caps,
  and a per-peer request-rate guard.

Standard library only, so the image is python:slim and nothing else.
"""

from __future__ import annotations

import base64
import binascii
import http.client
import json
import os
import re
import socket
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.utils import formatdate
from http.server import ThreadingHTTPServer
from socketserver import StreamRequestHandler
from urllib.parse import urlsplit

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
STRIP_FROM_UPSTREAM = {"server", "x-krakend", "x-krakend-completed", "date"}
# when an empty upstream error body is replaced, these describe the old body and must not survive
BODY_HEADERS = {"content-length", "content-type", "content-encoding", "etag", "retry-after"}

# Allow-list: ONLY these client headers are forwarded (plus the ones the edge builds itself:
# Authorization (canonical), Host (fixed), X-Forwarded-For, Content-Length (rebuilt)).
FORWARD_HEADERS = (
    "Accept",
    "Accept-Language",
    "Content-Type",
    "Origin",  # needed by KrakenD's CORS middleware (already checked against the allow-list)
    "Access-Control-Request-Method",
    "Access-Control-Request-Headers",
)
# Headers the edge decides on or forwards: more than one copy is ambiguous (parsers disagree on
# first-vs-last), so the request is refused instead of being normalised.
SINGLE_VALUED = (
    "host",
    "origin",
    "authorization",
    "content-type",
    "content-length",
    "transfer-encoding",
    "access-control-request-method",
    "access-control-request-headers",
    "expect",
)
METHODS = {"GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"}
ROUTES = tuple(
    re.compile(p)
    for p in (
        r"/coverage",
        r"/loans",
        r"/loans/[A-Za-z0-9_.:-]{1,128}",
        r"/loans/[A-Za-z0-9_.:-]{1,128}/source",
        r"/health/live",
        r"/health/ready",
        r"/__health",
    )
)
REQUEST_LINE_RE = re.compile(rb"([A-Z]{3,10}) ([\x21-\x7e]+) (HTTP/[0-9]\.[0-9])")
HEADER_LINE_RE = re.compile(rb"([!#$%&'*+.^_`|~0-9A-Za-z-]+):[ \t]*([\x20-\x7e\x09\x80-\xff]*)")
CONTENT_LENGTH_RE = re.compile(r"[0-9]{1,18}")  # strict ASCII digits only
BEARER_RE = re.compile(r"Bearer ([A-Za-z0-9._-]+)")  # case-sensitive scheme, single space
B64URL_RE = re.compile(r"[A-Za-z0-9_-]+")
BARE_LF_RE = re.compile(rb"(?<!\r)\n")
HS256_SIGNATURE_CHARS = 43  # 32 bytes, unpadded base64url
MAX_AUTH_BYTES = 2048
MAX_TARGET = 4096
MAX_HEADER_LINES = 100
MAX_HEAD_BYTES = 8192

CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    408: "request_timeout",
    411: "length_required",
    413: "payload_too_large",
    417: "expectation_failed",
    429: "rate_limited",
    431: "header_too_large",
    500: "gateway_error",
    502: "bad_gateway",
    503: "service_unavailable",
    504: "gateway_timeout",
    505: "version_not_supported",
}
MESSAGES = {
    "bad_request": "The request could not be understood.",
    "unauthorized": "A valid bearer token is required.",
    "forbidden": "Access is forbidden.",
    "origin_not_allowed": "This origin is not allowed.",
    "not_found": "No such route.",
    "method_not_allowed": "Method not allowed.",
    "request_timeout": "The request was not completed in time.",
    "header_too_large": "A request header is too large.",
    "length_required": "Requests with a body must declare Content-Length.",
    "payload_too_large": "The request body is too large.",
    "expectation_failed": "Expect is not supported.",
    "rate_limited": "Too many requests; slow down and retry.",
    "gateway_error": "The gateway could not complete the request.",
    "bad_gateway": "The gateway could not reach its upstream.",
    "service_unavailable": "The service is temporarily unavailable.",
    "gateway_timeout": "The upstream did not answer in time.",
    "version_not_supported": "Only HTTP/1.1 is supported.",
}
REASONS = {
    200: "OK",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    411: "Length Required",
    413: "Content Too Large",
    417: "Expectation Failed",
    429: "Too Many Requests",
    431: "Request Header Fields Too Large",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
    505: "HTTP Version Not Supported",
}
# KrakenD adds these to its own responses; the edge adds them to the ones it generates itself.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
NO_BODY_STATUSES = {204, 304}
CHUNK = 64 * 1024


@dataclass(frozen=True)
class EdgeConfig:
    upstream_host: str
    upstream_port: int
    max_body_bytes: int = 65_536
    # Honest only while every KrakenD bucket refills >= 1 token/s (tests/gateway checks the config).
    retry_after_seconds: int = 1
    upstream_timeout: float = 15.0  # KrakenD's own timeout is 10 s; this is the outer bound
    allowed_origins: frozenset[str] | None = None  # None: not enforced (unit tests only)
    strict_routes: bool = False  # production (from_env) always enforces the route allow-list
    max_connections: int = 64  # concurrent client connections; beyond this: 503 JSON
    max_connections_per_peer: int = 16  # per client address; beyond this: 429 JSON
    client_timeout: float = 15.0  # per-recv cap AND the keep-alive idle timeout
    header_timeout: float = 10.0  # TOTAL time from the first byte to the end of the header block
    body_timeout: float = 15.0  # TOTAL time to receive the request body
    peer_rate: float = 200.0  # requests/s per peer address (flood guard, not the API limit)
    peer_burst: int = 400
    max_tracked_peers: int = 1024

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> EdgeConfig:
        parts = urlsplit(env.get("UPSTREAM", ""))
        if parts.scheme != "http" or not parts.hostname or not parts.port:
            raise ValueError("UPSTREAM must look like http://host:port")
        origins = None
        if env.get("CORS_CONFIG"):
            with open(env["CORS_CONFIG"], encoding="utf-8") as fh:
                cors = json.load(fh)["extra_config"]["security/cors"]
            origins = frozenset(cors["allow_origins"])
            if any("*" in origin for origin in origins):
                raise ValueError("wildcard origins are not allowed")

        def num(name: str, default: float, kind: Callable[[str], float] = float) -> float:
            return kind(env[name]) if name in env else default

        return cls(
            upstream_host=parts.hostname,
            upstream_port=parts.port,
            max_body_bytes=int(num("MAX_BODY_BYTES", cls.max_body_bytes, int)),
            retry_after_seconds=int(num("RETRY_AFTER_SECONDS", cls.retry_after_seconds, int)),
            allowed_origins=origins,
            strict_routes=True,
            max_connections=int(num("MAX_CONNECTIONS", cls.max_connections, int)),
            max_connections_per_peer=int(
                num("PEER_MAX_CONNECTIONS", cls.max_connections_per_peer, int)
            ),
            client_timeout=num("CLIENT_TIMEOUT", cls.client_timeout),
            header_timeout=num("HEADER_TIMEOUT", cls.header_timeout),
            body_timeout=num("BODY_TIMEOUT", cls.body_timeout),
            peer_rate=num("PEER_RATE", cls.peer_rate),
            peer_burst=int(num("PEER_BURST", cls.peer_burst, int)),
        )


def error_payload(status: int, retry_after: int, code: str | None = None) -> dict[str, object]:
    code = code or CODES.get(status, "gateway_error")
    body: dict[str, object] = {"code": code, "message": MESSAGES[code], "status": status}
    if status == 429:
        body["retry_after_seconds"] = retry_after
    return body


def bad_target(target: str) -> bool:
    """True for request targets the edge refuses to normalise: it only serves origin-form paths."""
    if not target.startswith("/") or target.startswith("//") or len(target) > MAX_TARGET:
        return True  # absolute-URI, authority-form, asterisk-form, protocol-relative, huge
    if any(ord(c) <= 0x20 or ord(c) >= 0x7F or c == "\\" for c in target):
        return True  # whitespace, NUL and other controls, non-ASCII, backslash
    path = target.partition("?")[0]
    lowered = path.lower()
    if any(enc in lowered for enc in ("%2f", "%5c", "%00", "%2e")):
        return True  # encoded slash, backslash, NUL or dot: no decoding games
    return any(segment in (".", "..") for segment in path.split("/"))


def canonical_b64url(segment: str) -> bool:
    """True when ``segment`` is unpadded base64url that re-encodes to itself (no spare bits)."""
    if not B64URL_RE.fullmatch(segment):
        return False
    try:
        decoded = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError):
        return False
    return base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") == segment


def canonical_jwt(token: str) -> bool:
    """Exactly three canonical base64url segments with an HS256-sized (32 byte) signature.

    KrakenD accepts non-canonical trailing bits in the signature, so one valid token would otherwise
    have several distinct Authorization strings, each with its own rate-limit bucket.
    """
    parts = token.split(".")
    return (
        len(parts) == 3
        and all(canonical_b64url(p) for p in parts)
        and len(parts[2]) == HS256_SIGNATURE_CHARS
    )


class PeerLimiter:
    """Token bucket per peer address with a bounded table (oldest peers are evicted first)."""

    def __init__(
        self,
        rate: float,
        burst: int,
        max_peers: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate, self.burst, self.max_peers, self.clock = rate, burst, max_peers, clock
        self.buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, peer: str) -> bool:
        now = self.clock()
        with self.lock:
            tokens, last = self.buckets.pop(peer, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - last) * self.rate)
            allowed = tokens >= 1
            self.buckets[peer] = (tokens - 1 if allowed else tokens, now)
            while len(self.buckets) > self.max_peers:
                self.buckets.popitem(last=False)
            return allowed


class RequestError(Exception):
    """A request the edge refuses; carries the JSON error to send before closing."""

    def __init__(self, status: int, code: str | None = None) -> None:
        super().__init__(status)
        self.status, self.code = status, code


class Deadline(Exception):
    """The client did not deliver the next bytes in time."""


class ClientGone(Exception):
    """The client closed the connection."""


@dataclass
class Request:
    method: str
    target: str
    headers: dict[str, list[str]]


def parse_head(head: bytes) -> Request:
    """Strictly parse a request line + header block (without the final blank line)."""
    lines = head.split(b"\r\n")
    if any(b"\r" in line or b"\n" in line for line in lines):
        raise RequestError(400)  # bare CR / LF
    match = REQUEST_LINE_RE.fullmatch(lines[0])
    if match is None:
        raise RequestError(400)
    if match.group(3) != b"HTTP/1.1":
        raise RequestError(505)
    if len(lines) - 1 > MAX_HEADER_LINES:
        raise RequestError(431)
    headers: dict[str, list[str]] = {}
    for line in lines[1:]:
        header = HEADER_LINE_RE.fullmatch(line)  # also rejects obs-fold and "Name : v"
        if header is None:
            raise RequestError(400)
        name = header.group(1).decode("ascii").lower()
        headers.setdefault(name, []).append(header.group(2).decode("latin-1").rstrip(" \t"))
    return Request(match.group(1).decode("ascii"), match.group(2).decode("ascii"), headers)


def make_server(config: EdgeConfig, host: str, port: int) -> ThreadingHTTPServer:
    limiter = PeerLimiter(config.peer_rate, config.peer_burst, config.max_tracked_peers)

    class Handler(StreamRequestHandler):
        # -- low level I/O ---------------------------------------------------------------------
        def setup(self) -> None:
            super().setup()
            self.buf = bytearray()
            self.closing = False

        def recv_more(self, deadline: float) -> None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Deadline
            self.request.settimeout(min(remaining, config.client_timeout))
            try:
                data = self.request.recv(CHUNK)
            except TimeoutError as exc:
                raise Deadline from exc
            if not data:
                raise ClientGone
            self.buf.extend(data)

        def read_head(self) -> bytes | None:
            """The raw header block, or None on a clean close between requests."""
            if not self.buf:
                try:
                    self.recv_more(time.monotonic() + config.client_timeout)  # idle keep-alive
                except (ClientGone, Deadline):
                    return None
            deadline = time.monotonic() + config.header_timeout  # TOTAL, not per read
            while True:
                end = self.buf.find(b"\r\n\r\n")
                if end >= 0:
                    if end > MAX_HEAD_BYTES:
                        raise RequestError(431)
                    head = bytes(self.buf[:end])
                    del self.buf[: end + 4]
                    return head
                if len(self.buf) > MAX_HEAD_BYTES + 4:
                    raise RequestError(431)
                if BARE_LF_RE.search(self.buf):
                    raise RequestError(400)
                self.recv_more(deadline)

        def read_body(self, length: int) -> bytes:
            deadline = time.monotonic() + config.body_timeout  # TOTAL for the whole body
            while len(self.buf) < length:
                self.recv_more(deadline)
            body = bytes(self.buf[:length])
            del self.buf[:length]
            return body

        def send(self, data: bytes) -> None:
            self.request.settimeout(config.client_timeout)
            self.request.sendall(data)

        def status_line(self, status: int, reason: str | None) -> list[str]:
            sys.stderr.write(f"edge -> {status}\n")
            return [
                f"HTTP/1.1 {status} {reason or REASONS.get(status, 'Status')}",
                f"Date: {formatdate(usegmt=True)}",
            ]

        def send_error_json(
            self,
            status: int,
            *,
            code: str | None = None,
            extra: Mapping[str, str] | None = None,
            close: bool = True,
            head_only: bool = False,
        ) -> None:
            body = json.dumps(error_payload(status, config.retry_after_seconds, code)).encode()
            lines = self.status_line(status, None)
            lines += ["Content-Type: application/json", f"Content-Length: {len(body)}"]
            lines += [f"{k}: {v}" for k, v in {**SECURITY_HEADERS, **(extra or {})}.items()]
            if status == 429:
                lines.append(f"Retry-After: {config.retry_after_seconds}")
            if close:
                lines.append("Connection: close")
                self.closing = True
            payload = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
            self.send(payload if head_only else payload + body)

        # -- request handling ----------------------------------------------------------------
        def handle(self) -> None:
            while not self.closing:
                try:
                    head = self.read_head()
                    if head is None:
                        return
                    self.serve(parse_head(head))
                except RequestError as err:
                    self.send_error_json(err.status, code=err.code)
                except Deadline:
                    self.send_error_json(408)
                except ClientGone:
                    return

        def authorization(self, headers: dict[str, list[str]]) -> str | None:
            """The canonical Authorization value, or None when it must be dropped (-> 401)."""
            values = headers.get("authorization")
            if not values:
                return None
            raw = values[0]
            if len(raw.encode("latin-1")) > MAX_AUTH_BYTES:
                raise RequestError(431)
            match = BEARER_RE.fullmatch(raw)
            if match is None or not canonical_jwt(match.group(1)):
                return None
            return f"Bearer {match.group(1)}"

        def validate(self, req: Request) -> str | None:
            """Refuse anything ambiguous; return the canonical Authorization (or None)."""
            if req.method not in METHODS:
                raise RequestError(405)
            if bad_target(req.target):
                raise RequestError(400)
            for name in SINGLE_VALUED:
                if len(req.headers.get(name, [])) > 1:
                    raise RequestError(400)
            if "host" not in req.headers:
                raise RequestError(400)
            if "expect" in req.headers:
                raise RequestError(417)
            path = req.target.partition("?")[0]
            if config.strict_routes and not any(r.fullmatch(path) for r in ROUTES):
                raise RequestError(404)
            authorization = self.authorization(req.headers)
            origin = req.headers.get("origin", [None])[0]
            if (
                config.allowed_origins is not None
                and origin is not None
                and origin not in config.allowed_origins
            ):
                raise RequestError(403, "origin_not_allowed")
            if not limiter.allow(self.client_address[0]):
                raise RequestError(429)
            return authorization

        def framing(self, req: Request) -> int:
            lengths = req.headers.get("content-length", [])
            if lengths and "transfer-encoding" in req.headers:
                raise RequestError(400)  # CL + TE: classic smuggling shape
            if "transfer-encoding" in req.headers:
                raise RequestError(411)
            if not lengths:
                return 0
            if not CONTENT_LENGTH_RE.fullmatch(lengths[0]):
                raise RequestError(400)
            length = int(lengths[0])
            if length > config.max_body_bytes:
                raise RequestError(413)
            return length

        def serve(self, req: Request) -> None:
            authorization = self.validate(req)
            length = self.framing(req)
            body = self.read_body(length) if length else b""
            headers: dict[str, str] = {}
            for name in FORWARD_HEADERS:
                values = req.headers.get(name.lower())
                if values:
                    headers[name] = ", ".join(values)
            if authorization is not None:
                headers["Authorization"] = authorization
            headers["X-Forwarded-For"] = self.client_address[0]
            wants_close = "close" in ",".join(req.headers.get("connection", [])).lower()
            conn = http.client.HTTPConnection(
                config.upstream_host, config.upstream_port, timeout=config.upstream_timeout
            )
            try:
                conn.request(req.method, req.target, body=body or None, headers=headers)
                resp = conn.getresponse()
            except TimeoutError:
                conn.close()
                self.send_error_json(504, close=wants_close)
                return
            except (OSError, http.client.HTTPException):
                conn.close()
                self.send_error_json(502, close=wants_close)
                return
            try:
                self.relay(req, resp, wants_close)
            except (OSError, http.client.HTTPException):
                # upstream died mid-body: the status line is already out, so just drop the link
                self.closing = True
            finally:
                conn.close()

        def relay(self, req: Request, resp: http.client.HTTPResponse, wants_close: bool) -> None:
            length = resp.getheader("Content-Length")
            ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
            head_only = req.method == "HEAD"
            if resp.status >= 400 and not head_only and (length == "0" or ctype == "text/plain"):
                resp.read()  # gateway-generated error: empty/plain body, replace it with JSON
                keep = {
                    k: v
                    for k, v in resp.getheaders()
                    if k.lower() not in HOP_BY_HOP | STRIP_FROM_UPSTREAM | BODY_HEADERS
                }
                self.send_error_json(resp.status, extra=keep, close=wants_close)
                return
            lines = self.status_line(resp.status, resp.reason)
            for key, value in resp.getheaders():
                if key.lower() not in HOP_BY_HOP | STRIP_FROM_UPSTREAM:
                    lines.append(f"{key}: {value}")
            has_body = not head_only and resp.status not in NO_BODY_STATUSES
            if (has_body and length is None) or wants_close:
                # close-delimited upstream body (or client asked): relay until EOF, then close
                lines.append("Connection: close")
                self.closing = True
            self.send(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
            if has_body:
                while chunk := resp.read(CHUNK):
                    self.send(chunk)

    class BoundedServer(ThreadingHTTPServer):
        daemon_threads = True

        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]
            self.slots = threading.BoundedSemaphore(config.max_connections)
            self.peer_lock = threading.Lock()
            self.peer_counts: dict[str, int] = {}

        def process_request(self, request: socket.socket, client_address: tuple) -> None:
            peer = client_address[0]
            if not self.slots.acquire(blocking=False):
                self.reject_busy(request, 503)
                return
            with self.peer_lock:
                over = self.peer_counts.get(peer, 0) >= config.max_connections_per_peer
                if not over:
                    self.peer_counts[peer] = self.peer_counts.get(peer, 0) + 1
            if over:
                self.slots.release()
                self.reject_busy(request, 429)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.release(peer)
                raise

        def release(self, peer: str) -> None:
            with self.peer_lock:
                self.peer_counts[peer] -= 1
                if not self.peer_counts[peer]:
                    del self.peer_counts[peer]
            self.slots.release()

        def process_request_thread(self, request: socket.socket, client_address: tuple) -> None:
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.release(client_address[0])

        def reject_busy(self, request: socket.socket, status: int) -> None:
            body = json.dumps(error_payload(status, config.retry_after_seconds)).encode()
            head = [
                f"HTTP/1.1 {status} {REASONS[status]}",
                "Content-Type: application/json",
                f"Content-Length: {len(body)}",
                "Connection: close",
                *(f"{k}: {v}" for k, v in SECURITY_HEADERS.items()),
            ]
            if status == 429:
                head.append(f"Retry-After: {config.retry_after_seconds}")
            try:
                request.settimeout(1)
                request.sendall(("\r\n".join(head) + "\r\n\r\n").encode() + body)
            except OSError:
                pass
            self.shutdown_request(request)

        def handle_error(self, request: object, client_address: object) -> None:
            if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, TimeoutError)):
                return  # client went away / stalled: expected noise, not a bug
            super().handle_error(request, client_address)  # type: ignore[arg-type]

    return BoundedServer((host, port), Handler)


def main() -> int:
    config = EdgeConfig.from_env(os.environ)
    server = make_server(config, "0.0.0.0", int(os.environ.get("PORT", "8080")))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
