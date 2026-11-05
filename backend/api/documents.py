"""Source-PDF access: a minimal stdlib SigV4 GET against the MinIO/S3 raw bucket.

Kept dependency-free on purpose (the api image carries only fastapi/duckdb-level deps).
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote, urlparse

from backend.api.config import RawStoreConfig

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
CHUNK = 64 * 1024
TIMEOUT_SECONDS = 10


class ObjectNotFound(Exception):
    pass


class DocumentStoreError(Exception):
    """The object store answered unexpectedly or could not be reached."""


@dataclass
class ObjectStream:
    chunks: Iterator[bytes]
    content_length: int | None
    close: Callable[[], None]


class ObjectFetcher(Protocol):
    def open(self, key: str) -> ObjectStream: ...


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sigv4_headers(
    *,
    method: str,
    host: str,
    path: str,
    region: str,
    key_id: str,
    secret: str,
    now: datetime,
    extra_headers: dict[str, str] | None = None,
    session_token: str | None = None,
) -> dict[str, str]:
    """AWS Signature V4 for an S3 request with an empty body and no query string.

    ``path`` must already be percent-encoded. Returns the headers to send (``Host`` excluded).
    """
    amz_date = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    headers = {
        "host": host,
        "x-amz-content-sha256": EMPTY_SHA256,
        "x-amz-date": amz_date,
        **{k.lower(): v.strip() for k, v in (extra_headers or {}).items()},
    }
    if session_token:
        headers["x-amz-security-token"] = session_token
    names = sorted(headers)
    canonical_headers = "".join(f"{n}:{headers[n]}\n" for n in names)
    signed = ";".join(names)
    canonical = "\n".join([method, path, "", canonical_headers, signed, EMPTY_SHA256])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()]
    )
    k = _hmac(("AWS4" + secret).encode("utf-8"), day)
    for part in (region, "s3", "aws4_request"):
        k = _hmac(k, part)
    signature = hmac.new(k, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    out = {n: v for n, v in headers.items() if n != "host"}
    out["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, SignedHeaders={signed}, "
        f"Signature={signature}"
    )
    return out


def validate_key(key: str) -> None:
    if (
        not key
        or key.startswith("/")
        or "\\" in key
        or "//" in key
        or any(part in ("", ".", "..") for part in key.split("/"))
    ):
        raise ValueError(f"unsafe object key: {key!r}")


class S3ObjectFetcher:
    """Streams one object from the configured raw bucket (path-style addressing)."""

    def __init__(self, config: RawStoreConfig) -> None:
        self._cfg = config
        parsed = urlparse(config.endpoint)
        self._https = parsed.scheme == "https"
        self._host = parsed.netloc

    def open(self, key: str) -> ObjectStream:
        validate_key(key)
        path = "/" + quote(f"{self._cfg.bucket}/{key}", safe="/-_.~")
        headers = sigv4_headers(
            method="GET",
            host=self._host,
            path=path,
            region=self._cfg.region,
            key_id=self._cfg.key_id,
            secret=self._cfg.secret,
            now=datetime.now(UTC),
            session_token=self._cfg.session_token,
        )
        cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
        conn = cls(self._host, timeout=TIMEOUT_SECONDS)
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise DocumentStoreError("object store unreachable") from exc
        if resp.status == 404:
            resp.read()
            conn.close()
            raise ObjectNotFound(key)
        if resp.status != 200:
            resp.read()
            conn.close()
            raise DocumentStoreError(f"object store returned {resp.status}")
        length = resp.getheader("Content-Length")

        def chunks() -> Iterator[bytes]:
            while block := resp.read(CHUNK):
                yield block

        return ObjectStream(
            chunks=chunks(),
            content_length=int(length) if length and length.isdigit() else None,
            close=conn.close,
        )
