"""Static checks on the rendered `docker compose config` (needs the docker CLI, not a daemon)."""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest
import yaml
from gateway_helpers import COMPOSE_DIR, ENV_FILE, GATEWAY_COMPOSE, ROOT

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


@pytest.fixture(scope="module")
def rendered() -> dict:
    if not ENV_FILE.exists():
        subprocess.run(["make", "env"], cwd=ROOT, check=True, capture_output=True)
    out = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(ENV_FILE),
            "-f",
            str(COMPOSE_DIR / "compose.yaml"),
            "--profile",
            "core",
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
        # the default publish (8080) is what is under test, not a developer's override
        env={k: v for k, v in os.environ.items() if k != "GATEWAY_PORT"},
    )
    return json.loads(out.stdout)


def test_gateway_fragment_defines_krakend_and_edge_in_core_profile():
    services = yaml.safe_load(GATEWAY_COMPOSE.read_text())["services"]
    assert {"krakend", "gateway"} <= set(services)
    for name in ("krakend", "gateway"):
        assert services[name]["profiles"] == ["core"], name
        assert services[name]["networks"] == ["core"], name


def test_only_gateway_publishes_from_this_unit_on_loopback_8080(rendered):
    services = rendered["services"]
    ports = services["gateway"]["ports"]
    assert len(ports) == 1
    assert ports[0]["host_ip"] == "127.0.0.1"
    assert str(ports[0]["published"]) == "8080" and ports[0]["target"] == 8080
    for name in ("api", "krakend"):
        assert not services[name].get("ports"), f"{name} must not publish a port"


def test_no_published_port_is_reachable_beyond_loopback(rendered):
    for name, svc in rendered["services"].items():
        for port in svc.get("ports", []):
            assert port["host_ip"] == "127.0.0.1", (name, port)


def test_krakend_is_the_only_route_to_api_and_waits_for_it(rendered):
    services = rendered["services"]
    assert services["krakend"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert services["gateway"]["depends_on"]["krakend"]["condition"] == "service_healthy"
    assert set(services["api"]["networks"]) == {"core"}


def test_images_are_digest_pinned(rendered):
    assert DIGEST.search(rendered["services"]["krakend"]["image"])


def test_secrets_come_from_env_not_the_file():
    text = GATEWAY_COMPOSE.read_text()
    assert "GATEWAY_SECRET: ${GATEWAY_SECRET:?" in text
    assert "JWT_SIGNING_KEY: ${JWT_SIGNING_KEY:?" in text
    assert not re.search(r"[0-9a-f]{40,}", re.sub(r"@sha256:[0-9a-f]{64}", "", text))


def test_krakend_is_hardened_and_has_healthchecks(rendered):
    krakend = rendered["services"]["krakend"]
    assert krakend["read_only"] is True
    assert krakend["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in krakend["security_opt"]
    assert krakend["user"] not in ("", "0", "root")
    assert krakend["healthcheck"]["test"]
    assert rendered["services"]["gateway"]["healthcheck"]["test"]
    assert rendered["services"]["gateway"]["read_only"] is True
