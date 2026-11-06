"""Static checks on deploy/docker/compose.yaml (no Docker needed)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "docker"
COMPOSE = DEPLOY / "compose.yaml"

FRAGMENTS = [
    "compose.fetcher.yaml",
    "compose.extractors.yaml",
    "compose.ingest.yaml",
    "compose.api.yaml",
    "compose.gateway.yaml",
    "compose.web.yaml",
    "compose.notebooks.yaml",
]
SERVICES = {"minio", "postgres", "lakekeeper-migrate", "lakekeeper", "bootstrap"}


def _load():
    return yaml.safe_load(COMPOSE.read_text())


def test_includes_are_optional_fragments():
    includes = _load()["include"]
    assert [i["path"] for i in includes] == FRAGMENTS
    assert all(i["required"] is False for i in includes)


def test_core_services_present_and_in_core_profile_and_network():
    services = _load()["services"]
    assert SERVICES <= set(services)
    for name in SERVICES:
        assert services[name]["profiles"] == ["core"], name
        assert "core" in services[name]["networks"], name
    assert _load()["networks"]["core"]["driver"] == "bridge"


def test_published_ports_bind_loopback_only():
    for name, svc in _load()["services"].items():
        for port in svc.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name}: {port}"


def test_services_have_healthchecks_or_are_jobs():
    services = _load()["services"]
    for name in ("minio", "postgres", "lakekeeper"):
        assert "healthcheck" in services[name], name
    for job in ("lakekeeper-migrate", "bootstrap"):
        assert services[job]["restart"] == "no", job


def test_no_default_credentials_anywhere_in_deploy_or_make():
    for path in [*DEPLOY.rglob("*"), *(ROOT / "make").glob("*.mk"), ROOT / "Makefile"]:
        if path.is_file():
            assert "minioadmin" not in path.read_text(errors="ignore").lower(), path


def test_all_images_digest_pinned():
    spec = importlib.util.spec_from_file_location(
        "check_image_digests", ROOT / "scripts" / "check_image_digests.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.find_violations(DEPLOY) == []


def test_credentials_come_from_env_substitution():
    text = COMPOSE.read_text()
    for var in ("MINIO_ROOT_PASSWORD", "POSTGRES_PASSWORD", "LAKEKEEPER_PG_ENCRYPTION_KEY"):
        assert "${" + var + ":?" in text, var


def test_include_fragments_exist_so_plain_compose_works():
    # Compose (checked on 5.6.0) does not honor `required: false` on include, so WU3
    # ships stub fragments; owning units replace them. See ADR-0002.
    for name in FRAGMENTS:
        assert isinstance(yaml.safe_load((DEPLOY / name).read_text()), dict), name
