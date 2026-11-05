"""Snapshot expiry / compaction script, tested on a local SQL catalog table with 3 snapshots."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pyarrow as pa
import pytest
from pyiceberg.catalog.sql import SqlCatalog

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "expire_snapshots", ROOT / "scripts" / "maintenance" / "expire_snapshots.py"
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


@pytest.fixture
def table(tmp_path):
    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/cat.db", warehouse=f"file://{tmp_path}/wh")
    catalog.create_namespace("ns")
    schema = pa.schema([pa.field("id", pa.int64(), nullable=False)])
    tbl = catalog.create_table("ns.t", schema=schema)
    for i in range(3):
        tbl.append(pa.table({"id": pa.array([i], pa.int64())}, schema=schema))
    tbl = catalog.load_table("ns.t")
    assert len(tbl.snapshots()) == 3
    return tbl


def test_expire_retains_last_n_and_keeps_data(table):
    expired = mod.expire_snapshots(table, retain_last=1)
    assert len(expired) == 2
    reloaded = table.catalog.load_table("ns.t")
    assert len(reloaded.snapshots()) == 1
    assert reloaded.current_snapshot().snapshot_id not in expired
    assert sorted(reloaded.scan().to_arrow()["id"].to_pylist()) == [0, 1, 2]


def test_expire_retain_two(table):
    assert len(mod.expire_snapshots(table, retain_last=2)) == 1
    assert len(table.catalog.load_table("ns.t").snapshots()) == 2


def test_expire_noop_when_fewer_snapshots_than_retain(table):
    assert mod.expire_snapshots(table, retain_last=5) == []
    assert len(table.catalog.load_table("ns.t").snapshots()) == 3


def test_expire_rejects_retain_below_one(table):
    with pytest.raises(ValueError):
        mod.expire_snapshots(table, retain_last=0)


def test_compact_rewrites_to_single_data_file(table):
    assert len(list(table.scan().plan_files())) == 3
    mod.compact(table)
    reloaded = table.catalog.load_table("ns.t")
    assert len(list(reloaded.scan().plan_files())) == 1
    assert sorted(reloaded.scan().to_arrow()["id"].to_pylist()) == [0, 1, 2]


def test_with_endpoint_overrides_server_supplied_s3_endpoint(table):
    table.io.properties["s3.endpoint"] = "http://minio:9000"
    out = mod.with_endpoint(table, "http://127.0.0.1:9000")
    assert out.io.properties["s3.endpoint"] == "http://127.0.0.1:9000"
    assert out.io.properties["s3.path-style-access"] == "true"


def test_main_runs_against_catalog(table, monkeypatch, capsys):
    monkeypatch.setattr(mod, "load_catalog_from_args", lambda args: table.catalog)
    monkeypatch.setattr(mod, "with_endpoint", lambda t, endpoint: t)
    rc = mod.main(["--table", "ns.t", "--retain-last", "1", "--compact"])
    assert rc == 0
    out = capsys.readouterr().out
    # overwrite-based compaction adds delete+append snapshots; expiry keeps only the newest
    assert "expired 4" in out
    assert len(table.catalog.load_table("ns.t").snapshots()) == 1
