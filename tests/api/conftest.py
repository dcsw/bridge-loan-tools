"""Shared fixtures for the WU7 API tests.

Tests marked ``stack`` need the running compose MinIO and are deselected (with a visible
notice) unless ``RUN_STACK=1``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.common.snapshot import (
    DETAIL_FILE,
    LIST_FILE,
    MANIFEST_FILE,
    build_snapshot,
    synthetic_rows,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_SNAPSHOT = ROOT / "tests" / "fixtures" / "snapshots" / "single"


def pytest_configure(config):
    config.addinivalue_line("markers", "stack: needs the compose core stack (RUN_STACK=1)")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_STACK") == "1":
        return
    deselected = [i for i in items if i.get_closest_marker("stack")]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = [i for i in items if not i.get_closest_marker("stack")]
        print(f"\n[api] {len(deselected)} stack test(s) deselected; set RUN_STACK=1 to run them")


@pytest.fixture
def snapshot_dir(tmp_path) -> Path:
    """A writable copy of the committed single-row fixture snapshot."""
    dest = tmp_path / "snapshot"
    shutil.copytree(FIXTURE_SNAPSHOT, dest)
    return dest


GATEWAY_SECRET = "gateway-shared-secret-for-tests-0123456789"
KEY_ID = "key-alpha"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_api_snapshot(out: Path, n: int, seed: int = 7) -> list:
    """Synthetic snapshot with varied bridge scores, per-field provenance and coverage.

    The WU2 generator emits a constant placeholder score (100); the API tests need a spread of
    scores, so the list file's score column is rewritten and the manifest hashes recomputed.
    Returns the rows (for expected-value assertions).
    """
    rows = synthetic_rows(n, seed)
    build_snapshot(rows, out, "2026-01-01T00:00:00+00:00")
    list_tbl = pq.read_table(out / LIST_FILE)
    scores = [(i * 37) % 101 for i in range(n)]
    list_tbl = list_tbl.set_column(
        list_tbl.schema.get_field_index("bridge_score"),
        "bridge_score",
        pa.array(scores, type=pa.int32()),
    )
    pq.write_table(list_tbl, out / LIST_FILE, compression="snappy")
    detail = pq.read_table(out / DETAIL_FILE)
    fields = [
        json.dumps(
            {
                "principal": {
                    "value": "100000.00",
                    "source_extractor": "docling",
                    "confidence": 0.9,
                    "disagreement": False,
                },
                "recorded_date": {
                    "value": "2024-01-02",
                    "source_extractor": "pymupdf",
                    "confidence": 0.5,
                    "disagreement": True,
                },
            }
        )
    ] * n
    detail = detail.append_column("fields_json", pa.array(fields, type=pa.string()))
    pq.write_table(detail, out / DETAIL_FILE, compression="snappy")
    manifest = json.loads((out / MANIFEST_FILE).read_text())
    list_sha = _sha(out / LIST_FILE)
    manifest["snapshot_id"] = list_sha[:16]
    manifest["files"]["list"]["sha256"] = list_sha
    manifest["files"]["detail"]["sha256"] = _sha(out / DETAIL_FILE)
    manifest["coverage"] = {
        "mattapoisett": {
            "documents_fetched": n,
            "gaps": {"paid": 2, "blocked": 1, "failed": 0},
            "last_ingested_at": "2026-01-01T00:00:00+00:00",
        }
    }
    (out / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return rows


@pytest.fixture(scope="session")
def small_snapshot_master(tmp_path_factory):
    d = tmp_path_factory.mktemp("small") / "snap"
    rows = build_api_snapshot(d, 40)
    return d, rows


@pytest.fixture(scope="session")
def big_snapshot_master(tmp_path_factory):
    d = tmp_path_factory.mktemp("big") / "snap"
    rows = build_api_snapshot(d, 5000, seed=11)
    return d, rows


@pytest.fixture
def small_snapshot(small_snapshot_master, tmp_path):
    src, rows = small_snapshot_master
    dest = tmp_path / "snap"
    shutil.copytree(src, dest)
    return dest, rows


AUTH_HEADERS = {"X-Gateway-Secret": GATEWAY_SECRET, "X-Key-Id": KEY_ID}


@pytest.fixture
def make_client(tmp_path):
    """Factory: TestClient over create_app(); authenticated as the gateway unless auth=False."""
    from fastapi.testclient import TestClient

    from backend.api.config import Settings
    from backend.api.main import create_app

    created = []

    def _make(snapshot_base, *, auth=True, app_kwargs=None, **settings_kwargs):
        settings = Settings(
            gateway_secret=GATEWAY_SECRET,
            snapshot_base=str(snapshot_base),
            audit_log_path=str(tmp_path / "audit" / "audit.log"),
            **settings_kwargs,
        )
        app = create_app(settings, **(app_kwargs or {}))
        client = TestClient(
            app, raise_server_exceptions=False, headers=AUTH_HEADERS if auth else None
        )
        created.append(client)
        return client

    yield _make
    for c in created:
        c.close()
