"""DuckDB lockdown against the live MinIO from WU3 (RUN_STACK=1; MinIO must be up).

Answers the WU7 spike: does ``allowed_paths`` accept s3:// URIs with
``enable_external_access=false`` once the S3 secret exists before the lock?
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import boto3
import duckdb
import pytest
from botocore.client import Config

from backend.api.lockdown import S3Credentials, open_locked

pytestmark = pytest.mark.stack

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "snapshots" / "single"
ENDPOINT = "127.0.0.1:9000"


def _env() -> dict[str, str]:
    out = {}
    for line in (ROOT / os.environ.get("BRIDGE_ENV_FILE", ".env")).read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


@pytest.fixture(scope="module")
def minio():
    env = _env()
    s3 = boto3.client(
        "s3",
        endpoint_url=f"http://{ENDPOINT}",
        aws_access_key_id=env["MINIO_ROOT_USER"],
        aws_secret_access_key=env["MINIO_ROOT_PASSWORD"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    bucket = f"lockdown-{uuid.uuid4().hex[:8]}"
    s3.create_bucket(Bucket=bucket)
    for key, src in {
        "v1/loans_list.parquet": "loans_list.parquet",
        "v1/loans_detail.parquet": "loans_detail.parquet",
        "v1/manifest.json": "manifest.json",
        "other/loans_list.parquet": "loans_list.parquet",
    }.items():
        s3.upload_file(str(FIXTURE / src), bucket, key)
    yield {"env": env, "bucket": bucket, "s3": s3}
    for obj in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
        s3.delete_object(Bucket=bucket, Key=obj["Key"])
    s3.delete_bucket(Bucket=bucket)


def _creds(minio) -> S3Credentials:
    env = minio["env"]
    return S3Credentials(
        key_id=env["MINIO_ROOT_USER"],
        secret=env["MINIO_ROOT_PASSWORD"],
        endpoint=ENDPOINT,
        scope=f"s3://{minio['bucket']}/v1/",
    )


def test_s3_snapshot_read_works_under_lock(minio):
    b = minio["bucket"]
    conn = open_locked([f"s3://{b}/v1/loans_list.parquet"], _creds(minio))
    count = conn.execute(f"select count(*) from read_parquet('s3://{b}/v1/loans_list.parquet')")
    assert count.fetchone() == (1,)


def test_manifest_readable_via_read_text(minio):
    b = minio["bucket"]
    conn = open_locked([f"s3://{b}/v1/manifest.json"], _creds(minio))
    text = conn.execute(f"select content from read_text('s3://{b}/v1/manifest.json')").fetchone()[0]
    assert "snapshot_id" in text


def test_arbitrary_reads_fail_under_lock(minio):
    b = minio["bucket"]
    conn = open_locked([f"s3://{b}/v1/loans_list.parquet"], _creds(minio))
    for sql in (
        f"select * from read_parquet('s3://{b}/v1/loans_detail.parquet')",  # sibling file
        f"select * from read_parquet('s3://{b}/other/loans_list.parquet')",  # other prefix
        "select * from read_parquet('s3://snapshots/v1/loans_list.parquet')",  # other bucket
        f"select * from read_parquet('s3://{b}/v1/*.parquet')",  # glob
        "select * from read_csv('/etc/hostname')",
        f"select * from read_csv('http://{ENDPOINT}/minio/health/ready')",
        "select * from read_csv('https://example.com/')",
    ):
        with pytest.raises(duckdb.Error):
            conn.execute(sql)


def test_allowed_paths_is_the_boundary_not_the_secret_scope(minio):
    """Even with a bucket-wide secret, only the allow-listed object is readable."""
    b = minio["bucket"]
    wide = _creds(minio)
    wide = S3Credentials(wide.key_id, wide.secret, wide.endpoint, scope=f"s3://{b}/")
    conn = open_locked([f"s3://{b}/v1/loans_list.parquet"], wide)
    ok = conn.execute(f"select count(*) from read_parquet('s3://{b}/v1/loans_list.parquet')")
    assert ok.fetchone() == (1,)
    with pytest.raises(duckdb.Error):
        conn.execute(f"select * from read_parquet('s3://{b}/other/loans_list.parquet')")
    with pytest.raises(duckdb.Error):
        conn.execute(f"select * from read_parquet('s3://{b}/v1/loans_detail.parquet')")


def test_spike_allowed_directories_with_s3_prefix(minio):
    """SPIKE record: allowed_directories accepts an s3:// prefix and gates by prefix."""
    b = minio["bucket"]
    c = _creds(minio)
    conn = duckdb.connect(":memory:", config={"autoinstall_known_extensions": "false"})
    conn.execute("LOAD httpfs")
    conn.execute(
        f"CREATE SECRET (TYPE S3, KEY_ID '{c.key_id}', SECRET '{c.secret}',"
        f" ENDPOINT '{c.endpoint}', URL_STYLE 'path', USE_SSL false, SCOPE 's3://{b}/')"
    )
    conn.execute(f"SET allowed_directories = ['s3://{b}/v1/']")
    conn.execute("SET enable_external_access = false")
    conn.execute("SET lock_configuration = true")
    ok = conn.execute(f"select count(*) from read_parquet('s3://{b}/v1/loans_list.parquet')")
    assert ok.fetchone() == (1,)
    with pytest.raises(duckdb.Error):
        conn.execute(f"select * from read_parquet('s3://{b}/other/loans_list.parquet')")


def test_list_connection_cannot_read_detail_with_detail_scoped_secret_absent(minio):
    """Separate paths + separate secrets: the list connection has no route to the detail file."""
    b = minio["bucket"]
    list_conn = open_locked([f"s3://{b}/v1/loans_list.parquet"], _creds(minio))
    detail_conn = open_locked([f"s3://{b}/v1/loans_detail.parquet"], _creds(minio))
    q = "select count(*) from read_parquet('s3://{}/v1/{}')"
    assert detail_conn.execute(q.format(b, "loans_detail.parquet")).fetchone() == (1,)
    with pytest.raises(duckdb.Error):
        list_conn.execute(q.format(b, "loans_detail.parquet"))
    with pytest.raises(duckdb.Error):
        detail_conn.execute(q.format(b, "loans_list.parquet"))
