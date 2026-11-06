"""Silver job: bronze -> unify -> silver.instruments MERGE -> per-parcel loans -> silver.loans.

    python -m backend.ingest.silver_job [--manifests /manifests] [--work /work]

Runs in the Spark ingest container (no internet, no secrets):

* bronze tables ``bronze.bronze_<extractor>`` are read through the Lakekeeper catalog; a missing
  table (extractor never ran / failed) is skipped and silver proceeds with the others.
* ``--manifests`` is a read-only directory of WU4 manifest rows (``<doc_id>.json``; export them
  from the MinIO ``raw`` bucket with ``make silver-manifests``): they provide town + source_url.
* ``--work`` is a writable directory: ``geocode-cache.jsonl`` (read) answers geocoding; addresses
  the cache cannot answer are written to ``geocode-requests.txt``. The host runs
  ``python -m backend.ingest.geocode`` (internet) to fill the cache, then silver is re-run.

The pipeline itself (:func:`run_silver`) is pure Python over a ``SilverStore``; the Spark
implementation (:class:`IcebergSilverStore`) is a thin MERGE INTO wrapper, so the logic is unit
tested with :class:`InMemoryStore` and the Spark path is covered by the RUN_STACK=1 live test.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from backend.common.models import BronzeRecord, Manifest
from backend.common.signals import ClassifierConfig, load_config
from backend.ingest import views
from backend.ingest.geocode import CachedGeocoder, Geocoder, JsonlGeocodeCache
from backend.ingest.loans import derive_loans, loans_scope
from backend.ingest.unify import DocMeta, Skipped, unify_records

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

log = logging.getLogger("backend.ingest.silver")
_ID = re.compile(r"^[0-9a-f]{64}-\d+$")


class SilverStore(Protocol):
    def read_instruments(self) -> list[dict[str, Any]]: ...
    def read_loans(self) -> list[dict[str, Any]]: ...
    def merge_instruments(self, rows: Sequence[Mapping[str, Any]]) -> None: ...
    def merge_loans(self, rows: Sequence[Mapping[str, Any]]) -> None: ...
    def delete_instruments(self, ids: Sequence[str]) -> None: ...
    def delete_loans(self, ids: Sequence[str]) -> None: ...


class InMemoryStore:
    """Reference ``SilverStore`` with MERGE semantics (upsert by key), used by the tests."""

    def __init__(self) -> None:
        self._instruments: dict[str, dict[str, Any]] = {}
        self._loans: dict[str, dict[str, Any]] = {}

    def read_instruments(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._instruments.values()]

    def read_loans(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._loans.values()]

    def merge_instruments(self, rows: Sequence[Mapping[str, Any]]) -> None:
        for r in rows:
            self._instruments[r["instrument_id"]] = dict(r)

    def merge_loans(self, rows: Sequence[Mapping[str, Any]]) -> None:
        for r in rows:
            self._loans[r["loan_id"]] = dict(r)

    def delete_instruments(self, ids: Sequence[str]) -> None:
        for i in ids:
            self._instruments.pop(i, None)

    def delete_loans(self, ids: Sequence[str]) -> None:
        for i in ids:
            self._loans.pop(i, None)


@dataclass
class SilverReport:
    instruments_merged: int = 0
    instruments_deleted: int = 0
    loans_recomputed: int = 0
    skipped: list[Skipped] = field(default_factory=list)
    geocode_pending: list[str] = field(default_factory=list)


def run_silver(
    records: Sequence[BronzeRecord],
    meta: Mapping[str, DocMeta],
    store: SilverStore,
    geocoder: Geocoder,
    cfg: ClassifierConfig | None = None,
) -> SilverReport:
    cfg = cfg or load_config()
    unified = unify_records(records, meta)
    report = SilverReport(skipped=list(unified.skipped))
    new_rows = [views.instrument_to_row(u) for u in unified.instruments]
    new_ids = {r["instrument_id"] for r in new_rows}
    replaced_docs = {r["doc_id"] for r in new_rows}  # only docs that produced instruments

    before_rows = store.read_instruments()
    stale = sorted(
        r["instrument_id"]
        for r in before_rows
        if r["doc_id"] in replaced_docs and r["instrument_id"] not in new_ids
    )
    store.delete_loans(stale)
    store.delete_instruments(stale)
    store.merge_instruments(new_rows)
    report.instruments_merged, report.instruments_deleted = len(new_rows), len(stale)

    after = [views.row_to_unified(r) for r in store.read_instruments()]
    mortgage_ids = {
        a.instrument.instrument_id for a in after if a.instrument.doc_type == "mortgage"
    }
    # An instrument re-classified (e.g. mortgage -> deed) must not leave a loan behind.
    store.delete_loans(
        sorted(r["loan_id"] for r in store.read_loans() if r["loan_id"] not in mortgage_ids)
    )
    before = [views.row_to_unified(r).instrument for r in before_rows]
    scope = loans_scope(before, [a.instrument for a in after], new_ids | set(stale))
    loans = derive_loans(after, geocoder=geocoder, cfg=cfg, scope=scope)
    store.merge_loans([views.loan_to_row(x) for x in loans])
    report.loans_recomputed = len(loans)
    report.geocode_pending = sorted(getattr(geocoder, "pending", set()))
    return report


# -- manifests ---------------------------------------------------------------------------------


def read_manifest_dir(path: Path) -> list[Manifest]:
    """WU4 manifest rows (``<doc_id>.json``); unreadable or invalid files are logged and skipped."""
    if not path.is_dir():
        return []
    rows: list[Manifest] = []
    for file in sorted(path.glob("*.json")):
        try:
            rows.append(Manifest.model_validate_json(file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            log.warning("skipping unreadable manifest row %s", file.name)
    return rows


def doc_meta_from_manifests(rows: Sequence[Manifest]) -> dict[str, DocMeta]:
    return {
        r.doc_id: DocMeta(town=r.town, source_url=r.source_url)
        for r in rows
        if r.status == "fetched"
    }


# -- Spark implementation ----------------------------------------------------------------------


def _spark_type(kind: str) -> Any:
    from pyspark.sql import types as t

    return {
        "string": t.StringType(),
        "int": t.IntegerType(),
        "double": t.DoubleType(),
        "bool": t.BooleanType(),
        "decimal": t.DecimalType(14, 2),
        "date": t.DateType(),
        "timestamp": t.TimestampType(),
        "array_string": t.ArrayType(t.StringType()),
    }[kind]


def _struct(columns: Sequence[views.Column]) -> Any:
    from pyspark.sql import types as t

    return t.StructType(
        [t.StructField(n, _spark_type(k), n not in views.NOT_NULL) for n, k in columns]
    )


class IcebergSilverStore:
    """MERGE INTO ``<catalog>.silver.{instruments,loans}`` keyed on instrument_id / loan_id."""

    def __init__(self, spark: SparkSession, catalog: str) -> None:
        self.spark, self.catalog = spark, catalog
        self.instruments = f"{catalog}.{views.NAMESPACE}.{views.INSTRUMENTS_TABLE}"
        self.loans = f"{catalog}.{views.NAMESPACE}.{views.LOANS_TABLE}"
        spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {catalog}.{views.NAMESPACE}")
        spark.sql(views.spark_ddl(self.instruments, views.INSTRUMENT_COLUMNS))
        spark.sql(views.spark_ddl(self.loans, views.LOAN_COLUMNS))

    def _rows(self, table: str) -> list[dict[str, Any]]:
        return [r.asDict() for r in self.spark.table(table).collect()]

    def read_instruments(self) -> list[dict[str, Any]]:
        return self._rows(self.instruments)

    def read_loans(self) -> list[dict[str, Any]]:
        return self._rows(self.loans)

    def _merge(
        self, table: str, key: str, columns: Sequence[views.Column], rows: Sequence[Mapping]
    ) -> None:
        if not rows:
            return
        view = f"_src_{table.rsplit('.', 1)[-1]}"
        frame = self.spark.createDataFrame(
            [tuple(r[name] for name, _ in columns) for r in rows], _struct(columns)
        )
        frame.createOrReplaceTempView(view)
        self.spark.sql(
            f"MERGE INTO {table} t USING {view} s ON t.{key} = s.{key} "
            "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *"
        )

    def merge_instruments(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._merge(self.instruments, "instrument_id", views.INSTRUMENT_COLUMNS, rows)

    def merge_loans(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._merge(self.loans, "loan_id", views.LOAN_COLUMNS, rows)

    def _delete(self, table: str, key: str, ids: Sequence[str]) -> None:
        bad = [i for i in ids if not _ID.match(i)]
        if bad:
            raise ValueError("refusing to build SQL from a malformed instrument id")
        if ids:
            quoted = ", ".join(f"'{i}'" for i in ids)
            self.spark.sql(f"DELETE FROM {table} WHERE {key} IN ({quoted})")

    def delete_instruments(self, ids: Sequence[str]) -> None:
        self._delete(self.instruments, "instrument_id", ids)

    def delete_loans(self, ids: Sequence[str]) -> None:
        self._delete(self.loans, "loan_id", ids)


def read_bronze(spark: SparkSession, catalog: str) -> list[BronzeRecord]:
    """All bronze records; a missing/unreadable extractor table is skipped (failure isolation)."""
    from backend.ingest.bronze import BRONZE_TABLES
    from backend.ingest.quarantine import NAMESPACE

    records: list[BronzeRecord] = []
    for extractor, table in BRONZE_TABLES.items():
        try:
            rows = spark.table(f"{catalog}.{NAMESPACE}.{table}").collect()
        except Exception as exc:  # isolation boundary: table absent or unreadable
            print(f"silver bronze extractor={extractor} state=skipped detail={type(exc).__name__}")
            continue
        for row in rows:
            data = row.asDict()
            data["extracted_at"] = data["extracted_at"].replace(tzinfo=UTC)
            records.append(BronzeRecord(**data))
        print(f"silver bronze extractor={extractor} state=read records={len(rows)}")
    return records


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.ingest.silver_job")
    parser.add_argument(
        "--manifests", type=Path, default=Path(os.environ.get("MANIFEST_DIR", "/manifests"))
    )
    parser.add_argument("--work", type=Path, default=Path(os.environ.get("SILVER_WORK", "/work")))
    args = parser.parse_args(argv)

    from backend.ingest.spark_session import CATALOG, build_spark

    meta = doc_meta_from_manifests(read_manifest_dir(args.manifests))
    cache = JsonlGeocodeCache(args.work / "geocode-cache.jsonl")
    geocoder = CachedGeocoder(cache, inner=None)
    spark = build_spark("bridge-loan-silver")
    try:
        report = run_silver(
            read_bronze(spark, CATALOG), meta, IcebergSilverStore(spark, CATALOG), geocoder
        )
    finally:
        spark.stop()
    if report.geocode_pending:
        args.work.mkdir(parents=True, exist_ok=True)
        (args.work / "geocode-requests.txt").write_text("\n".join(report.geocode_pending) + "\n")
    for s in report.skipped:
        print(f"silver skipped doc={s.doc_id} reason={s.reason}")
    print(
        f"silver instruments_merged={report.instruments_merged} "
        f"instruments_deleted={report.instruments_deleted} "
        f"loans_recomputed={report.loans_recomputed} geocode_pending={len(report.geocode_pending)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
