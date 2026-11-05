"""Docker-backed WU4 checks (RUN_STACK=1; needs the compose core stack up: `make stack-up`).

* raw_ingest dedupe against the live MinIO.
* fetcher container isolation: no route to minio, lakekeeper or the metadata IP.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.common.models import Manifest
from backend.fetcher.raw_ingest import MANIFEST_PREFIX, PDF_PREFIX, RawIngestor

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
COMPOSE_FILE = ROOT / "deploy" / "docker" / "compose.yaml"
PROJECT = "bridgeloan"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_STACK") != "1",
    reason="needs Docker and the running core stack; set RUN_STACK=1 (make stack-up first)",
)


def _env() -> dict[str, str]:
    out = {}
    for line in ENV_FILE.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def _compose(*args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "docker", "compose", "-p", PROJECT, "--env-file", str(ENV_FILE),
            "-f", str(COMPOSE_FILE), "--profile", "core", "--profile", "ingest", *args,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )  # fmt: skip


@pytest.fixture(scope="module")
def s3():
    import boto3
    from botocore.client import Config

    env = _env()
    return boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:9000",
        aws_access_key_id=env["MINIO_ROOT_USER"],
        aws_secret_access_key=env["MINIO_ROOT_PASSWORD"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


def test_raw_ingest_dedupe_against_live_minio(s3, tmp_path):
    body = b"%PDF-1.4\nwu4 dedupe " + uuid.uuid4().hex.encode() + b"\n%%EOF"
    sha = hashlib.sha256(body).hexdigest()

    def drop(url):
        row = Manifest(
            doc_id=sha,
            source_url=url,
            fetched_at=datetime.now(UTC),
            town="mattapoisett",
            status="fetched",
            size_bytes=len(body),
        )
        (tmp_path / f"{sha}.pdf").write_bytes(body)
        (tmp_path / f"{sha}.json").write_text(row.model_dump_json())

    pdf_key, row_key = f"{PDF_PREFIX}{sha}.pdf", f"{MANIFEST_PREFIX}{sha}.json"
    try:
        ingestor = RawIngestor(s3)
        drop("https://portal.example.test/first.pdf")
        first = ingestor.ingest_dir(tmp_path)
        drop("https://portal.example.test/second.pdf")
        second = ingestor.ingest_dir(tmp_path)

        assert (first.uploaded, second.uploaded, second.duplicates) == (1, 0, 1)
        listed = s3.list_objects_v2(Bucket="raw", Prefix="")["Contents"]
        assert [o["Key"] for o in listed if sha in o["Key"]].count(pdf_key) == 1
        assert sum(1 for o in listed if sha in o["Key"]) == 2  # one pdf + one manifest row
        stored = json.loads(s3.get_object(Bucket="raw", Key=row_key)["Body"].read())
        assert stored["source_url"] == "https://portal.example.test/first.pdf"
        assert s3.get_object(Bucket="raw", Key=pdf_key)["Body"].read() == body
    finally:
        s3.delete_objects(Bucket="raw", Delete={"Objects": [{"Key": pdf_key}, {"Key": row_key}]})


IP_TEMPLATE = "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"
PROBE = r"""
import json, socket, sys, threading
targets = json.loads(sys.argv[1])
def can_connect(host, port):
    try:
        socket.create_connection((host, port), timeout=3).close()
        return True
    except OSError:
        return False
# positive control: the probe itself can connect when something is listening
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
control = can_connect("127.0.0.1", srv.getsockname()[1])
results = {}
for t in targets:
    host, _, port = t.rpartition(":")
    results[t] = can_connect(host, int(port))
print(json.dumps({"control": control, "results": results}))
"""


def _container_ip(service: str) -> str:
    cid = _compose("ps", "-q", service).stdout.strip()
    assert cid, f"{service} is not running; run `make stack-up` first"
    out = subprocess.run(
        ["docker", "inspect", "-f", IP_TEMPLATE, cid],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    return out[0]


def test_fetch_network_is_internal():
    out = subprocess.run(
        ["docker", "network", "inspect", f"{PROJECT}_fetch", "-f", "{{.Internal}}"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if out.returncode != 0:  # network is created on first `run`
        _compose("build", "fetcher")
        _compose("run", "--rm", "--no-deps", "--entrypoint", "true", "fetcher")
        out = subprocess.run(
            ["docker", "network", "inspect", f"{PROJECT}_fetch", "-f", "{{.Internal}}"],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
    assert out.stdout.strip() == "true"


def test_fetcher_container_has_no_route_to_minio_lakekeeper_or_metadata_ip():
    build = _compose("build", "fetcher")
    assert build.returncode == 0, build.stderr[-3000:]
    minio_ip, lakekeeper_ip = _container_ip("minio"), _container_ip("lakekeeper")
    targets = [
        "minio:9000",
        "lakekeeper:8181",
        f"{minio_ip}:9000",
        f"{lakekeeper_ip}:8181",
        "169.254.169.254:80",
    ]
    run = _compose(
        "run", "--rm", "--no-deps", "--entrypoint", "python", "fetcher",
        "-c", PROBE, json.dumps(targets),
    )  # fmt: skip
    assert run.returncode == 0, run.stderr[-3000:]
    result = json.loads(run.stdout.strip().splitlines()[-1])
    assert result["control"] is True, "probe could not even connect to its own loopback"
    assert result["results"] == {t: False for t in targets}
