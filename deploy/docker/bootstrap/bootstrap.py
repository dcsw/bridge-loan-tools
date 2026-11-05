"""One-shot bootstrap: buckets (SSE + no public access), Lakekeeper warehouse, namespaces.

Idempotent: safe to re-run. Exits non-zero on any failure.
"""

from __future__ import annotations

import os
import sys
import time

import boto3
import requests
from botocore.client import Config
from botocore.exceptions import ClientError

MINIO_ENDPOINT = os.environ["MINIO_ENDPOINT"]
LAKEKEEPER_URL = os.environ["LAKEKEEPER_URL"]
WAREHOUSE_NAME = os.environ.get("WAREHOUSE_NAME", "bridgeloan")
ACCESS_KEY = os.environ["MINIO_ROOT_USER"]
SECRET_KEY = os.environ["MINIO_ROOT_PASSWORD"]
REGION = "us-east-1"

WAREHOUSE_BUCKET = "lakehouse"
BUCKETS = [WAREHOUSE_BUCKET, "raw", "snapshots"]
NAMESPACES = ["bronze", "silver"]


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name=REGION,
        config=Config(s3={"addressing_style": "path"}),
    )


def ensure_bucket(s3, bucket: str) -> None:
    try:
        s3.create_bucket(Bucket=bucket)
        log(f"created bucket {bucket}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise
    # SSE-S3 by default for every object (backed by MINIO_KMS_SECRET_KEY).
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    # Block public access: no anonymous policy; MinIO denies anonymous requests by default.
    try:
        s3.delete_bucket_policy(Bucket=bucket)
    except ClientError:
        pass
    try:
        s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
    except ClientError as exc:
        # MinIO does not implement PutPublicAccessBlock; the absence of any anonymous
        # policy is what blocks access, and is asserted by tests/stack.
        log(
            f"public-access-block API unsupported ({exc.response['Error']['Code']}); "
            "relying on no anonymous policy"
        )
    log(f"bucket {bucket} secured")


def api(method: str, path: str, **kwargs) -> requests.Response:
    return requests.request(method, f"{LAKEKEEPER_URL}{path}", timeout=30, **kwargs)


def ensure_bootstrapped() -> None:
    info = api("GET", "/management/v1/info")
    info.raise_for_status()
    if info.json().get("bootstrapped"):
        return
    resp = api("POST", "/management/v1/bootstrap", json={"accept-terms-of-use": True})
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"bootstrap failed: {resp.status_code} {resp.text}")
    log("lakekeeper bootstrapped")


def ensure_warehouse() -> None:
    listing = api("GET", "/management/v1/warehouse")
    listing.raise_for_status()
    if any(w["name"] == WAREHOUSE_NAME for w in listing.json().get("warehouses", [])):
        return
    payload = {
        "warehouse-name": WAREHOUSE_NAME,
        "storage-profile": {
            "type": "s3",
            "bucket": WAREHOUSE_BUCKET,
            "key-prefix": "warehouse",
            "region": REGION,
            # internal endpoint: hosts must override client-side with s3.endpoint
            "endpoint": MINIO_ENDPOINT,
            "path-style-access": True,
            "flavor": "s3-compat",
            "sts-enabled": os.environ.get("LAKEKEEPER_STS", "true") == "true",
            "sts-role-arn": None,
        },
        "storage-credential": {
            "type": "s3",
            "credential-type": "access-key",
            "aws-access-key-id": ACCESS_KEY,
            "aws-secret-access-key": SECRET_KEY,
        },
    }
    resp = api("POST", "/management/v1/warehouse", json=payload)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"warehouse create failed: {resp.status_code} {resp.text}")
    log(f"created warehouse {WAREHOUSE_NAME}")


def ensure_namespaces() -> None:
    config = api("GET", "/catalog/v1/config", params={"warehouse": WAREHOUSE_NAME})
    config.raise_for_status()
    body = config.json()
    prefix = body["overrides"].get("prefix") or body["defaults"]["prefix"]
    for ns in NAMESPACES:
        resp = api("POST", f"/catalog/v1/{prefix}/namespaces", json={"namespace": [ns]})
        if resp.status_code == 409:
            continue
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"namespace {ns} failed: {resp.status_code} {resp.text}")
        log(f"created namespace {ns}")


def main() -> int:
    s3 = s3_client()
    for attempt in range(30):
        try:
            s3.list_buckets()
            break
        except Exception as exc:  # noqa: BLE001 - startup race, retried then re-raised
            if attempt == 29:
                raise
            log(f"waiting for minio: {exc}")
            time.sleep(2)
    for bucket in BUCKETS:
        ensure_bucket(s3, bucket)
    ensure_bootstrapped()
    ensure_warehouse()
    ensure_namespaces()
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
