"""Informational perf of the read API on the 5k-row synthetic snapshot (WU11 DoD 4).

In-process (Starlette TestClient, no network, no gateway) so the numbers isolate the API +
DuckDB path. Conditions are recorded next to the numbers: hardware, versions, N runs, tool.
Informational only in Phase 1: nothing here gates a build.

    uv run python scripts/measure_perf.py --out docs/perf/phase1.json [--runs 50] [--rows 5000]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WARMUP = 5
GATEWAY_SECRET = "perf-secret-0123456789-0123456789-0123456789"
HEADERS = {"X-Gateway-Secret": GATEWAY_SECRET, "X-Key-Id": "perf"}


def _cpu() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _mem_gib() -> float | None:
    try:
        kb = int(Path("/proc/meminfo").read_text().split()[1])
        return round(kb / 1024 / 1024, 1)
    except (OSError, ValueError, IndexError):
        return None


def _pct(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def measure(rows: int, runs: int) -> dict:
    from fastapi.testclient import TestClient

    from backend.api.config import Settings
    from backend.api.main import create_app
    from tests.api.conftest import build_api_snapshot

    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "snap"
        build_api_snapshot(snap, rows, seed=11)
        settings = Settings(
            gateway_secret=GATEWAY_SECRET,
            snapshot_base=str(snap),
            audit_log_path=str(Path(tmp) / "audit" / "audit.log"),
        )
        client = TestClient(create_app(settings), headers=HEADERS)
        t0 = time.perf_counter()
        first = client.get("/loans?limit=1")
        cold_ms = (time.perf_counter() - t0) * 1000
        assert first.status_code == 200, first.text
        loan_id = first.json()["features"][0]["id"]
        cases = {
            "GET /loans (default limit)": "/loans",
            "GET /loans?limit=1000": "/loans?limit=1000",
            "GET /loans?min_score=80&lender_type=private_llc": (
                "/loans?min_score=80&lender_type=private_llc"
            ),
            "GET /loans?bbox (small box)": "/loans?bbox=-70.82,41.64,-70.80,41.66",
            "GET /loans/{id} (detail)": f"/loans/{loan_id}",
            "GET /coverage": "/coverage",
        }
        results = {}
        for name, path in cases.items():
            for _ in range(WARMUP):
                assert client.get(path).status_code == 200, (name, path)
            samples = []
            for _ in range(runs):
                t = time.perf_counter()
                resp = client.get(path)
                samples.append((time.perf_counter() - t) * 1000)
                assert resp.status_code == 200
            results[name] = {
                "p50_ms": round(statistics.median(samples), 2),
                "p95_ms": round(_pct(samples, 0.95), 2),
                "max_ms": round(max(samples), 2),
            }
        client.close()
    return {"cold_first_request_ms": round(cold_ms, 1), "endpoints": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--rows", type=int, default=5000)
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    report = {
        "informational": True,
        "tool": "starlette TestClient, in-process, time.perf_counter",
        "rows": args.rows,
        "runs_per_endpoint": args.runs,
        "warmup_runs": WARMUP,
        "conditions": {
            "cpu": _cpu(),
            "logical_cores": os.cpu_count(),
            "memory_gib": _mem_gib(),
            "os": platform.platform(),
            "python": platform.python_version(),
            "duckdb": version("duckdb"),
            "fastapi": version("fastapi"),
        },
        **measure(args.rows, args.runs),
    }
    text = json.dumps(report, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
