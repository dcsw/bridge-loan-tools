"""Static checks on the fetcher compose fragment and Dockerfile (no Docker needed)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "docker"
FRAGMENT = DEPLOY / "compose.fetcher.yaml"
DOCKERFILE = DEPLOY / "fetcher" / "Dockerfile"


def _load():
    return yaml.safe_load(FRAGMENT.read_text())


def test_fetch_network_is_internal_and_core_is_not_attached():
    doc = _load()
    assert doc["networks"]["fetch"]["internal"] is True
    assert list(doc["services"]["fetcher"]["networks"]) == ["fetch"]


def test_fetcher_is_a_one_shot_job_outside_core_profile():
    svc = _load()["services"]["fetcher"]
    assert svc["profiles"] == ["ingest"]
    assert "ports" not in svc


def test_fetcher_runs_non_root_read_only_with_dropped_caps():
    svc = _load()["services"]["fetcher"]
    assert svc["user"] not in (None, "root", "0", "0:0")
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]


def test_only_the_drop_dir_is_mounted_and_no_docker_socket():
    svc = _load()["services"]["fetcher"]
    volumes = [v if isinstance(v, str) else v["source"] for v in svc.get("volumes", [])]
    assert len(volumes) == 1 and volumes[0].endswith("data/drop:/drop")
    assert not any("docker.sock" in v for v in volumes)


def test_dockerfile_pins_base_by_digest_and_drops_root():
    text = DOCKERFILE.read_text()
    froms = re.findall(r"^FROM\s+(\S+)", text, re.MULTILINE)
    assert froms and all(re.search(r"@sha256:[0-9a-f]{64}$", f) for f in froms)
    users = re.findall(r"^USER\s+(\S+)", text, re.MULTILINE)
    assert users and users[-1] not in ("root", "0")
    assert "playwright" in text.lower()
