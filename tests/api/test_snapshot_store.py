"""Snapshot store: manifest TTL / 503 re-read, integrity, separate list/detail connections."""

from __future__ import annotations

import json
import shutil

import duckdb
import pyarrow.parquet as pq
import pytest

from backend.api.config import Settings
from backend.api.snapshot import SnapshotStore, SnapshotUnavailable
from backend.common.snapshot import DETAIL_FILE, LIST_FILE, MANIFEST_FILE
from tests.api.conftest import GATEWAY_SECRET, build_api_snapshot


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _store(snap, tmp_path, clock=None, **kw):
    settings = Settings(
        gateway_secret=GATEWAY_SECRET,
        snapshot_base=str(snap),
        audit_log_path=str(tmp_path / "a.log"),
        manifest_ttl_seconds=60,
        reload_min_interval_seconds=1,
        **kw,
    )
    return SnapshotStore(settings, clock=clock or Clock())


def test_missing_manifest_is_unavailable(tmp_path):
    store = _store(tmp_path / "nope", tmp_path)
    with pytest.raises(SnapshotUnavailable):
        store.current()
    assert store.is_ready() is False


def test_loads_snapshot_and_reports_manifest(small_snapshot, tmp_path):
    snap, _ = small_snapshot
    state = _store(snap, tmp_path).current()
    manifest = json.loads((snap / MANIFEST_FILE).read_text())
    assert state.snapshot_id == manifest["snapshot_id"]
    assert state.generated_at == manifest["generated_at"]


def test_manifest_not_reread_within_ttl(small_snapshot, tmp_path):
    snap, _ = small_snapshot
    clock = Clock()
    store = _store(snap, tmp_path, clock)
    first = store.current()
    (snap / MANIFEST_FILE).unlink()
    clock.now += 30
    assert store.current() is first


def test_manifest_reread_after_ttl_picks_up_new_snapshot(small_snapshot, tmp_path):
    snap, _ = small_snapshot
    clock = Clock()
    store = _store(snap, tmp_path, clock)
    old = store.current()
    build_api_snapshot(snap, 10, seed=99)
    clock.now += 61
    new = store.current()
    assert new.snapshot_id != old.snapshot_id
    count = new.list_conn.execute("select count(*) from loans").fetchone()
    assert count == (10,)


def test_ttl_refresh_with_unchanged_manifest_keeps_state(small_snapshot, tmp_path):
    snap, _ = small_snapshot
    clock = Clock()
    store = _store(snap, tmp_path, clock)
    first = store.current()
    clock.now += 61
    assert store.current() is first


def test_refresh_failure_keeps_serving_previous_snapshot(small_snapshot, tmp_path):
    snap, _ = small_snapshot
    clock = Clock()
    store = _store(snap, tmp_path, clock)
    first = store.current()
    (snap / MANIFEST_FILE).write_text("{not json")
    clock.now += 61
    assert store.current() is first


def test_503_state_rereads_manifest_without_waiting_for_ttl(tmp_path, small_snapshot):
    master, _ = small_snapshot
    snap = tmp_path / "late"
    clock = Clock()
    store = _store(snap, tmp_path, clock)
    with pytest.raises(SnapshotUnavailable):
        store.current()
    shutil.copytree(master, snap)
    # throttled: an immediate retry does not hit storage again
    with pytest.raises(SnapshotUnavailable):
        store.current()
    clock.now += 2  # > reload_min_interval, << ttl
    assert store.current().snapshot_id
    assert store.is_ready() is True


def test_invalidate_forces_reload(small_snapshot, tmp_path):
    snap, _ = small_snapshot
    store = _store(snap, tmp_path)
    first = store.current()
    build_api_snapshot(snap, 12, seed=5)
    store.invalidate()
    assert store.current().snapshot_id != first.snapshot_id


def test_sha_mismatch_rejected(small_snapshot, tmp_path):
    snap, _ = small_snapshot
    manifest = json.loads((snap / MANIFEST_FILE).read_text())
    manifest["files"]["list"]["sha256"] = "0" * 64
    (snap / MANIFEST_FILE).write_text(json.dumps(manifest))
    with pytest.raises(SnapshotUnavailable):
        _store(snap, tmp_path).current()


def test_row_count_mismatch_rejected(small_snapshot, tmp_path):
    snap, _ = small_snapshot
    manifest = json.loads((snap / MANIFEST_FILE).read_text())
    manifest["files"]["list"]["rows"] = 999
    (snap / MANIFEST_FILE).write_text(json.dumps(manifest))
    with pytest.raises(SnapshotUnavailable):
        _store(snap, tmp_path).current()


@pytest.mark.parametrize("evil", ["../x.parquet", "/etc/passwd", "s3://other/x", "a/../../b"])
def test_manifest_paths_cannot_escape_snapshot_base(small_snapshot, tmp_path, evil):
    snap, _ = small_snapshot
    manifest = json.loads((snap / MANIFEST_FILE).read_text())
    manifest["files"]["list"]["path"] = evil
    (snap / MANIFEST_FILE).write_text(json.dumps(manifest))
    with pytest.raises(SnapshotUnavailable):
        _store(snap, tmp_path).current()


def test_unsupported_schema_version_rejected(small_snapshot, tmp_path):
    snap, _ = small_snapshot
    manifest = json.loads((snap / MANIFEST_FILE).read_text())
    manifest["schema_version"] = 99
    (snap / MANIFEST_FILE).write_text(json.dumps(manifest))
    with pytest.raises(SnapshotUnavailable):
        _store(snap, tmp_path).current()


def test_list_connection_cannot_read_borrower_file(small_snapshot, tmp_path):
    snap, _ = small_snapshot
    state = _store(snap, tmp_path).current()
    detail_path = str(snap / DETAIL_FILE)
    with pytest.raises(duckdb.Error):
        state.list_conn.execute("select borrower from read_parquet(?)", [detail_path])
    with pytest.raises(duckdb.Error):
        state.list_conn.cursor().execute("select borrower from read_parquet(?)", [detail_path])
    cols = [c[0] for c in state.list_conn.execute("select * from loans limit 0").description]
    assert "borrower" not in cols


def test_detail_connection_cannot_read_list_file_or_arbitrary_files(small_snapshot, tmp_path):
    snap, _ = small_snapshot
    state = _store(snap, tmp_path).current()
    with pytest.raises(duckdb.Error):
        state.detail_conn.execute("select * from read_parquet(?)", [str(snap / LIST_FILE)])
    with pytest.raises(duckdb.Error):
        state.detail_conn.execute("select * from read_csv('/etc/hostname')")
    rows = state.detail_conn.execute(
        "select count(*) from read_parquet(?)", [str(snap / DETAIL_FILE)]
    ).fetchone()
    assert rows == (40,)


def test_list_and_detail_use_separate_credentials(small_snapshot, tmp_path):
    from backend.api.lockdown import S3Credentials

    snap, _ = small_snapshot
    settings = Settings(
        gateway_secret=GATEWAY_SECRET,
        snapshot_base=str(snap),
        audit_log_path=str(tmp_path / "a.log"),
        list_s3=S3Credentials("list-key", "list-secret", "h:1", "s3://b/list/"),
        detail_s3=S3Credentials("detail-key", "detail-secret", "h:1", "s3://b/detail/"),
    )
    assert settings.list_s3 != settings.detail_s3
    # local bases ignore S3 creds but the settings keep them apart (env prefixes differ)
    env = {
        "GATEWAY_SECRET": GATEWAY_SECRET,
        "SNAPSHOT_BASE": "s3://snapshots/current",
        "SNAPSHOT_S3_ENDPOINT": "minio:9000",
        "LIST_S3_KEY_ID": "lk",
        "LIST_S3_SECRET": "ls",
        "DETAIL_S3_KEY_ID": "dk",
        "DETAIL_S3_SECRET": "ds",
    }
    s = Settings.from_env(env)
    assert (s.list_s3.key_id, s.detail_s3.key_id) == ("lk", "dk")
    assert s.list_s3.scope == "s3://snapshots/current/"
    assert pq.read_table  # keep pyarrow import used
