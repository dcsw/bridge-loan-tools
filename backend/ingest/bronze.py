"""Bronze job: extract-out JSON -> one Iceberg table per extractor (WU5b).

    python -m backend.ingest.bronze [--extract-out /extract-out] [--extractors a,b]

* Tables ``bronze.bronze_<extractor>`` in the Lakekeeper warehouse, key (doc_id, extractor,
  page_no), written with ``MERGE INTO`` so re-running the same input changes nothing.
* ``status=failed`` / malformed / invalid records go to ``bronze.quarantine`` (quarantine.py).
  ``partial`` records (e.g. ``needs_ocr``) are kept in bronze with their status.
* Isolation: each extractor is processed in its own try block. A missing directory is
  ``skipped``; any other error marks only that extractor ``failed`` (exit code 1 at the end).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from backend.common.models import BronzeRecord
from backend.ingest.quarantine import (
    NAMESPACE,
    LoadResult,
    QuarantineRow,
    load_extractor_dir,
    write_quarantine,
)

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

BRONZE_TABLES: dict[str, str] = {
    "docling": "bronze_docling",
    "pymupdf": "bronze_pymupdf",
    "unstructured": "bronze_unstructured",
}
EXTRACTORS: tuple[str, ...] = tuple(BRONZE_TABLES)

State = Literal["written", "skipped", "failed"]


class Sink(Protocol):
    def write_bronze(self, extractor: str, records: Sequence[BronzeRecord]) -> int: ...
    def write_quarantine(self, rows: Sequence[QuarantineRow]) -> int: ...


@dataclass(frozen=True)
class ExtractorOutcome:
    extractor: str
    state: State
    records: int = 0
    quarantined: int = 0
    detail: str = ""


_BRONZE_DDL = """
CREATE TABLE IF NOT EXISTS {catalog}.{ns}.{table} (
  doc_id STRING NOT NULL,
  extractor STRING NOT NULL,
  extractor_version STRING NOT NULL,
  page_no INT NOT NULL,
  text STRING NOT NULL,
  tables_json STRING NOT NULL,
  extracted_at TIMESTAMP NOT NULL,
  status STRING NOT NULL,
  error STRING
) USING iceberg
TBLPROPERTIES ('format-version'='2', 'write.merge.mode'='copy-on-write')
"""


class IcebergSink:
    """Writes through the Spark Iceberg REST catalog."""

    def __init__(self, spark: SparkSession, catalog: str) -> None:
        self.spark, self.catalog = spark, catalog

    def write_bronze(self, extractor: str, records: Sequence[BronzeRecord]) -> int:
        from pyspark.sql.types import (
            IntegerType,
            StringType,
            StructField,
            StructType,
            TimestampType,
        )

        table = BRONZE_TABLES[extractor]
        self.spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {self.catalog}.{NAMESPACE}")
        self.spark.sql(_BRONZE_DDL.format(catalog=self.catalog, ns=NAMESPACE, table=table))
        if not records:
            return 0
        schema = StructType(
            [
                StructField("doc_id", StringType(), False),
                StructField("extractor", StringType(), False),
                StructField("extractor_version", StringType(), False),
                StructField("page_no", IntegerType(), False),
                StructField("text", StringType(), False),
                StructField("tables_json", StringType(), False),
                StructField("extracted_at", TimestampType(), False),
                StructField("status", StringType(), False),
                StructField("error", StringType(), True),
            ]
        )
        rows = [
            (
                r.doc_id, r.extractor, r.extractor_version, r.page_no, r.text,
                r.tables_json, r.extracted_at, r.status, r.error,
            )
            for r in records
        ]  # fmt: skip
        view = f"_bronze_src_{extractor}"
        self.spark.createDataFrame(rows, schema).createOrReplaceTempView(view)
        self.spark.sql(
            f"""
            MERGE INTO {self.catalog}.{NAMESPACE}.{table} t
            USING {view} s
            ON t.doc_id = s.doc_id AND t.extractor = s.extractor AND t.page_no = s.page_no
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
            """
        )
        return len(rows)

    def write_quarantine(self, rows: Sequence[QuarantineRow]) -> int:
        return write_quarantine(self.spark, self.catalog, rows)


def run(
    extract_out: Path,
    extractors: Sequence[str],
    sink: Sink,
    loader: Callable[[str, Path], LoadResult] = load_extractor_dir,
) -> list[ExtractorOutcome]:
    unknown = [e for e in extractors if e not in BRONZE_TABLES]
    if unknown:
        raise ValueError(f"unknown extractor(s): {unknown}; known: {list(BRONZE_TABLES)}")
    outcomes: list[ExtractorOutcome] = []
    for extractor in extractors:
        try:
            loaded = loader(extractor, extract_out / extractor)
            quarantined = sink.write_quarantine(loaded.rejects) if loaded.rejects else 0
            written = sink.write_bronze(extractor, loaded.records)
            outcomes.append(ExtractorOutcome(extractor, "written", written, quarantined))
        except FileNotFoundError as exc:
            outcomes.append(ExtractorOutcome(extractor, "skipped", detail=str(exc)))
        except Exception as exc:  # isolation boundary: one extractor must not block the rest
            outcomes.append(
                ExtractorOutcome(extractor, "failed", detail=f"{type(exc).__name__}: {exc}")
            )
    return outcomes


def exit_code(outcomes: Sequence[ExtractorOutcome]) -> int:
    return 1 if any(o.state == "failed" for o in outcomes) else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.ingest.bronze")
    parser.add_argument(
        "--extract-out", type=Path, default=Path(os.environ.get("EXTRACT_OUT", "/extract-out"))
    )
    parser.add_argument("--extractors", default=",".join(EXTRACTORS), help="comma separated")
    args = parser.parse_args(argv)

    from backend.ingest.spark_session import CATALOG, build_spark

    spark = build_spark()
    try:
        outcomes = run(
            args.extract_out,
            [e for e in args.extractors.split(",") if e],
            IcebergSink(spark, CATALOG),
        )
    finally:
        spark.stop()
    for o in outcomes:
        print(
            f"bronze extractor={o.extractor} state={o.state} records={o.records} "
            f"quarantined={o.quarantined} detail={o.detail}",
            flush=True,
        )
    return exit_code(outcomes)


if __name__ == "__main__":
    sys.exit(main())
