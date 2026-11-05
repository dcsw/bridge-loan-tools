"""Static checks of the ingest image, compose fragment and make target (no Docker needed)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ROOT / "deploy" / "docker"
DOCKERFILE = (DOCKER / "ingest" / "Dockerfile").read_text()
COMPOSE_TEXT = (DOCKER / "compose.ingest.yaml").read_text()
COMPOSE = yaml.safe_load(COMPOSE_TEXT)
CORE = yaml.safe_load((DOCKER / "compose.yaml").read_text())
SVC = COMPOSE["services"]["ingest"]


def test_base_is_digest_pinned_java17():
    froms = re.findall(r"^FROM\s+(\S+)", DOCKERFILE, re.M)
    assert froms and all(re.search(r"@sha256:[0-9a-f]{64}$", f) for f in froms)
    assert "17-jre" in froms[0]


def test_jars_baked_and_checksummed_no_packages_at_runtime():
    assert "iceberg-spark-runtime-4.0_2.13" in DOCKERFILE
    assert "iceberg-aws-bundle" in DOCKERFILE
    assert "sha256sum -c" in DOCKERFILE
    assert "--require-hashes" in DOCKERFILE
    everything = DOCKERFILE + COMPOSE_TEXT
    everything += (ROOT / "backend" / "ingest" / "spark_session.py").read_text()
    assert "--packages" not in everything
    assert "spark.jars.packages" not in everything


def test_non_root_user_and_hardening():
    assert re.search(r"^USER\s+10001", DOCKERFILE, re.M)
    assert SVC["read_only"] is True
    assert SVC["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in SVC["security_opt"]
    assert SVC["profiles"] == ["ingest"]
    assert SVC["restart"] == "no"


def test_extract_out_mounted_read_only():
    mounts = [v for v in SVC["volumes"] if v["target"] == "/extract-out"]
    assert len(mounts) == 1 and mounts[0]["read_only"] is True


def test_service_only_on_internal_ingest_network():
    assert SVC["networks"] == ["ingest"]
    assert "network_mode" not in SVC
    assert CORE["networks"]["ingest"] == {"driver": "bridge", "internal": True}


def test_minio_and_lakekeeper_reachable_from_ingest_network():
    for name in ("minio", "lakekeeper"):
        assert CORE["services"][name]["networks"] == ["core", "ingest"], name
    for name in ("postgres", "bootstrap", "lakekeeper-migrate"):
        assert "ingest" not in CORE["services"][name]["networks"], name


def test_depends_on_catalog_health():
    assert SVC["depends_on"]["lakekeeper"]["condition"] == "service_healthy"


def test_make_targets_exist():
    text = (ROOT / "make" / "ingest.mk").read_text()
    for target in ("ingest-build", "ingest-run", "ingest-test", "ingest-smoke"):
        assert re.search(rf"^{target}:", text, re.M), target
    out = subprocess.run(
        ["make", "-n", "ingest-run"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    assert "--profile ingest" in out.stdout
