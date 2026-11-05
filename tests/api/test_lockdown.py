"""DuckDB lockdown (local files): snapshot read works, everything else fails."""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest

from backend.api.lockdown import S3Credentials, open_locked, safe_join

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "snapshots" / "single"


@pytest.fixture
def snap(tmp_path):
    d = tmp_path / "snap"
    shutil.copytree(FIXTURE, d)
    (tmp_path / "secret.csv").write_text("a,b\n1,2\n")
    return d


def test_snapshot_read_works(snap):
    conn = open_locked([str(snap / "loans_list.parquet")])
    sql = f"select count(*) from read_parquet('{snap}/loans_list.parquet')"
    assert conn.execute(sql).fetchone() == (1,)


def test_arbitrary_file_read_fails(snap, tmp_path):
    conn = open_locked([str(snap / "loans_list.parquet")])
    for sql in (
        f"select * from read_csv('{tmp_path}/secret.csv')",
        "select * from read_csv('/etc/hostname')",
        f"select * from read_parquet('{snap}/loans_detail.parquet')",
        f"select * from read_text('{snap}/manifest.json')",
        "select * from read_csv('http://127.0.0.1:1/x.csv')",
        "select * from read_csv('https://example.com/x.csv')",
        "select * from read_parquet('s3://bucket/x.parquet')",
    ):
        with pytest.raises(duckdb.Error):
            conn.execute(sql)


def test_cannot_write_or_attach(snap, tmp_path):
    conn = open_locked([str(snap / "loans_list.parquet")])
    for sql in (
        f"COPY (select 1) TO '{tmp_path}/out.csv'",
        f"ATTACH '{tmp_path}/x.db' AS x",
    ):
        with pytest.raises(duckdb.Error):
            conn.execute(sql)
    assert not (tmp_path / "out.csv").exists()


def test_configuration_locked(snap):
    conn = open_locked([str(snap / "loans_list.parquet")])
    for sql in (
        "SET enable_external_access = true",
        "SET allowed_paths = ['/etc/hostname']",
        "SET allowed_directories = ['/']",
        "SET lock_configuration = false",
        "SET autoinstall_known_extensions = true",
        "RESET enable_external_access",
    ):
        with pytest.raises(duckdb.Error):
            conn.execute(sql)


def test_no_install_or_autoload(snap):
    conn = open_locked([str(snap / "loans_list.parquet")])
    settings = dict(
        conn.execute(
            "select name, value from duckdb_settings() "
            "where name in ('autoinstall_known_extensions','autoload_known_extensions',"
            "'enable_external_access','lock_configuration')"
        ).fetchall()
    )
    assert settings == {
        "autoinstall_known_extensions": "false",
        "autoload_known_extensions": "false",
        "enable_external_access": "false",
        "lock_configuration": "true",
    }
    with pytest.raises(duckdb.Error):
        conn.execute("INSTALL spatial")


def test_empty_allow_list_rejected():
    with pytest.raises(ValueError):
        open_locked([])


def test_remote_requires_credentials():
    with pytest.raises(ValueError, match="credentials"):
        open_locked(["s3://snapshots/v1/loans_list.parquet"])


def test_failed_setup_closes_connection():
    creds = S3Credentials("k", "s", "127.0.0.1:1", "s3://snapshots/", url_style="bogus")
    with pytest.raises(duckdb.Error):
        open_locked(["s3://snapshots/x.parquet"], creds)


@pytest.mark.parametrize(
    "rel", ["../x", "/abs", "a/../../b", "s3://other/x", "a\\b", "", "https://evil/x"]
)
def test_safe_join_rejects(rel):
    with pytest.raises(ValueError):
        safe_join("s3://snapshots/v1", rel)


def test_safe_join_ok():
    got = safe_join("s3://snapshots/v1/", "loans_list.parquet")
    assert got == "s3://snapshots/v1/loans_list.parquet"
    assert safe_join("/tmp/s", "v2/x.parquet") == "/tmp/s/v2/x.parquet"
