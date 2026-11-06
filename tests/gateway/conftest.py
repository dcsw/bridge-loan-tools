"""Fixtures for the gateway tests.

Tests marked ``stack`` need Docker; they are deselected (with a visible notice) unless
``RUN_STACK=1`` (same convention as tests/stack and tests/api).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from edge_fixtures import make_edge, upstream
from gateway_helpers import ENV_FILE, GATEWAY_COMPOSE, ROOT, docker_compose, read_env

__all__ = ["make_edge", "upstream"]  # re-exported so pytest registers the edge fixtures

HARNESS_COMPOSE = Path(__file__).parent / "harness" / "compose.stub.yaml"
HARNESS_PROJECT = "gwtest"
HARNESS_PORT = "18080"


def pytest_configure(config):
    config.addinivalue_line("markers", "stack: needs Docker (RUN_STACK=1)")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_STACK") == "1":
        return
    deselected = [i for i in items if i.get_closest_marker("stack")]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = [i for i in items if not i.get_closest_marker("stack")]
        print(
            f"\n[gateway] {len(deselected)} stack test(s) deselected; set RUN_STACK=1 to run them"
        )


class RedactedEnv(dict):
    """dict whose repr hides the values, so pytest failure output never shows credentials."""

    def __repr__(self) -> str:
        return "<env redacted>"


@pytest.fixture(scope="session")
def env() -> dict[str, str]:
    """Generate .env via `make env` (kept if present). Values are never printed."""
    subprocess.run(["make", "env"], cwd=ROOT, check=True, capture_output=True, text=True)
    return RedactedEnv(read_env(ENV_FILE))


@dataclass
class Gateway:
    base: str
    jwt_key: str = field(repr=False)
    project: str
    files: list[Path]
    gateway_secret: str = field(repr=False)
    run_env: dict[str, str]

    def compose(self, *args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
        return docker_compose(self.project, self.files, *args, check=check, env=self.run_env, **kw)

    def api_exec(self, code: str) -> str:
        """Run python inside the api container (the only way to reach it: no host port)."""
        return self.compose("exec", "-T", "api", "python", "-c", code).stdout


@pytest.fixture(scope="session")
def harness(env, tmp_path_factory) -> Iterator[Gateway]:
    """Real gateway fragment + real api image over a fixture snapshot (project gwtest)."""
    spec = importlib.util.spec_from_file_location(
        "api_conftest", ROOT / "tests" / "api" / "conftest.py"
    )
    assert spec and spec.loader
    api_conftest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api_conftest)
    snap = tmp_path_factory.mktemp("gw-snapshot") / "snap"
    api_conftest.build_api_snapshot(snap, 12)
    snap.chmod(0o755)
    for f in snap.iterdir():
        f.chmod(0o644)

    gw = Gateway(
        base=f"http://127.0.0.1:{HARNESS_PORT}",
        jwt_key=env["JWT_SIGNING_KEY"],
        project=HARNESS_PROJECT,
        files=[GATEWAY_COMPOSE, HARNESS_COMPOSE],
        gateway_secret=env["GATEWAY_SECRET"],
        run_env={"SNAPSHOT_DIR": str(snap), "GATEWAY_PORT": HARNESS_PORT},
    )
    gw.compose("down", "-v", "--remove-orphans", check=False)
    up = gw.compose("up", "-d", "--build", "--wait", check=False)
    if up.returncode != 0:
        logs = gw.compose("logs", "--no-color", check=False).stdout[-6000:]
        gw.compose("down", "-v", "--remove-orphans", check=False)
        pytest.fail(f"harness up failed:\n{up.stderr[-3000:]}\n{logs}")
    try:
        yield gw
    finally:
        gw.compose("down", "-v", "--remove-orphans", check=False)
        shutil.rmtree(snap.parent, ignore_errors=True)
