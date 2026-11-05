#!/usr/bin/env python3
"""Regenerate extractor goldens by running the real extractor containers on the fixtures.

MANUAL, REVIEWED: goldens define "correct" for the tolerance-based tests, so changing them
is a deliberate act. Default is a dry run that prints what would change; pass ``--write`` to
update ``tests/fixtures/golden/<extractor>/<fixture>.json`` and review the git diff before
committing. Only the synthetic PDFs in ``tests/fixtures/pdfs`` are ever processed.

    uv run python scripts/regen_goldens.py --extractor pymupdf            # dry run
    uv run python scripts/regen_goldens.py --extractor pymupdf --write
    uv run python scripts/regen_goldens.py --extractor all --write --build
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.ingest.extractors.golden import (  # noqa: E402
    canonicalize,
    compare_to_golden,
    unexpected_failures,
)

EXTRACTORS = ["pymupdf", "docling", "unstructured"]
COMPOSE = ROOT / "deploy" / "docker" / "compose.extractors.yaml"
PDFS = ROOT / "tests" / "fixtures" / "pdfs"
GOLDEN = ROOT / "tests" / "fixtures" / "golden"
BASE = ["docker", "compose", "-p", "bridgeloan-extract", "-f", str(COMPOSE), "--profile", "extract"]


def run_extractor(extractor: str, work: Path, build: bool) -> Path:
    in_dir, out_dir = work / "in", work / "out"
    in_dir.mkdir(exist_ok=True)
    (out_dir / extractor).mkdir(parents=True, exist_ok=True)
    for pdf in sorted(PDFS.glob("*.pdf")):
        shutil.copy(pdf, in_dir / pdf.name)
    env = {
        **os.environ,
        "EXTRACT_IN_DIR": str(in_dir),
        "EXTRACT_OUT_DIR": str(out_dir),
        "EXTRACT_UID": str(os.getuid()),
        "EXTRACT_GID": str(os.getgid()),
    }
    service = f"extract-{extractor}"
    if build:
        subprocess.run([*BASE, "build", service], check=True, env=env)
    subprocess.run([*BASE, "run", "--rm", "-T", service], check=True, env=env)
    return out_dir / extractor


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--extractor", choices=[*EXTRACTORS, "all"], required=True)
    parser.add_argument("--write", action="store_true", help="overwrite goldens (default: dry run)")
    parser.add_argument("--build", action="store_true", help="(re)build the image first")
    args = parser.parse_args()
    selected = EXTRACTORS if args.extractor == "all" else [args.extractor]
    for extractor in selected:
        with tempfile.TemporaryDirectory(prefix="regen-golden-") as tmp:
            out = run_extractor(extractor, Path(tmp), args.build)
            new_by_name = {}
            for pdf in sorted(PDFS.glob("*.pdf")):
                doc_id = hashlib.sha256(pdf.read_bytes()).hexdigest()
                new_by_name[pdf.stem] = canonicalize(
                    json.loads((out / f"{doc_id}.json").read_text())
                )
            bad = unexpected_failures(new_by_name)
            if bad:
                print(f"{extractor}: REFUSING to use output, unexpected failures: {bad}")
                return 1
            for name, new in new_by_name.items():
                target = GOLDEN / extractor / f"{name}.json"
                old = json.loads(target.read_text()) if target.exists() else None
                problems = ["new golden"] if old is None else compare_to_golden(new, old)
                print(f"{extractor}/{name}: {problems or 'unchanged (within tolerance)'}")
                if args.write:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps(new, indent=2, sort_keys=True) + "\n")
    if not args.write:
        print("dry run; re-run with --write to update goldens, then review `git diff`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
