"""Locked-down DuckDB connections (design 3.1/8, ADR-0006).

Order matters: extensions are LOADed and the S3 secret is created BEFORE
``enable_external_access=false`` and ``lock_configuration=true``; afterwards the connection
can only read the exact files in ``allowed_paths`` and cannot be reconfigured.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

import duckdb


@dataclass(frozen=True)
class S3Credentials:
    """Credentials for one snapshot path (list and detail get separate instances)."""

    key_id: str
    secret: str
    endpoint: str  # host:port, no scheme (DuckDB S3 secret syntax)
    scope: str  # s3:// URI prefix the secret applies to
    region: str = "us-east-1"
    use_ssl: bool = False
    url_style: str = "path"
    session_token: str | None = None


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def is_remote(uri: str) -> bool:
    return urlparse(uri).scheme in {"s3", "http", "https"}


def open_locked(
    allowed_paths: list[str], s3: S3Credentials | None = None
) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB limited to ``allowed_paths`` (exact files)."""
    if not allowed_paths:
        raise ValueError("allowed_paths must not be empty")
    conn = duckdb.connect(
        ":memory:",
        config={"autoinstall_known_extensions": "false", "autoload_known_extensions": "false"},
    )
    try:
        if any(is_remote(p) for p in allowed_paths):
            if s3 is None:
                raise ValueError("S3 credentials are required for remote snapshot paths")
            conn.execute("LOAD httpfs")  # pre-installed at image build time; never INSTALLed here
            parts = [
                "TYPE S3",
                f"KEY_ID {_q(s3.key_id)}",
                f"SECRET {_q(s3.secret)}",
                f"ENDPOINT {_q(s3.endpoint)}",
                f"REGION {_q(s3.region)}",
                f"URL_STYLE {_q(s3.url_style)}",
                f"USE_SSL {'true' if s3.use_ssl else 'false'}",
                f"SCOPE {_q(s3.scope)}",
            ]
            if s3.session_token:
                parts.append(f"SESSION_TOKEN {_q(s3.session_token)}")
            conn.execute(f"CREATE SECRET ({', '.join(parts)})")
        conn.execute(f"SET allowed_paths = [{', '.join(_q(p) for p in allowed_paths)}]")
        conn.execute("SET enable_external_access = false")
        conn.execute("SET lock_configuration = true")
    except Exception:
        conn.close()
        raise
    return conn


def safe_join(base: str, relative: str) -> str:
    """Join a manifest-relative path to the snapshot base; reject escapes and absolute paths."""
    p = PurePosixPath(relative)
    if p.is_absolute() or ".." in p.parts or "://" in relative or "\\" in relative or not p.parts:
        raise ValueError(f"unsafe snapshot path: {relative!r}")
    return base.rstrip("/") + "/" + p.as_posix()
