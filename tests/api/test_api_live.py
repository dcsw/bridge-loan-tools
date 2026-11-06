"""The API app against the live MinIO from WU3 (RUN_STACK=1; MinIO must be up).

Covers the deployed read path: manifest + Parquet read through locked DuckDB over S3, separate
list/detail credentials, the SigV4 source-PDF stream, and manifest re-read after a 503.
"""

from __future__ import annotations

import os
import time
import uuid

import boto3
import pytest
from botocore.client import Config
from fastapi.testclient import TestClient

from backend.api.config import RawStoreConfig, Settings
from backend.api.lockdown import S3Credentials
from backend.api.main import create_app
from backend.common.snapshot import DETAIL_FILE, LIST_FILE, MANIFEST_FILE
from tests.api.conftest import AUTH_HEADERS, GATEWAY_SECRET, ROOT, build_api_snapshot

pytestmark = pytest.mark.stack

ENDPOINT = "127.0.0.1:9000"
PDF = b"%PDF-1.7\n" + bytes(range(256)) * 400  # > one 64 KiB chunk


def _env() -> dict[str, str]:
    out = {}
    for line in (ROOT / os.environ.get("BRIDGE_ENV_FILE", ".env")).read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


@pytest.fixture(scope="module")
def minio(tmp_path_factory):
    env = _env()
    s3 = boto3.client(
        "s3",
        endpoint_url=f"http://{ENDPOINT}",
        aws_access_key_id=env["MINIO_ROOT_USER"],
        aws_secret_access_key=env["MINIO_ROOT_PASSWORD"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    snap_bucket = f"apisnap-{uuid.uuid4().hex[:8]}"
    raw_bucket = f"apiraw-{uuid.uuid4().hex[:8]}"
    s3.create_bucket(Bucket=snap_bucket)
    s3.create_bucket(Bucket=raw_bucket)
    local = tmp_path_factory.mktemp("live") / "snap"
    rows = build_api_snapshot(local, 40)
    yield {
        "env": env,
        "s3": s3,
        "snap_bucket": snap_bucket,
        "raw_bucket": raw_bucket,
        "local": local,
        "rows": rows,
    }
    for bucket in (snap_bucket, raw_bucket):
        for obj in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=obj["Key"])
        s3.delete_bucket(Bucket=bucket)


def _publish(minio, prefix="current"):
    for name in (LIST_FILE, DETAIL_FILE, MANIFEST_FILE):
        minio["s3"].upload_file(
            str(minio["local"] / name), minio["snap_bucket"], f"{prefix}/{name}"
        )


def _client(minio, tmp_path, **kw) -> TestClient:
    env = minio["env"]
    base = f"s3://{minio['snap_bucket']}/current"

    def creds(key_id, secret):
        return S3Credentials(key_id, secret, ENDPOINT, scope=base + "/")

    user, password = env["MINIO_ROOT_USER"], env["MINIO_ROOT_PASSWORD"]
    settings = Settings(
        gateway_secret=GATEWAY_SECRET,
        snapshot_base=base,
        list_s3=creds(user, password),
        detail_s3=creds(user, password),
        raw_store=RawStoreConfig(
            endpoint=f"http://{ENDPOINT}",
            bucket=minio["raw_bucket"],
            key_id=user,
            secret=password,
        ),
        audit_log_path=str(tmp_path / "audit.log"),
        **kw,
    )
    return TestClient(create_app(settings), raise_server_exceptions=False, headers=AUTH_HEADERS)


def test_503_until_published_then_serves_from_s3(minio, tmp_path):
    client = _client(minio, tmp_path, reload_min_interval_seconds=0.2, manifest_ttl_seconds=3600)
    assert client.get("/loans").status_code == 503
    assert client.get("/health/ready").status_code == 503
    _publish(minio)
    time.sleep(0.3)  # > reload throttle; far below the TTL: the 503 path forces the re-read
    r = client.get("/loans", params={"limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 40 and len(body["features"]) == 5
    assert "borrower" not in r.text.lower()
    assert client.get("/health/ready").status_code == 200


def test_detail_and_source_pdf_stream_from_minio(minio, tmp_path):
    _publish(minio)
    client = _client(minio, tmp_path)
    first = client.get("/loans", params={"limit": 1}).json()["features"][0]["properties"]["loan_id"]
    detail = client.get(f"/loans/{first}")
    assert detail.status_code == 200 and detail.json()["borrower"]
    doc_id = detail.json()["source"]["doc_id"]

    missing = client.get(f"/loans/{first}/source")
    assert missing.status_code == 404 and missing.json()["code"] == "document_not_found"

    minio["s3"].put_object(
        Bucket=minio["raw_bucket"], Key=f"{doc_id}.pdf", Body=PDF, ContentType="text/html"
    )
    r = client.get(f"/loans/{first}/source")
    assert r.status_code == 200
    assert r.content == PDF
    assert r.headers["content-type"] == "application/pdf"  # not the object's text/html
    assert r.headers["content-disposition"] == f'attachment; filename="{doc_id}.pdf"'
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert r.headers["content-length"] == str(len(PDF))

    minio["s3"].put_object(Bucket=minio["raw_bucket"], Key=f"{doc_id}.pdf", Body=b"<html>")
    bad = client.get(f"/loans/{first}/source")
    assert bad.status_code == 502 and bad.json()["code"] == "invalid_document"


def test_wrong_credentials_for_raw_store_fail_closed(minio, tmp_path):
    _publish(minio)
    client = _client(minio, tmp_path)
    first = client.get("/loans", params={"limit": 1}).json()["features"][0]["properties"]["loan_id"]
    client.app.state.fetcher._cfg = RawStoreConfig(
        endpoint=f"http://{ENDPOINT}", bucket=minio["raw_bucket"], key_id="nope", secret="x" * 20
    )
    r = client.get(f"/loans/{first}/source")
    assert r.status_code == 503 and r.json()["code"] == "documents_unavailable"


def test_no_credentials_means_no_snapshot(minio, tmp_path):
    _publish(minio)
    bad = S3Credentials("nope", "x" * 20, ENDPOINT, scope=f"s3://{minio['snap_bucket']}/current/")
    settings = Settings(
        gateway_secret=GATEWAY_SECRET,
        snapshot_base=f"s3://{minio['snap_bucket']}/current",
        list_s3=bad,
        detail_s3=bad,
        audit_log_path=str(tmp_path / "a2.log"),
    )
    other = TestClient(create_app(settings), raise_server_exceptions=False, headers=AUTH_HEADERS)
    assert other.get("/loans").status_code == 503
