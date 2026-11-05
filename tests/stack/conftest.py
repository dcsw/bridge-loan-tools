"""Shared fixtures for the WU3 stack tests.

Tests marked ``stack`` need Docker and a running compose core stack; they are deselected
(with a visible notice) unless ``RUN_STACK=1``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "deploy" / "docker" / "compose.yaml"
ENV_FILE = ROOT / ".env"
PROJECT = "bridgeloan"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "stack: needs Docker and the compose core stack (RUN_STACK=1)"
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_STACK") == "1":
        return
    deselected = [i for i in items if i.get_closest_marker("stack")]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = [i for i in items if not i.get_closest_marker("stack")]
        print(f"\n[stack] {len(deselected)} stack test(s) deselected; set RUN_STACK=1 to run them")


def compose(*args: str, check: bool = True, timeout: int = 900) -> subprocess.CompletedProcess:
    cmd = [
        "docker",
        "compose",
        "-p",
        PROJECT,
        "--env-file",
        str(ENV_FILE),
        "-f",
        str(COMPOSE_FILE),
        "--profile",
        "core",
        *args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)


def read_env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


@pytest.fixture(scope="session")
def env() -> dict[str, str]:
    """Generate .env via `make env` (kept if present) and return its values."""
    subprocess.run(["make", "env"], cwd=ROOT, check=True, capture_output=True, text=True)
    return read_env(ENV_FILE)


@pytest.fixture(scope="session")
def stack(env):
    """Bring the core stack up (waiting for health) and tear it down with volumes."""
    compose("down", "-v", "--remove-orphans", check=False)
    up = compose("up", "-d", "--build", "--wait", check=False)
    if up.returncode != 0:
        logs = compose("logs", "--no-color", check=False).stdout[-6000:]
        compose("down", "-v", "--remove-orphans", check=False)
        pytest.fail(f"compose up failed:\n{up.stderr[-3000:]}\n{logs}")
    waited = compose("wait", "bootstrap", check=False, timeout=300)
    if waited.returncode != 0:
        logs = compose("logs", "--no-color", "bootstrap", "lakekeeper", check=False).stdout[-6000:]
        compose("down", "-v", "--remove-orphans", check=False)
        pytest.fail(f"bootstrap exited {waited.stdout.strip()}:\n{logs}")
    try:
        yield env
    finally:
        compose("down", "-v", "--remove-orphans", check=False)
