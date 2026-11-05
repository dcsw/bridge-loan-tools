"""Helpers for the live WU5b tests (kept out of conftest.py so the module name is unique)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "deploy" / "docker" / "compose.yaml"
ENV_FILE = ROOT / ".env"
PROJECT = "bridgeloan"
LAKEKEEPER = "http://127.0.0.1:8181"
WAREHOUSE = "bridgeloan"
GOLDEN = ROOT / "tests" / "fixtures" / "golden"


def compose(
    *args: str, check: bool = True, timeout: int = 1200, extract_out: Path | None = None
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if extract_out is not None:
        env["EXTRACT_OUT_DIR"] = str(extract_out)
    cmd = [
        "docker", "compose", "-p", PROJECT, "--env-file", str(ENV_FILE),
        "-f", str(COMPOSE_FILE), "--profile", "core", "--profile", "ingest", *args,
    ]  # fmt: skip
    return subprocess.run(
        cmd, capture_output=True, text=True, check=check, timeout=timeout, env=env
    )


def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def duck(env: dict[str, str]) -> duckdb.DuckDBPyConnection:
    """DuckDB attached to the Lakekeeper warehouse as ``lk`` (host: no vended credentials)."""
    con = duckdb.connect()
    con.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
    con.execute(
        "CREATE SECRET minio (TYPE S3, KEY_ID ?, SECRET ?, ENDPOINT '127.0.0.1:9000', "
        "URL_STYLE 'path', USE_SSL false, REGION 'us-east-1')",
        [env["MINIO_ROOT_USER"], env["MINIO_ROOT_PASSWORD"]],
    )
    con.execute(
        f"ATTACH '{WAREHOUSE}' AS lk (TYPE ICEBERG, ENDPOINT '{LAKEKEEPER}/catalog', "
        "AUTHORIZATION_TYPE 'none', ACCESS_DELEGATION_MODE 'none')"
    )
    return con
