"""Shared builders for the WU6 tests (unique module name: tests dirs share one import root)."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from backend.common.models import BronzeRecord, Instrument
from backend.ingest.unify import DocMeta

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "fixtures" / "golden"
TRUTH = ROOT / "tests" / "fixtures" / "truth"
CENSUS = ROOT / "tests" / "fixtures" / "census"
EXTRACTORS = ("docling", "pymupdf", "unstructured")
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
SHA = "a" * 64


def golden_records(name: str, extractors=EXTRACTORS) -> list[BronzeRecord]:
    out: list[BronzeRecord] = []
    for e in extractors:
        out += [BronzeRecord(**r) for r in json.loads((GOLDEN / e / f"{name}.json").read_text())]
    return out


def truth(name: str) -> dict:
    return json.loads((TRUTH / f"{name}.json").read_text())


def census(name: str) -> dict:
    return json.loads((CENSUS / f"{name}.json").read_text())


def meta_for(records) -> dict[str, DocMeta]:
    return {
        r.doc_id: DocMeta(
            town="mattapoisett", source_url=f"https://portal.example.test/docs/{r.doc_id}.pdf"
        )
        for r in records
    }


def record(extractor, text, *, doc_id=SHA, page_no=1, status="ok", error=None) -> BronzeRecord:
    return BronzeRecord(
        doc_id=doc_id,
        extractor=extractor,
        extractor_version="t",
        page_no=page_no,
        text=text,
        tables_json="[]",
        extracted_at=datetime(1970, 1, 1, tzinfo=UTC),
        status=status,
        error=error,
    )


def make_instrument(ordinal=0, doc_id=SHA, **kw) -> Instrument:
    base = dict(
        instrument_id=f"{doc_id}-{ordinal}",
        doc_id=doc_id,
        ordinal=ordinal,
        doc_type="mortgage",
        town="mattapoisett",
        parcel_key="MATT-1-1",
        address_raw="1 Main St, Mattapoisett, MA",
        address_norm="1 MAIN ST MATTAPOISETT MA",
        grantor="Jane Doe",
        grantee="Acme Capital LLC",
        lender_type="private_llc",
        principal=Decimal("100000.00"),
        recorded_date=date(2024, 1, 10),
        term_months=12,
        recorded_book="1",
        recorded_page=str(ordinal + 1),
    )
    base.update(kw)
    return Instrument(**base)


# -- live (RUN_STACK=1) helpers ------------------------------------------------------------------

COMPOSE_FILE = ROOT / "deploy" / "docker" / "compose.yaml"
ENV_FILE = ROOT / os.environ.get("BRIDGE_ENV_FILE", ".env")
PROJECT = "bridgeloan"


def compose(*args: str, check: bool = True, timeout: int = 1500, env: dict | None = None):
    import os
    import subprocess

    cmd = [
        "docker", "compose", "-p", PROJECT, "--env-file", str(ENV_FILE),
        "-f", str(COMPOSE_FILE), "--profile", "core", "--profile", "ingest", *args,
    ]  # fmt: skip
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
        env={**os.environ, **(env or {})},
    )


def read_env() -> dict[str, str]:
    out = {}
    for line in ENV_FILE.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out
