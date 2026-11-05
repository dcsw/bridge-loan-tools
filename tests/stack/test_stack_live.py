"""Live checks against the running compose core stack (RUN_STACK=1; needs Docker)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import boto3
import duckdb
import pyarrow as pa
import pytest
import requests
import yaml
from botocore.client import Config
from botocore.exceptions import ClientError
from conftest import compose
from pyiceberg.catalog import load_catalog

pytestmark = pytest.mark.stack

ROOT = Path(__file__).resolve().parents[2]
MINIO = "http://127.0.0.1:9000"
LAKEKEEPER = "http://127.0.0.1:8181"
WAREHOUSE = "bridgeloan"
BUCKETS = ["lakehouse", "raw", "snapshots"]


def _s3(env):
    return boto3.client(
        "s3",
        endpoint_url=MINIO,
        aws_access_key_id=env["MINIO_ROOT_USER"],
        aws_secret_access_key=env["MINIO_ROOT_PASSWORD"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


def _ps():
    out = compose("ps", "-a", "--format", "json").stdout
    return {
        row["Service"]: row
        for row in (json.loads(line) for line in out.splitlines() if line.strip())
    }


def test_services_healthy_and_bootstrap_exited_zero(stack):
    ps = _ps()
    for name in ("minio", "postgres", "lakekeeper"):
        assert ps[name]["Health"] == "healthy", name
    assert ps["bootstrap"]["State"] == "exited" and ps["bootstrap"]["ExitCode"] == 0
    assert ps["lakekeeper-migrate"]["ExitCode"] == 0


def test_published_ports_loopback_in_rendered_config(stack):
    rendered = yaml.safe_load(compose("config", "--format", "json").stdout)
    published = 0
    for name, svc in rendered["services"].items():
        for port in svc.get("ports", []):
            published += 1
            assert port.get("host_ip") == "127.0.0.1", f"{name}: {port}"
    assert published >= 2


def test_no_default_credentials_in_rendered_config(stack):
    assert "minioadmin" not in compose("config").stdout.lower()


def test_buckets_block_public_access_and_use_sse(stack):
    s3 = _s3(stack)
    names = {b["Name"] for b in s3.list_buckets()["Buckets"]}
    assert set(BUCKETS) <= names
    for bucket in BUCKETS:
        # default encryption configured
        enc = s3.get_bucket_encryption(Bucket=bucket)
        rule = enc["ServerSideEncryptionConfiguration"]["Rules"][0]
        assert rule["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] in ("AES256", "aws:kms")
        # objects written without SSE headers still come back encrypted
        s3.put_object(Bucket=bucket, Key="probe.txt", Body=b"x")
        assert s3.head_object(Bucket=bucket, Key="probe.txt").get("ServerSideEncryption")
        # anonymous access denied for list, get and put
        url = f"{MINIO}/{bucket}"
        assert requests.get(url, timeout=10).status_code == 403
        assert requests.get(f"{url}/probe.txt", timeout=10).status_code == 403
        assert requests.put(f"{url}/anon.txt", data=b"x", timeout=10).status_code == 403
        # no public ACL / policy grants
        try:
            policy = json.loads(s3.get_bucket_policy(Bucket=bucket)["Policy"])
        except ClientError:
            policy = {"Statement": []}
        for stmt in policy["Statement"]:
            principal = stmt.get("Principal")
            if stmt.get("Effect") == "Allow":
                assert principal not in ("*", {"AWS": "*"}, {"AWS": ["*"]}), stmt
        s3.delete_object(Bucket=bucket, Key="probe.txt")


def _load(catalog, ident):
    return _mod().with_endpoint(catalog.load_table(ident), MINIO)


def _mod():
    spec = importlib.util.spec_from_file_location(
        "expire_snapshots", ROOT / "scripts" / "maintenance" / "expire_snapshots.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _catalog(env):
    return load_catalog(
        "lk",
        **{
            "type": "rest",
            "uri": f"{LAKEKEEPER}/catalog",
            "warehouse": WAREHOUSE,
            # client-side override: warehouse metadata carries internal minio:9000
            "s3.endpoint": MINIO,
            "s3.path-style-access": "true",
            "s3.access-key-id": env["MINIO_ROOT_USER"],
            "s3.secret-access-key": env["MINIO_ROOT_PASSWORD"],
            "s3.region": "us-east-1",
        },
    )


def test_pyiceberg_write_duckdb_read_roundtrip(stack):
    catalog = _catalog(stack)
    catalog.create_namespace_if_not_exists("wu3")
    schema = pa.schema([pa.field("id", pa.int64(), nullable=False), pa.field("name", pa.string())])
    if catalog.table_exists("wu3.roundtrip"):
        catalog.drop_table("wu3.roundtrip")
    catalog.create_table("wu3.roundtrip", schema=schema)
    table = _load(catalog, "wu3.roundtrip")
    table.append(pa.table({"id": [1, 2], "name": ["a", "b"]}, schema=schema))

    metadata = catalog.load_table("wu3.roundtrip").metadata_location
    assert "minio:9000" in catalog.load_table("wu3.roundtrip").io.properties["s3.endpoint"]
    assert metadata.startswith("s3://")

    con = duckdb.connect()
    con.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
    con.execute(
        "CREATE SECRET minio (TYPE S3, KEY_ID ?, SECRET ?, ENDPOINT '127.0.0.1:9000', "
        "URL_STYLE 'path', USE_SSL false, REGION 'us-east-1')",
        [stack["MINIO_ROOT_USER"], stack["MINIO_ROOT_PASSWORD"]],
    )
    rows = con.execute("SELECT id, name FROM iceberg_scan(?) ORDER BY id", [metadata]).fetchall()
    assert rows == [(1, "a"), (2, "b")]


def test_duckdb_rest_attach_supported(stack):  # runs after the roundtrip test creates the table
    """The pinned DuckDB can ATTACH the Lakekeeper REST catalog (no data read needed)."""
    con = duckdb.connect()
    con.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
    con.execute(
        "CREATE SECRET minio (TYPE S3, KEY_ID ?, SECRET ?, ENDPOINT '127.0.0.1:9000', "
        "URL_STYLE 'path', USE_SSL false, REGION 'us-east-1')",
        [stack["MINIO_ROOT_USER"], stack["MINIO_ROOT_PASSWORD"]],
    )
    con.execute(
        f"ATTACH '{WAREHOUSE}' AS lk (TYPE ICEBERG, ENDPOINT '{LAKEKEEPER}/catalog', "
        "AUTHORIZATION_TYPE 'none', ACCESS_DELEGATION_MODE 'none')"
    )
    assert con.execute("SELECT count(*) FROM lk.wu3.roundtrip").fetchone() == (2,)


def test_expire_script_on_live_table_with_three_snapshots(stack):
    mod = _mod()
    catalog = _catalog(stack)
    catalog.create_namespace_if_not_exists("wu3")
    schema = pa.schema([pa.field("id", pa.int64(), nullable=False)])
    if catalog.table_exists("wu3.snaps"):
        catalog.drop_table("wu3.snaps")
    catalog.create_table("wu3.snaps", schema=schema)
    table = _load(catalog, "wu3.snaps")
    for i in range(3):
        table.append(pa.table({"id": pa.array([i], pa.int64())}, schema=schema))
    table = _load(catalog, "wu3.snaps")
    assert len(table.snapshots()) == 3
    assert len(mod.expire_snapshots(table, retain_last=1)) == 2
    reloaded = _load(catalog, "wu3.snaps")
    assert len(reloaded.snapshots()) == 1
    assert sorted(reloaded.scan().to_arrow()["id"].to_pylist()) == [0, 1, 2]
