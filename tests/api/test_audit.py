"""Audit log: format, permissions, retention (documented setting + purge)."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta

import pytest

from backend.api.audit import AuditLog
from backend.api.config import Settings

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _write(path, entries, extra=()):
    lines = [json.dumps(e) for e in entries] + list(extra)
    path.write_text("\n".join(lines) + "\n")


def _entry(days_ago, loan="l1"):
    ts = (NOW - timedelta(days=days_ago)).isoformat()
    return {"ts": ts, "key_id": "k", "loan_id": loan, "action": "detail"}


def test_record_appends_json_line_and_creates_private_file(tmp_path):
    log = AuditLog(tmp_path / "sub" / "audit.log", retention_days=30, clock=lambda: NOW)
    log.record("key-1", "loan-9", "detail")
    path = tmp_path / "sub" / "audit.log"
    assert json.loads(path.read_text()) == {
        "ts": NOW.isoformat(),
        "key_id": "key-1",
        "loan_id": "loan-9",
        "action": "detail",
    }
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_purge_removes_only_entries_older_than_retention(tmp_path):
    path = tmp_path / "audit.log"
    _write(path, [_entry(100, "old"), _entry(29, "fresh"), _entry(31, "stale")], ["not json"])
    log = AuditLog(path, retention_days=30, clock=lambda: NOW)
    removed = log.purge_expired()
    assert removed == 2
    kept = path.read_text().splitlines()
    assert [json.loads(k)["loan_id"] for k in kept if k.startswith("{")] == ["fresh"]
    assert "not json" in kept  # unparseable lines are never silently destroyed


def test_purge_on_missing_file_is_noop(tmp_path):
    assert AuditLog(tmp_path / "none.log", retention_days=1, clock=lambda: NOW).purge_expired() == 0


def test_purge_runs_at_most_daily_during_record(tmp_path):
    path = tmp_path / "audit.log"
    _write(path, [_entry(100, "old")])
    clock = [NOW]
    log = AuditLog(path, retention_days=30, clock=lambda: clock[0])
    log.record("k", "a", "detail")  # first write triggers a purge
    assert "old" not in path.read_text()
    _write(path, [_entry(100, "old2")])
    log.record("k", "b", "detail")  # within 24h: no purge
    assert "old2" in path.read_text()
    clock[0] = NOW + timedelta(hours=25)
    log.record("k", "c", "detail")
    assert "old2" not in path.read_text()


def test_retention_must_be_positive(tmp_path):
    with pytest.raises(ValueError, match="retention"):
        AuditLog(tmp_path / "a.log", retention_days=0)
    with pytest.raises(ValueError, match="retention"):
        Settings(
            gateway_secret="g" * 32,
            snapshot_base="/tmp/s",
            audit_log_path="x",
            audit_retention_days=0,
        )


def test_retention_setting_from_env_with_default():
    base = {"GATEWAY_SECRET": "g" * 32, "SNAPSHOT_BASE": "/tmp/s"}
    assert Settings.from_env(base).audit_retention_days == 365
    assert Settings.from_env({**base, "AUDIT_RETENTION_DAYS": "90"}).audit_retention_days == 90
    with pytest.raises(ValueError):
        Settings.from_env({**base, "AUDIT_RETENTION_DAYS": "abc"})


def test_app_startup_purges_expired_entries(tmp_path, snapshot_dir, make_client):
    path = tmp_path / "audit" / "audit.log"
    path.parent.mkdir()
    old = {"ts": "2000-01-01T00:00:00+00:00", "key_id": "k", "loan_id": "old", "action": "detail"}
    _write(path, [old])
    make_client(snapshot_dir, audit_retention_days=30)
    assert path.read_text().strip() == ""


def test_documented_in_adr():
    from pathlib import Path

    adr = (Path(__file__).resolve().parents[2] / "docs/adr/0006-duckdb-lockdown.md").read_text()
    assert "AUDIT_RETENTION_DAYS" in adr
