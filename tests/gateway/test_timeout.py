"""The committed krakend.json enforces its 10 s backend timeout (needs Docker; RUN_STACK=1).

Runs the real KrakenD image with the committed config and entrypoint, with the backend host
swapped for a sleeping server, so the test does not depend on the API having a slow route.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid

import pytest
import yaml
from gateway_helpers import GATEWAY_COMPOSE, GATEWAY_DIR, KRAKEND_JSON, http, sign_jwt

pytestmark = pytest.mark.stack

SLEEPER = (
    "import http.server,time\n"
    "class H(http.server.BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        time.sleep(30)\n"
    "http.server.ThreadingHTTPServer(('0.0.0.0',8000),H).serve_forever()\n"
)


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True)


def test_slow_backend_is_cut_off_at_ten_seconds(tmp_path, env):
    image = yaml.safe_load(GATEWAY_COMPOSE.read_text())["services"]["krakend"]["image"]
    conf = tmp_path / "gateway"
    conf.mkdir()
    shutil.copy(GATEWAY_DIR / "entrypoint.sh", conf / "entrypoint.sh")
    (conf / "krakend.json").write_text(
        KRAKEND_JSON.read_text().replace("http://api:8000", "http://slow:8000")
    )
    suffix = uuid.uuid4().hex[:8]
    net, slow, kd = f"gwto-{suffix}", f"gwto-slow-{suffix}", f"gwto-kd-{suffix}"
    _docker("network", "create", net)
    try:
        _docker(
            "run", "-d", "--rm", "--name", slow, "--network", net, "--network-alias", "slow",
            "python:3.12-slim", "python", "-c", SLEEPER,
        )  # fmt: skip
        _docker(
            "run", "-d", "--rm", "--name", kd, "--network", net, "-p", "127.0.0.1:18081:8080",
            "-e", f"GATEWAY_SECRET={env['GATEWAY_SECRET']}",
            "-e", f"JWT_SIGNING_KEY={env['JWT_SIGNING_KEY']}",
            "-v", f"{conf}:/gateway:ro", "--entrypoint", "/bin/sh", image, "/gateway/entrypoint.sh",
        )  # fmt: skip
        token = sign_jwt(env["JWT_SIGNING_KEY"], "timeout-key")
        deadline = time.time() + 30
        healthy = False
        while time.time() < deadline:
            try:
                healthy = http("http://127.0.0.1:18081/__health").status == 200
            except OSError:  # published port accepts, then resets, while KrakenD is still starting
                healthy = False
            if healthy:
                break
            time.sleep(0.5)
        assert healthy, "KrakenD did not become healthy within 30 s"
        start = time.monotonic()
        resp = http(
            "http://127.0.0.1:18081/coverage",
            headers={"Authorization": f"Bearer {token}"},
            timeout=25,
        )
        elapsed = time.monotonic() - start
        assert resp.status >= 500
        assert 9 <= elapsed <= 13, f"expected cut-off near 10 s, took {elapsed:.1f}s"
    finally:
        for name in (kd, slow):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "network", "rm", net], capture_output=True)
