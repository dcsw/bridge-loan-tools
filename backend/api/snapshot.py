"""Published-snapshot read path (design 3.3).

The API reads the manifest through a locked DuckDB, validates it, then opens TWO separately
locked connections: the list connection can read only the PII-free list file (loaded into an
in-memory table) and the detail connection can read only the borrower detail file. The manifest
is re-read on TTL and, while no snapshot is loaded (503 state), on every request after a short
throttle interval.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import duckdb
from pydantic import BaseModel, Field

from backend.api.config import Settings
from backend.api.lockdown import open_locked, safe_join
from backend.api.logging_setup import API_LOGGER

MANIFEST_FILE = "manifest.json"
SUPPORTED_SCHEMA_VERSION = 1
log = logging.getLogger(API_LOGGER)


class SnapshotUnavailable(Exception):
    """No readable snapshot (missing, malformed, or failed integrity checks)."""


class FileRef(BaseModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rows: int = Field(ge=0)


class ManifestFiles(BaseModel):
    list: FileRef
    detail: FileRef


class CoverageGaps(BaseModel):
    paid: int = Field(ge=0)
    blocked: int = Field(ge=0)
    failed: int = Field(ge=0)


class CoverageEntry(BaseModel):
    documents_fetched: int = Field(ge=0)
    gaps: CoverageGaps | None = None
    last_ingested_at: str | None = None


class SnapshotManifest(BaseModel):
    schema_version: Literal[1]
    snapshot_id: str = Field(pattern=r"^[0-9A-Za-z_-]{1,64}$")
    generated_at: str
    classifier_version: str
    town: str
    row_count: int = Field(ge=0)
    files: ManifestFiles
    coverage: dict[str, CoverageEntry] | None = None


@dataclass(frozen=True)
class SnapshotState:
    manifest: SnapshotManifest
    list_conn: duckdb.DuckDBPyConnection  # table `loans`; no access to the detail file
    detail_conn: duckdb.DuckDBPyConnection  # reads only detail_path
    detail_path: str

    @property
    def snapshot_id(self) -> str:
        return self.manifest.snapshot_id

    @property
    def generated_at(self) -> str:
        return self.manifest.generated_at


class SnapshotStore:
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic) -> None:
        self._settings = settings
        self._clock = clock
        self._lock = threading.Lock()
        self._state: SnapshotState | None = None
        self._loaded_at = 0.0
        self._last_attempt: float | None = None
        self._last_error = "no snapshot loaded"

    # -- public ------------------------------------------------------------------------------

    def current(self) -> SnapshotState:
        with self._lock:
            now = self._clock()
            state = self._state
            ttl = self._settings.manifest_ttl_seconds
            if state is not None and now - self._loaded_at < ttl:
                return state
            interval = self._settings.reload_min_interval_seconds
            if (
                state is None
                and self._last_attempt is not None
                and now - self._last_attempt < interval
            ):
                raise SnapshotUnavailable(self._last_error)
            return self._refresh(now)

    def is_ready(self) -> bool:
        try:
            self.current()
        except SnapshotUnavailable:
            return False
        return True

    def invalidate(self) -> None:
        """Force the next ``current()`` to re-read the manifest."""
        with self._lock:
            self._loaded_at = float("-inf")
            self._last_attempt = None

    # -- internals ---------------------------------------------------------------------------

    def _refresh(self, now: float) -> SnapshotState:
        self._last_attempt = now
        try:
            manifest = self._read_manifest()
            old = self._state
            if old is not None and old.snapshot_id == manifest.snapshot_id:
                self._loaded_at = now
                return old
            new = self._build_state(manifest)
        except Exception as exc:  # every failure mode (I/O, JSON, DuckDB, validation) = unavailable
            self._last_error = f"{type(exc).__name__}"
            log.warning("snapshot refresh failed: %s", type(exc).__name__)
            if self._state is not None:
                # keep serving the previous snapshot; retry after the throttle interval
                ttl = self._settings.manifest_ttl_seconds
                self._loaded_at = now - ttl + self._settings.reload_min_interval_seconds
                return self._state
            raise SnapshotUnavailable(self._last_error) from exc
        self._state = new
        self._loaded_at = now
        return new

    def _read_manifest(self) -> SnapshotManifest:
        path = safe_join(self._settings.snapshot_base, MANIFEST_FILE)
        conn = open_locked([path], self._settings.list_s3)
        try:
            row = conn.execute("select content from read_text(?)", [path]).fetchone()
        finally:
            conn.close()
        if row is None:
            raise SnapshotUnavailable("manifest not found")
        return SnapshotManifest.model_validate_json(row[0])

    def _build_state(self, manifest: SnapshotManifest) -> SnapshotState:
        base = self._settings.snapshot_base
        list_path = safe_join(base, manifest.files.list.path)
        detail_path = safe_join(base, manifest.files.detail.path)
        list_conn = open_locked([list_path], self._settings.list_s3)
        try:
            blob = list_conn.execute("select content from read_blob(?)", [list_path]).fetchone()
            if (
                blob is None
                or hashlib.sha256(bytes(blob[0])).hexdigest() != manifest.files.list.sha256
            ):
                raise SnapshotUnavailable("list file sha256 does not match manifest")
            list_conn.execute("create table loans as select * from read_parquet(?)", [list_path])
            count = list_conn.execute("select count(*) from loans").fetchone()
            if count is None or count[0] != manifest.files.list.rows:
                raise SnapshotUnavailable("list row count does not match manifest")
        except Exception:
            list_conn.close()
            raise
        try:
            detail_conn = open_locked([detail_path], self._settings.detail_s3)
        except Exception:
            list_conn.close()
            raise
        return SnapshotState(manifest, list_conn, detail_conn, detail_path)
