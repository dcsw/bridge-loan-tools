"""The real core stack (compose project `bridgeloan`) with the gateway in front of the api.

Needs Docker and exclusive use of host ports 9000/8181/8080; run with RUN_STACK=1.
"""

from __future__ import annotations

import json
import os

import pytest
from gateway_helpers import (
    COMPOSE_DIR,
    docker_compose,
    http,
    load_openapi,
    response_schema,
    sign_jwt,
    validate,
)

pytestmark = pytest.mark.stack

COMPOSE = COMPOSE_DIR / "compose.yaml"
PROJECT = "bridgeloan"
# 8080 is the default; GATEWAY_PORT overrides it when something else on the host already holds it
PORT = int(os.environ.get("GATEWAY_PORT", "8080"))
BASE = f"http://127.0.0.1:{PORT}"


def compose(*args, check=True, timeout=900):
    return docker_compose(
        PROJECT, [COMPOSE], *args, check=check, timeout=timeout, env={"GATEWAY_PORT": str(PORT)}
    )


@pytest.fixture(scope="module")
def core(env):
    compose("down", "-v", "--remove-orphans", check=False)
    up = compose("up", "-d", "--build", "--wait", check=False)
    if up.returncode != 0:
        logs = compose("logs", "--no-color", check=False).stdout[-6000:]
        compose("down", "-v", "--remove-orphans", check=False)
        pytest.fail(f"core stack failed to come up healthy:\n{up.stderr[-3000:]}\n{logs}")
    try:
        yield env
    finally:
        compose("down", "-v", "--remove-orphans", check=False)


def services_state() -> dict[str, dict]:
    out = compose("ps", "--all", "--format", "json").stdout
    return {s["Service"]: s for s in (json.loads(line) for line in out.splitlines() if line)}


def test_up_wait_brings_api_krakend_and_gateway_healthy(core):
    state = services_state()
    for name in ("api", "krakend", "gateway", "minio", "postgres", "lakekeeper"):
        assert state[name]["State"] == "running", name
        assert state[name]["Health"] == "healthy", name


def test_only_the_gateway_of_this_unit_is_published_on_loopback(core):
    state = services_state()
    for name in ("api", "krakend"):
        assert not [p for p in state[name]["Publishers"] if p.get("PublishedPort")], name
    published = [p for p in state["gateway"]["Publishers"] if p.get("PublishedPort")]
    assert {(p["URL"], p["PublishedPort"]) for p in published} == {("127.0.0.1", PORT)}


def test_direct_to_api_without_shared_secret_is_rejected_end_to_end(core):
    code = (
        "import urllib.request,urllib.error\n"
        "try:\n urllib.request.urlopen('http://api:8000/loans',timeout=5)\n"
        "except urllib.error.HTTPError as e:\n print(e.code, e.read().decode())\n"
    )
    out = compose("run", "--rm", "--no-deps", "--entrypoint", "python", "api", "-c", code).stdout
    assert out.startswith("401 ") and "gateway_auth_required" in out


def test_request_without_jwt_is_401_json_through_the_published_port(core):
    resp = http(f"{BASE}/loans")
    assert resp.status == 401 and json.loads(resp.body)["code"] == "unauthorized"


def test_gateway_health_and_backend_problem_json_with_retry_after_pass_through(core):
    assert http(f"{BASE}/__health").status == 200
    assert http(f"{BASE}/health/live").status == 200
    token = sign_jwt(core["JWT_SIGNING_KEY"], "live-key")
    resp = http(f"{BASE}/loans", headers={"Authorization": f"Bearer {token}"})
    # no snapshot has been published in a fresh stack: the api answers 503 problem+json itself
    assert resp.status == 503
    assert resp.header("content-type") == "application/problem+json"
    assert resp.header("retry-after")
    body = json.loads(resp.body)
    schema = response_schema(load_openapi(), "/loans", "get", 503, "application/problem+json")
    assert schema and validate(load_openapi(), schema, body) == []
