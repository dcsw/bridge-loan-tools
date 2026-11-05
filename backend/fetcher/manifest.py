"""Fetch outcomes -> WU2 ``Manifest`` rows, and the drop-dir hand-off.

Drop dir layout (host dir ``data/drop/``; the fetcher has no route to MinIO):

* fetched: ``<sha256>.pdf`` + ``<sha256>.json`` (Manifest row, doc_id = sha256 of the bytes)
* gap:     ``<sha256(url)>.json`` only (Manifest row, status "gap", gap_reason set, size 0)

Files are written via tmp + rename so ``raw_ingest`` never sees a partial file; the PDF is
renamed into place before its row so a visible row always has its PDF.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from backend.common.models import GapReason, Manifest
from backend.fetcher.adapter import (
    Fetcher,
    FetchResult,
    Limiter,
    Outcome,
    RetryPolicy,
    fetch_politely,
)

log = logging.getLogger(__name__)


def gap_reason_for(result: FetchResult) -> GapReason:
    """Map a non-OK fetch outcome to the manifest gap taxonomy."""
    outcome = result.outcome
    if outcome is Outcome.OK:
        raise ValueError("a successful fetch has no gap reason")
    if outcome is Outcome.PAID:
        return "paid"
    if outcome in (Outcome.CAPTCHA, Outcome.RATE_LIMITED, Outcome.BLOCKED_URL):
        return "blocked"
    if outcome is Outcome.HTTP_ERROR and result.status in (401, 403):
        return "blocked"
    return "failed"


def build_row(
    result: FetchResult, *, town: str, fetched_at: datetime
) -> tuple[Manifest, bytes | None]:
    if result.outcome is Outcome.OK:
        row = Manifest(
            doc_id=hashlib.sha256(result.body).hexdigest(),
            source_url=result.url,
            fetched_at=fetched_at,
            town=town,
            status="fetched",
            size_bytes=len(result.body),
        )
        return row, result.body
    row = Manifest(
        doc_id=hashlib.sha256(result.url.encode()).hexdigest(),
        source_url=result.url,
        fetched_at=fetched_at,
        town=town,
        status="gap",
        gap_reason=gap_reason_for(result),
        size_bytes=0,
    )
    return row, None


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_drop(drop_dir: Path, row: Manifest, body: bytes | None) -> None:
    drop_dir.mkdir(parents=True, exist_ok=True)
    if body is not None:
        _atomic_write(drop_dir / f"{row.doc_id}.pdf", body)
    _atomic_write(drop_dir / f"{row.doc_id}.json", row.model_dump_json().encode())


def fetch_to_drop(
    fetcher: Fetcher,
    urls: Iterable[str],
    *,
    town: str,
    drop_dir: Path,
    policy: RetryPolicy,
    limiter: Limiter | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[Manifest]:
    """Fetch every URL; each yields a manifest row (fetched or gap). Never raises per-URL."""
    rows: list[Manifest] = []
    for url in urls:
        try:
            result = fetch_politely(fetcher, url, policy=policy, limiter=limiter, sleep=sleep)
        except Exception as exc:  # adapter bug or browser crash must not stop the run
            log.error("fetcher crashed on %s: %s", url, type(exc).__name__)
            result = FetchResult(url=url, outcome=Outcome.HTTP_ERROR, detail=type(exc).__name__)
        row, body = build_row(result, town=town, fetched_at=now())
        write_drop(drop_dir, row, body)
        rows.append(row)
    return rows
