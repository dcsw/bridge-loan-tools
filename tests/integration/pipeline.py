"""Stage runners for the WU11 vertical slice. Every stage shells out to the same make targets a
developer uses, against a dedicated env file so the developer's own `.env` is never touched."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = os.environ.get("E2E_ENV_FILE", ".env.e2e")
GATEWAY_PORT = os.environ.get("E2E_GATEWAY_PORT", "18090")
WEB_PORT = os.environ.get("E2E_WEB_PORT", "13000")
DATA = ROOT / "data"
DATA_DIRS = ("drop", "extract-in", "extract-out", "manifests", "silver-work", "e2e")
FIXTURE_URLS = ["http://fakeportal:8080/pdfs/mortgage.pdf"]
# Recorded-geocoder mode: the Census response for the fixture address is replayed from the cache.
FIXTURE_ADDRESS = "12525 Obrien Squares Suite 947, Mattapoisett, MA"
COMPOSE = [
    "docker", "compose", "-p", "bridgeloan", "--env-file", ENV_FILE,
    "-f", "deploy/docker/compose.yaml", "--profile", "core", "--profile", "ingest",
    "--profile", "e2e", "--profile", "web",
]  # fmt: skip


def env() -> dict[str, str]:
    return {**os.environ, "GATEWAY_PORT": GATEWAY_PORT, "WEB_PORT": WEB_PORT}


def run(cmd: list[str], *, timeout: int = 1800, extra_env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        cmd, cwd=ROOT, env={**env(), **(extra_env or {})}, capture_output=True, text=True,
        timeout=timeout, check=False,
    )  # fmt: skip
    if proc.returncode != 0:
        tail = f"{proc.stdout[-2500:]}\n{proc.stderr[-2500:]}"
        raise RuntimeError(f"{' '.join(cmd)} failed ({proc.returncode}):\n{tail}")
    return proc.stdout


def make(target: str, **kw: str) -> str:
    return run(
        [
            "make",
            "--no-print-directory",
            target,
            f"ENV_FILE={ENV_FILE}",
            *[f"{k}={v}" for k, v in kw.items()],
        ]
    )


def clean() -> None:
    """Clean state: stack and volumes gone, every host hand-off dir empty."""
    make("env")  # a fresh checkout has no env file yet; compose needs one even to tear down
    run([*COMPOSE, "down", "-v", "--remove-orphans"])
    for name in DATA_DIRS:
        shutil.rmtree(DATA / name, ignore_errors=True)
    leftovers = [n for n in DATA_DIRS if (DATA / n).exists()]
    if leftovers:  # e.g. root-owned bind-mount dirs created by Docker
        raise RuntimeError(f"clean state not reached; remove manually: {leftovers}")


def stack_up() -> None:
    make("stack-up")
    run([*COMPOSE, "up", "-d", "--build", "--wait", "fakeportal"])


def seed_geocode_cache() -> None:
    work = DATA / "silver-work"
    work.mkdir(parents=True, exist_ok=True)
    row = {"address": FIXTURE_ADDRESS, "status": "matched", "lat": 41.6532, "lon": -70.8161}
    (work / "geocode-cache.jsonl").write_text(json.dumps(row, sort_keys=True) + "\n")


def pipeline() -> dict[str, str]:
    """fetcher -> raw-ingest -> extractors -> bronze -> silver -> publish. Idempotent."""
    for name in ("drop", "extract-in", "extract-out", "manifests", "silver-work", "e2e"):
        (DATA / name).mkdir(parents=True, exist_ok=True)
    (DATA / "e2e" / "urls.txt").write_text("\n".join(FIXTURE_URLS) + "\n")
    run([
        *COMPOSE, "run", "--rm",
        "-e", "FETCHER_PORTAL_HOSTS=fakeportal", "-e", "FETCHER_TEST_MODE=1",
        "-e", "FETCHER_ALLOW_PRIVATE_FOR_TESTS=1", "-v", f"{DATA / 'e2e'}:/work:ro",
        "fetcher", "--urls", "/work/urls.txt", "--town", "mattapoisett", "--min-interval", "0",
    ])  # fmt: skip
    ingest_out = make("fetcher-ingest")
    # raw store is MinIO; the extractors read the same bytes from the host hand-off dir.
    for pdf in (DATA / "drop").rglob("*.pdf"):
        shutil.copy2(pdf, DATA / "extract-in" / pdf.name)
    make("extract-all")
    make("ingest-run")
    make("silver-manifests")
    seed_geocode_cache()
    silver_out = make("silver-run")
    publish_out = make("publish")
    return {"fetcher_ingest": ingest_out, "silver": silver_out, "publish": publish_out}


def token(key_id: str) -> str:
    lines = make("gateway-token", KEY_ID=key_id, TTL_DAYS="1").split()
    return next(x for x in reversed(lines) if x.count(".") == 2 and x.startswith("eyJ"))


def api_get(path: str, jwt: str) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{GATEWAY_PORT}{path}", headers={"Authorization": f"Bearer {jwt}"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - loopback gateway
        return json.loads(resp.read())


def stable_view(jwt: str) -> dict:
    """The API surface with run-specific fields (timestamps, snapshot id) removed."""
    loans = api_get("/loans", jwt)
    loans.pop("generated_at", None)
    coverage = api_get("/coverage", jwt)
    for key in ("generated_at", "snapshot_id", "published_at"):
        coverage.pop(key, None)
    for town in coverage.get("towns", []):
        town.pop("last_ingested_at", None)  # wall-clock time of the last ingest run
    return {"loans": loans, "coverage": coverage}


def web_up(jwt: str) -> None:
    run(
        [*COMPOSE, "up", "-d", "--build", "--wait", "web"],
        extra_env={"WEB_API_JWT": jwt, "GATEWAY_UPSTREAM": "http://gateway:8080"},
    )
