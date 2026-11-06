"""Helpers shared by the stack tests (kept out of conftest.py so the module name is unique).

A bare ``from conftest import ...`` resolves to whichever tests/*/conftest.py is imported first
when several test directories are collected in one session, so helpers live here instead.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "deploy" / "docker" / "compose.yaml"
ENV_FILE = ROOT / os.environ.get("BRIDGE_ENV_FILE", ".env")
PROJECT = "bridgeloan"


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
