"""Publish: materialize ``semantic_loans_v1`` into versioned Parquet and flip ``manifest.json``.

    python -m backend.ingest.publish (--local-dir DIR | --bucket snapshots) [--manifests-dir DIR]

Manual and explicit (``make publish``), never run automatically. Steps:

1. Create the semantic views from ``sql/semantic_loans_v1.sql`` on a fresh DuckDB connection over
   the silver tables and read the PII-free list (``semantic_loans_v1``, sorted by town) and the
   borrower detail (``semantic_loans_v1_borrower``) from them.
2. Write, under the versioned key ``versions/<snapshot_id>/``: ``loans_list.parquet``,
   ``loans_detail.parquet`` and a self-contained ``manifest.json`` (validates with
   ``backend.common.snapshot.validate_snapshot``).
3. LAST, put the root ``manifest.json`` pointer (paths prefixed with the version key). A crash at
   any earlier point leaves the previous root manifest, and the snapshot it names, untouched.

Detail rows carry ``fields_json`` (per-field value/confidence/disagreement/source_extractor) and the
root manifest carries ``coverage`` (per town: documents fetched, gaps by reason from the WU4
manifest rows, last_ingested_at) for the API.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol

from backend.common.models import Manifest
from backend.common.snapshot import (
    _DETAIL_SCHEMA,
    _LIST_SCHEMA,
    DETAIL_FILE,
    LIST_FILE,
    MANIFEST_FILE,
    SCHEMA_VERSION,
)
from backend.ingest import views
from backend.ingest.unify import normalize_town

if TYPE_CHECKING:
    import pyarrow as pa

VERSIONS_PREFIX = "versions"
GAP_REASONS = ("paid", "blocked", "failed")


# -- object stores -----------------------------------------------------------------------------


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes | None: ...


def _safe_key(key: str) -> PurePosixPath:
    p = PurePosixPath(key)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ValueError(f"unsafe object key: {key!r}")
    return p


class LocalObjectStore:
    """Directory-backed store; ``put`` is write-to-temp + atomic rename."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def put(self, key: str, data: bytes) -> None:
        path = self.root / _safe_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def get(self, key: str) -> bytes | None:
        path = self.root / _safe_key(key)
        return path.read_bytes() if path.exists() else None


class S3ObjectStore:
    """S3/MinIO bucket store (boto3-style client). A single ``put_object`` is atomic."""

    def __init__(self, client: Any, bucket: str, prefix: str = "") -> None:
        self.client, self.bucket, self.prefix = client, bucket, prefix.strip("/")

    def _key(self, key: str) -> str:
        key = str(_safe_key(key))
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data)

    def get(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None


# -- silver sources ----------------------------------------------------------------------------


class SilverSource(Protocol):
    def loans(self) -> pa.Table: ...
    def instruments(self) -> pa.Table: ...


@dataclass(frozen=True)
class ArrowSilverSource:
    _loans: pa.Table
    _instruments: pa.Table

    def loans(self) -> pa.Table:
        return self._loans

    def instruments(self) -> pa.Table:
        return self._instruments


class IcebergSilverSource:  # pragma: no cover - exercised by the RUN_STACK=1 live test
    """Reads ``silver.loans`` / ``silver.instruments`` through pyiceberg (host side)."""

    def __init__(self, uri: str, warehouse: str, s3_endpoint: str) -> None:
        from pyiceberg.catalog import load_catalog
        from pyiceberg.io import load_file_io

        self._catalog = load_catalog(
            "lakekeeper",
            **{
                "type": "rest",
                "uri": uri,
                "warehouse": warehouse,
                "s3.endpoint": s3_endpoint,
                "s3.path-style-access": "true",
                "s3.access-key-id": os.environ.get("MINIO_ROOT_USER", ""),
                "s3.secret-access-key": os.environ.get("MINIO_ROOT_PASSWORD", ""),
                "s3.region": "us-east-1",
            },
        )
        self._endpoint = s3_endpoint
        self._load_file_io = load_file_io

    def _scan(self, name: str, columns: Sequence[views.Column]) -> pa.Table:
        table = self._catalog.load_table(f"{views.NAMESPACE}.{name}")
        # Lakekeeper reports its in-network MinIO endpoint; rebuild the FileIO for the host.
        props = {**table.io.properties, "s3.endpoint": self._endpoint}
        table.io = self._load_file_io(props, table.metadata_location)
        arrow = table.scan().to_arrow()
        return views.table_from_rows(arrow.to_pylist(), columns)

    def loans(self) -> pa.Table:
        return self._scan(views.LOANS_TABLE, views.LOAN_COLUMNS)

    def instruments(self) -> pa.Table:
        return self._scan(views.INSTRUMENTS_TABLE, views.INSTRUMENT_COLUMNS)


# -- coverage ----------------------------------------------------------------------------------


def read_manifests_s3(client: Any, bucket: str, prefix: str = "manifest/") -> list[Manifest]:
    """WU4 manifest rows (``manifest/<doc_id>.json``) from the MinIO ``raw`` bucket."""
    rows: list[Manifest] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            body = client.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            try:
                rows.append(Manifest.model_validate_json(body))
            except ValueError:
                continue  # a foreign/invalid object must not break publishing
    return rows


def export_manifests(rows: Sequence[Manifest], dest: Path) -> int:
    """Write manifest rows as ``<doc_id>.json`` (the silver container mounts this read-only)."""
    dest.mkdir(parents=True, exist_ok=True)
    for row in rows:
        (dest / f"{row.doc_id}.json").write_text(row.model_dump_json(), encoding="utf-8")
    return len(rows)


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_coverage(
    manifests: Sequence[Manifest] | None, ingested: dict[str, datetime]
) -> dict[str, dict[str, Any]] | None:
    """Per-town coverage; None when no manifest rows are available (API then derives it)."""
    if not manifests:
        return None
    fetched: Counter[str] = Counter()
    gaps: dict[str, Counter[str]] = {}
    for row in manifests:
        town = normalize_town(row.town)
        gaps.setdefault(town, Counter())
        if row.status == "fetched":
            fetched[town] += 1
        elif row.gap_reason is not None:
            gaps[town][row.gap_reason] += 1
    coverage: dict[str, dict[str, Any]] = {}
    for town in sorted({*gaps, *ingested}):
        last = ingested.get(town)
        coverage[town] = {
            "documents_fetched": fetched[town],
            "gaps": {reason: gaps.get(town, Counter())[reason] for reason in GAP_REASONS},
            "last_ingested_at": _iso(last) if last else None,
        }
    return coverage


# -- publish -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishResult:
    snapshot_id: str
    row_count: int
    key_prefix: str


def _parquet_bytes(table: pa.Table) -> bytes:
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


def _file_ref(path: str, data: bytes, rows: int) -> dict[str, Any]:
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "rows": rows}


def _dump(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()


def publish(
    source: SilverSource,
    store: ObjectStore,
    *,
    manifests: Sequence[Manifest] | None = None,
    now: datetime | None = None,
) -> PublishResult:
    import duckdb
    import pyarrow as pa

    now = now or datetime.now(UTC)
    con = duckdb.connect()
    try:
        views.register_silver(con, source.loans(), source.instruments())
        views.create_semantic_views(con)
        list_rows = con.sql(
            f"SELECT * FROM {views.VIEW_NAME} ORDER BY town, bridge_score DESC, loan_id"
        ).to_arrow_table()
        detail_rows = con.sql(
            f"SELECT * FROM {views.BORROWER_VIEW_NAME} ORDER BY loan_id"
        ).to_arrow_table()
    finally:
        con.close()
    if list_rows.num_rows == 0:
        raise ValueError("no loans to publish")

    list_tbl = pa.Table.from_pylist(list_rows.to_pylist(), schema=_LIST_SCHEMA)
    detail_schema = _DETAIL_SCHEMA.append(pa.field("fields_json", pa.string()))
    detail_tbl = pa.Table.from_pylist(detail_rows.to_pylist(), schema=detail_schema)
    list_bytes, detail_bytes = _parquet_bytes(list_tbl), _parquet_bytes(detail_tbl)

    snapshot_id = (
        f"{now.astimezone(UTC):%Y%m%dT%H%M%SZ}-{hashlib.sha256(list_bytes).hexdigest()[:12]}"
    )
    prefix = f"{VERSIONS_PREFIX}/{snapshot_id}"
    towns = sorted(set(list_tbl.column("town").to_pylist()))
    versions = sorted(set(list_tbl.column("classifier_version").to_pylist()))
    ingested: dict[str, datetime] = {}
    for town, ts in zip(
        list_tbl.column("town").to_pylist(),
        list_tbl.column("last_ingested_at").to_pylist(),
        strict=True,
    ):
        ingested[town] = max(ts, ingested.get(town, ts))

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "generated_at": _iso(now),
        "classifier_version": versions[0] if len(versions) == 1 else "mixed",
        "town": ",".join(towns),
        "row_count": list_tbl.num_rows,
        "files": {
            "list": _file_ref(LIST_FILE, list_bytes, list_tbl.num_rows),
            "detail": _file_ref(DETAIL_FILE, detail_bytes, detail_tbl.num_rows),
        },
    }
    coverage = build_coverage(manifests, ingested)
    if coverage is not None:
        manifest["coverage"] = coverage

    # Data first, pointer last: nothing the previous manifest references is ever modified.
    store.put(f"{prefix}/{LIST_FILE}", list_bytes)
    store.put(f"{prefix}/{DETAIL_FILE}", detail_bytes)
    store.put(f"{prefix}/{MANIFEST_FILE}", _dump(manifest))
    root = json.loads(json.dumps(manifest))
    for ref in root["files"].values():
        ref["path"] = f"{prefix}/{ref['path']}"
    store.put(MANIFEST_FILE, _dump(root))
    return PublishResult(snapshot_id, list_tbl.num_rows, prefix)


# -- CLI ---------------------------------------------------------------------------------------


def _s3_client(args: argparse.Namespace) -> Any:
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=args.s3_endpoint,
        aws_access_key_id=os.environ.get("MINIO_ROOT_USER", ""),
        aws_secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", ""),
        region_name="us-east-1",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.ingest.publish", description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--local-dir", type=Path, help="publish into a local directory")
    target.add_argument("--bucket", help="publish into this MinIO/S3 bucket (e.g. snapshots)")
    target.add_argument(
        "--export-manifests", type=Path, help="copy raw-bucket manifest rows into this directory"
    )
    parser.add_argument("--prefix", default="")
    parser.add_argument("--manifests-dir", type=Path, help="local WU4 manifest rows (coverage)")
    parser.add_argument("--raw-bucket", default="raw", help="read coverage rows from this bucket")
    parser.add_argument("--uri", default="http://127.0.0.1:8181/catalog")
    parser.add_argument("--warehouse", default="bridgeloan")
    parser.add_argument("--s3-endpoint", default="http://127.0.0.1:9000")
    args = parser.parse_args(argv)
    if args.export_manifests is None and args.local_dir is None and args.bucket is None:
        parser.error("one of --local-dir, --bucket, --export-manifests is required")

    if args.export_manifests is not None:
        rows = read_manifests_s3(_s3_client(args), args.raw_bucket)
        print(f"exported {export_manifests(rows, args.export_manifests)} manifest row(s)")
        return 0

    manifests: list[Manifest] | None = None
    client = None
    if args.local_dir is not None:
        store: ObjectStore = LocalObjectStore(args.local_dir)
    else:
        client = _s3_client(args)
        store = S3ObjectStore(client, args.bucket, args.prefix)
    if args.manifests_dir is not None:
        from backend.ingest.silver_job import read_manifest_dir

        manifests = read_manifest_dir(args.manifests_dir)
    elif client is not None:
        manifests = read_manifests_s3(client, args.raw_bucket)

    source = IcebergSilverSource(args.uri, args.warehouse, args.s3_endpoint)
    result = publish(source, store, manifests=manifests)
    print(f"published snapshot_id={result.snapshot_id} rows={result.row_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
