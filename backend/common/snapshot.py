"""Fixture snapshot generator: PII-free list Parquet + borrower-detail Parquet + manifest.json.

Everything here is synthetic (Faker with fixed seeds) and deterministic.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from faker import Faker
from pydantic import BaseModel, ConfigDict

from backend.common.classify import classify_loan
from backend.common.models import Loan, LoanFeature
from backend.common.normalize import classify_lender_type, normalize_address, normalize_lender_name
from backend.common.signals import SIGNAL_NAMES, load_config

LIST_FILE = "loans_list.parquet"
DETAIL_FILE = "loans_detail.parquet"
MANIFEST_FILE = "manifest.json"
SCHEMA_VERSION = 1
POOL_SEED = 20261105  # fixed: the synthetic name pools never vary with the row seed
TOWN = "mattapoisett"
BBOX = (41.62, 41.69, -70.87, -70.76)  # approx lat_min, lat_max, lon_min, lon_max

_LIST_SCHEMA = pa.schema(
    [
        ("loan_id", pa.string()),
        ("address_norm", pa.string()),
        ("lender", pa.string()),
        ("lender_type", pa.string()),
        ("principal", pa.decimal128(14, 2)),
        ("recorded_date", pa.date32()),
        ("maturity_date", pa.date32()),
        ("term_months", pa.int32()),
        ("bridge_score", pa.int32()),
        ("is_bridge_loan", pa.bool_()),
        ("classifier_version", pa.string()),
        ("signals", pa.list_(pa.string())),
        ("geocode_status", pa.string()),
        ("lat", pa.float64()),
        ("lon", pa.float64()),
        ("town", pa.string()),
        ("signals_unevaluable", pa.list_(pa.string())),
        ("last_ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)
_DETAIL_SCHEMA = pa.schema(
    [
        ("loan_id", pa.string()),
        ("borrower", pa.string()),
        ("doc_id", pa.string()),
        ("recorded_book", pa.string()),
        ("recorded_page", pa.string()),
        ("source_url", pa.string()),
    ]
)


class SnapshotRow(BaseModel):
    model_config = ConfigDict(frozen=True)
    loan: Loan
    feature: LoanFeature
    borrower: str
    doc_id: str
    recorded_book: str
    recorded_page: str
    source_url: str


def _fake() -> Faker:
    fake = Faker("en_US")
    fake.seed_instance(POOL_SEED)
    return fake


def person_pool() -> list[str]:
    """Fixed pool of synthetic person names; the ONLY source of borrower names."""
    fake = _fake()
    return sorted({fake.name() for _ in range(300)})


def lender_pool() -> list[str]:
    """Fixed pool of synthetic lender names (organizations)."""
    fake = Faker("en_US")
    fake.seed_instance(POOL_SEED + 1)
    suffixes = ["Savings Bank", "Credit Union", "Lending LLC", "Capital LLC", "National Bank NA"]
    return sorted(
        {
            f"{fake.last_name()} {fake.word().title()} {fake.random_element(suffixes)}"
            for _ in range(60)
        }
    )


def address_pool() -> list[str]:
    fake = Faker("en_US")
    fake.seed_instance(POOL_SEED + 2)
    return sorted({fake.street_address() for _ in range(2000)})


def synthetic_rows(n: int, seed: int) -> list[SnapshotRow]:
    """Deterministic synthetic rows: same (n, seed) -> identical rows."""
    rng = random.Random(seed)
    people, lenders, addresses = person_pool(), lender_pool(), address_pool()
    cfg = load_config()
    ingested = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for i in range(n):
        digest = hashlib.sha256(f"{seed}-{i}".encode()).hexdigest()
        status = rng.choices(["matched", "low_match", "no_match"], [85, 10, 5])[0]
        lat = lon = None
        if status != "no_match":
            lat = round(rng.uniform(BBOX[0], BBOX[1]), 6)
            lon = round(rng.uniform(BBOX[2], BBOX[3]), 6)
        unevaluable = rng.sample(SIGNAL_NAMES, rng.randint(0, 2))
        fired = [s for s in SIGNAL_NAMES if s not in unevaluable and rng.random() < 0.4]
        lender = rng.choice(lenders)
        recorded = date(2022, 1, 1) + timedelta(days=rng.randint(0, 1400))
        term = rng.choice([6, 12, 18, 24, 60, 180, 360])
        maturity = recorded + timedelta(days=int(term * 30.4375)) if rng.random() < 0.8 else None
        loan_id = f"{digest}-0"
        loan = Loan(
            loan_id=loan_id,
            lat=lat,
            lon=lon,
            geocode_status=status,
            signals=fired,
            signals_unevaluable=unevaluable,
            last_ingested_at=ingested,
            classifier_version=cfg.classifier_version,
        )
        verdict = classify_loan(loan, cfg)
        loan = loan.model_copy(
            update={"bridge_score": verdict.bridge_score, "is_bridge_loan": verdict.is_bridge_loan}
        )
        norm_lender = normalize_lender_name(lender)
        feature = LoanFeature(
            loan_id=loan_id,
            address_norm=normalize_address(rng.choice(addresses) + " Mattapoisett MA"),
            lender=norm_lender,
            lender_type=classify_lender_type(norm_lender),
            principal=Decimal(rng.randint(10_000, 200_000)) * 10,
            recorded_date=recorded,
            maturity_date=maturity,
            term_months=term,
            bridge_score=loan.bridge_score,
            is_bridge_loan=loan.is_bridge_loan,
            classifier_version=loan.classifier_version,
            signals=fired,
            geocode_status=status,
        )
        rows.append(
            SnapshotRow(
                loan=loan,
                feature=feature,
                borrower=rng.choice(people),
                doc_id=digest,
                recorded_book=str(rng.randint(1, 999)),
                recorded_page=str(rng.randint(1, 999)),
                source_url=f"https://portal.example.test/docs/{digest}.pdf",
            )
        )
    return rows


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _list_record(row: SnapshotRow) -> dict:
    rec = row.feature.model_dump()
    rec.update(
        lat=row.loan.lat,
        lon=row.loan.lon,
        town=TOWN,
        signals_unevaluable=list(row.loan.signals_unevaluable),
        last_ingested_at=row.loan.last_ingested_at,
    )
    return rec


def build_snapshot(rows: list[SnapshotRow], out_dir: Path | str, generated_at: str) -> Path:
    """Write list + detail Parquet and manifest.json into out_dir; returns out_dir."""
    if not rows:
        raise ValueError("a snapshot needs at least one row")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    list_tbl = pa.Table.from_pylist([_list_record(r) for r in rows], schema=_LIST_SCHEMA)
    detail_tbl = pa.Table.from_pylist(
        [
            {
                "loan_id": r.loan.loan_id,
                "borrower": r.borrower,
                "doc_id": r.doc_id,
                "recorded_book": r.recorded_book,
                "recorded_page": r.recorded_page,
                "source_url": r.source_url,
            }
            for r in rows
        ],
        schema=_DETAIL_SCHEMA,
    )
    pq.write_table(list_tbl, out / LIST_FILE, compression="snappy")
    pq.write_table(detail_tbl, out / DETAIL_FILE, compression="snappy")
    list_sha = _sha(out / LIST_FILE)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": list_sha[:16],
        "generated_at": generated_at,
        "classifier_version": rows[0].loan.classifier_version,
        "town": TOWN,
        "row_count": len(rows),
        "files": {
            "list": {"path": LIST_FILE, "sha256": list_sha, "rows": list_tbl.num_rows},
            "detail": {
                "path": DETAIL_FILE,
                "sha256": _sha(out / DETAIL_FILE),
                "rows": detail_tbl.num_rows,
            },
        },
    }
    (out / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return out


def validate_snapshot(directory: Path | str) -> int:
    """Check hashes, row counts and that every list row validates against the models."""
    d = Path(directory)
    manifest = json.loads((d / MANIFEST_FILE).read_text())
    for key, meta in manifest["files"].items():
        if _sha(d / meta["path"]) != meta["sha256"]:
            raise ValueError(f"sha256 mismatch for {key}")
    table = pq.read_table(d / LIST_FILE)
    if table.num_rows != manifest["row_count"]:
        raise ValueError("row_count mismatch")
    feature_fields = set(LoanFeature.model_fields)
    loan_fields = set(Loan.model_fields)
    for rec in table.to_pylist():
        LoanFeature(**{k: v for k, v in rec.items() if k in feature_fields})
        Loan(**{k: v for k, v in rec.items() if k in loan_fields})
    if pq.read_table(d / DETAIL_FILE).num_rows != table.num_rows:
        raise ValueError("detail row_count mismatch")
    return table.num_rows


def tree_hash(directory: Path | str) -> str:
    """Hash of all file paths + bytes under a directory (determinism checks)."""
    d = Path(directory)
    h = hashlib.sha256()
    for p in sorted(x for x in d.rglob("*") if x.is_file()):
        h.update(str(p.relative_to(d)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()
