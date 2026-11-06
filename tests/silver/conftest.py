"""WU6 silver tests: pure Python by default; the live Spark test needs RUN_STACK=1.

No unit test here may touch the network: an autouse guard makes every socket connect fail loudly.
"""

from __future__ import annotations

import os
import socket

import pytest


class NetworkBlocked(OSError):
    """Raised by the guard; an OSError so HTTP clients surface it as an ordinary connect failure."""


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
        print(f"\n[silver] {len(deselected)} stack test(s) deselected; set RUN_STACK=1 to run them")


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if request.node.get_closest_marker("stack"):
        return

    def refuse(*args, **kwargs):
        raise NetworkBlocked("network access attempted in a silver unit test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture(scope="session")
def stack():
    """Live core stack. Reuses a running one (and leaves it up); otherwise starts and removes it."""
    import subprocess

    from silver_helpers import ROOT, compose, read_env

    subprocess.run(["make", "env"], cwd=ROOT, check=True, capture_output=True, text=True)
    already = bool(compose("ps", "-q", "lakekeeper", check=False).stdout.strip())
    if not already:
        up = compose(
            "up", "-d", "--build", "--wait", "minio", "postgres", "lakekeeper", "bootstrap",
            check=False,
        )  # fmt: skip
        if up.returncode != 0:
            compose("down", "-v", "--remove-orphans", check=False)
            pytest.fail(f"stack up failed:\n{up.stderr[-3000:]}")
    waited = compose("wait", "bootstrap", check=False, timeout=300)
    if waited.returncode != 0:
        if not already:
            compose("down", "-v", "--remove-orphans", check=False)
        pytest.fail(f"bootstrap failed:\n{waited.stderr[-2000:]}")
    build = compose("build", "ingest", check=False)
    assert build.returncode == 0, build.stderr[-3000:]
    try:
        yield read_env()
    finally:
        if not already:
            compose("down", "-v", "--remove-orphans", check=False)
