"""Static checks on the extractor compose fragment and Dockerfiles (WU5a DoD 2/3)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker" / "compose.extractors.yaml"
EXTRACTORS = ["pymupdf", "docling", "unstructured"]


def _doc():
    return yaml.safe_load(COMPOSE.read_text())


def test_one_service_per_extractor():
    assert sorted(_doc()["services"]) == sorted(f"extract-{e}" for e in EXTRACTORS)


def test_every_service_is_sandboxed():
    for name, svc in _doc()["services"].items():
        assert svc["network_mode"] == "none", name
        assert svc["read_only"] is True, name
        assert svc["user"], name
        assert "ALL" in svc["cap_drop"], name
        assert "no-new-privileges:true" in svc["security_opt"], name
        assert svc["mem_limit"] and svc["cpus"] and svc["pids_limit"], name
        assert "networks" not in svc, name
        assert svc["profiles"] == ["extract"], f"{name}: must not start with `up`"
        env = svc["environment"]
        assert env["HF_HUB_OFFLINE"] == "1" and env["TRANSFORMERS_OFFLINE"] == "1", name
        assert svc["restart"] == "no", name


def test_volumes_input_readonly_output_rw_and_no_docker_socket():
    assert "docker.sock" not in COMPOSE.read_text()
    for name, svc in _doc()["services"].items():
        vols = {v["target"]: v for v in svc["volumes"]}
        assert set(vols) == {"/in", "/out"}, name
        assert vols["/in"]["read_only"] is True, name
        assert not vols["/out"].get("read_only"), name
        assert "extract-in" in vols["/in"]["source"], name
        assert "extract-out" in vols["/out"]["source"], name
        assert name.removeprefix("extract-") in vols["/out"]["source"], name


def test_dockerfiles_digest_pinned_nonroot_offline():
    for ext in EXTRACTORS:
        df = (ROOT / "backend/ingest/extractors" / ext / "Dockerfile").read_text()
        froms = re.findall(r"^FROM\s+(\S+)", df, re.MULTILINE)
        assert froms and all("@sha256:" in f for f in froms), ext
        assert re.search(r"^USER\s+\d+", df, re.MULTILINE), ext
        assert "HF_HUB_OFFLINE=1" in df, ext


def test_model_extractors_have_manifest_and_unstructured_revisions_pinned():
    base = ROOT / "backend/ingest/extractors"
    for ext in ["docling", "unstructured"]:
        lines = (base / ext / "models.sha256").read_text().splitlines()
        assert lines and all(re.fullmatch(r"[0-9a-f]{64}  \S+", ln) for ln in lines), ext
        assert "models.sha256" in (base / ext / "Dockerfile").read_text(), ext
    bake = (base / "unstructured" / "bake_models.py").read_text()
    assert len(re.findall(r'"[0-9a-f]{40}"', bake)) >= 2
    assert "main" not in re.findall(r"revision=(\S+)", bake)
