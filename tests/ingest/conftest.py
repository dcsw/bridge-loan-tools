"""Fixtures for the WU5b tests. Live tests (marker ``stack``) need Docker and the core stack and
are deselected, with a notice, unless ``RUN_STACK=1``.

The ``stack`` fixture reuses a stack that is already running (and leaves it up); otherwise it
starts the core stack and tears it down (with volumes) at the end.
"""

from __future__ import annotations

import os
import subprocess

import pytest
from ingest_helpers import ROOT, compose, read_env


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
        print(f"\n[ingest] {len(deselected)} stack test(s) deselected; set RUN_STACK=1 to run them")


@pytest.fixture(scope="session")
def stack():
    subprocess.run(["make", "env"], cwd=ROOT, check=True, capture_output=True, text=True)
    env = read_env()
    already = bool(compose("ps", "-q", "lakekeeper", check=False).stdout.strip())
    if not already:
        up = compose(
            "up",
            "-d",
            "--build",
            "--wait",
            "minio",
            "postgres",
            "lakekeeper",
            "bootstrap",
            check=False,
        )
        if up.returncode != 0:
            compose("down", "-v", "--remove-orphans", check=False)
            pytest.fail(f"stack up failed:\n{up.stderr[-3000:]}")
    waited = compose("wait", "bootstrap", check=False, timeout=300)
    if waited.returncode != 0:
        logs = compose("logs", "--no-color", "bootstrap", "lakekeeper", check=False).stdout[-4000:]
        if not already:
            compose("down", "-v", "--remove-orphans", check=False)
        pytest.fail(f"bootstrap failed:\n{logs}")
    build = compose("build", "ingest", check=False)
    assert build.returncode == 0, build.stderr[-3000:]
    try:
        yield env
    finally:
        if not already:
            compose("down", "-v", "--remove-orphans", check=False)
