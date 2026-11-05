"""API image stays small (deps allow-list) and its compose fragment is locked down."""

from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "backend" / "api" / "pyproject.toml"
COMPOSE = ROOT / "deploy" / "docker" / "compose.api.yaml"
DOCKERFILE = ROOT / "deploy" / "docker" / "api" / "Dockerfile"

ALLOWED = {"fastapi", "duckdb", "uvicorn", "mangum"}
FORBIDDEN = {
    "boto3",
    "botocore",
    "pyarrow",
    "pandas",
    "pyiceberg",
    "pyspark",
    "requests",
    "httpx",
    "faker",
    "fpdf2",
    "pillow",
    "numpy",
}


def _names(deps):
    return {re.split(r"[<>=!~\[ ;]", d, maxsplit=1)[0].lower() for d in deps}


def test_runtime_dependencies_are_only_fastapi_duckdb_level():
    deps = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    assert _names(deps) <= ALLOWED
    assert not _names(deps) & FORBIDDEN
    assert {"fastapi", "duckdb"} <= _names(deps)


def test_duckdb_is_pinned_exactly():
    deps = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    assert "duckdb==2.0.0" in deps


def test_test_only_deps_live_in_dev_group_not_runtime():
    data = tomllib.loads(PYPROJECT.read_text())
    assert "httpx" not in _names(data["project"]["dependencies"])
    assert "httpx" in _names(data["dependency-groups"]["dev"])


def test_api_source_imports_no_heavy_modules():
    banned = ("boto3", "botocore", "pyarrow", "pandas", "pyiceberg", "requests", "httpx", "faker")
    for path in (ROOT / "backend" / "api").glob("*.py"):
        text = path.read_text()
        for mod in banned:
            assert not re.search(rf"^\s*(import|from)\s+{mod}\b", text, re.M), (path, mod)
        # backend.common modules that drag in heavy deps must not be imported by the API
        assert not re.search(r"backend\.common\.(snapshot|classify|normalize|signals)", text), path


def test_compose_service_is_locked_down():
    svc = yaml.safe_load(COMPOSE.read_text())["services"]["api"]
    assert svc["profiles"] == ["core"]
    assert list(svc["networks"]) == ["core"]
    assert "ports" not in svc  # never published on the host; only the gateway is
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert "healthcheck" in svc
    assert svc["build"]["dockerfile"] == "deploy/docker/api/Dockerfile"


def test_compose_secrets_come_from_env_substitution():
    text = COMPOSE.read_text()
    for var in ("GATEWAY_SECRET", "MINIO_ROOT_PASSWORD"):
        assert "${" + var + ":?" in text, var


def test_dockerfile_digest_pinned_non_root_and_extension_preinstalled():
    spec = importlib.util.spec_from_file_location(
        "check_image_digests", ROOT / "scripts" / "check_image_digests.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.find_violations(DOCKERFILE.parent) == []
    text = DOCKERFILE.read_text()
    assert re.search(r"^USER\s+(?!root\b)\S+", text, re.M)
    assert "INSTALL httpfs" in text  # baked in at build time
    assert "EXPOSE" not in text.replace("EXPOSE 8000", "")  # only the internal port


def test_dockerfile_pins_match_lockfile():
    lock = (ROOT / "uv.lock").read_text()
    text = DOCKERFILE.read_text()
    for pkg in ("fastapi", "duckdb", "uvicorn"):
        locked = re.search(rf'name = "{pkg}"\nversion = "([^"]+)"', lock).group(1)
        assert f"{pkg}=={locked}" in text, pkg
