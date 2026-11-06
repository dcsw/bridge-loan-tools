"""Locate the repo root so notebooks run from the host checkout or the notebooks container."""

from __future__ import annotations

from pathlib import Path


def repo_root(start: Path | None = None) -> Path:
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "backend" / "ingest").is_dir() and (
            candidate / "tests" / "fixtures"
        ).is_dir():
            return candidate
    raise RuntimeError("repo root not found (needs backend/ingest and tests/fixtures)")
