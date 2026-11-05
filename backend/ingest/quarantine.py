"""Load extractor output and route rejects to quarantine (design 3.1/3.2, WU5b).

Extractor output is ``data/extract-out/<extractor>/<name>.json``: a JSON list of
BronzeRecord-shaped dicts. ``load_extractor_dir`` is pure Python (no Spark): it never raises
for bad *content*, it turns every unusable file or record into a ``QuarantineRow``:

=====================  ==========================================================
reason_code            meaning
=====================  ==========================================================
``unreadable``         file could not be read or decoded as UTF-8
``malformed_json``     file is not valid JSON
``not_a_list``         top level is not a JSON list
``validation_error``   record is not a dict or fails ``BronzeRecord`` validation
``extractor_mismatch`` record.extractor differs from the directory's extractor
``status_failed``      extractor reported ``status=failed`` (reason = its error text)
``duplicate_key``      a second record for the same (doc_id, extractor, page_no)
=====================  ==========================================================

Only a missing / non-directory path raises (``FileNotFoundError`` / ``NotADirectoryError``);
the caller decides whether that skips or fails the extractor.

The Spark writer for the quarantine table is at the bottom (pyspark is imported lazily so the
loader and its tests run on hosts without Java).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from backend.common.models import BronzeRecord

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyspark.sql import SparkSession

NAMESPACE = "bronze"
QUARANTINE_TABLE = "quarantine"
MAX_PAYLOAD_CHARS = 4096
MAX_REASON_CHARS = 1024


@dataclass(frozen=True)
class QuarantineRow:
    """A rejected file (record_index -1) or record. Key: (extractor, source_file, record_index)."""

    extractor: str
    source_file: str
    record_index: int
    doc_id: str | None
    reason_code: str
    reason: str
    payload: str
    quarantined_at: datetime


@dataclass
class LoadResult:
    records: list[BronzeRecord] = field(default_factory=list)
    rejects: list[QuarantineRow] = field(default_factory=list)
    files_read: int = 0


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _reject(
    extractor: str,
    source_file: str,
    index: int,
    code: str,
    reason: str,
    payload: Any,
    doc_id: Any,
    now: datetime,
) -> QuarantineRow:
    raw = payload if isinstance(payload, str) else json.dumps(payload, default=str, sort_keys=True)
    return QuarantineRow(
        extractor=extractor,
        source_file=source_file,
        record_index=index,
        doc_id=doc_id if isinstance(doc_id, str) else None,
        reason_code=code,
        reason=_truncate(reason, MAX_REASON_CHARS),
        payload=_truncate(raw, MAX_PAYLOAD_CHARS),
        quarantined_at=now,
    )


def load_extractor_dir(extractor: str, directory: Path, now: datetime | None = None) -> LoadResult:
    """Read every top-level ``*.json`` in ``directory``; see module docstring for routing."""
    now = now or datetime.now(UTC)
    if not directory.exists():
        raise FileNotFoundError(f"extractor output directory missing: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"extractor output path is not a directory: {directory}")

    result = LoadResult()
    seen: set[tuple[str, str, int]] = set()
    for path in sorted(p for p in directory.glob("*.json") if p.is_file()):
        result.files_read += 1
        name = path.name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            result.rejects.append(
                _reject(
                    extractor, name, -1, "unreadable", f"{type(exc).__name__}: {exc}", "", None, now
                )
            )
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            result.rejects.append(
                _reject(extractor, name, -1, "malformed_json", str(exc), text, None, now)
            )
            continue
        if not isinstance(data, list):
            result.rejects.append(
                _reject(
                    extractor,
                    name,
                    -1,
                    "not_a_list",
                    f"top level is {type(data).__name__}",
                    text,
                    None,
                    now,
                )
            )
            continue
        for index, item in enumerate(data):
            doc_id = item.get("doc_id") if isinstance(item, dict) else None
            try:
                record = BronzeRecord.model_validate(item)
            except ValidationError as exc:
                result.rejects.append(
                    _reject(extractor, name, index, "validation_error", str(exc), item, doc_id, now)
                )
                continue
            if record.extractor != extractor:
                reason = f"record extractor {record.extractor!r} != directory {extractor!r}"
                result.rejects.append(
                    _reject(extractor, name, index, "extractor_mismatch", reason, item, doc_id, now)
                )
            elif record.status == "failed":
                result.rejects.append(
                    _reject(
                        extractor,
                        name,
                        index,
                        "status_failed",
                        record.error or "",
                        item,
                        doc_id,
                        now,
                    )
                )
            elif (key := (record.doc_id, record.extractor, record.page_no)) in seen:
                reason = f"duplicate key {key}; first occurrence kept"
                result.rejects.append(
                    _reject(extractor, name, index, "duplicate_key", reason, item, doc_id, now)
                )
            else:
                seen.add(key)
                result.records.append(record)
    return result


# --- Spark writer (lazy pyspark import) -----------------------------------------------------

_DDL = f"""
CREATE TABLE IF NOT EXISTS {{catalog}}.{NAMESPACE}.{QUARANTINE_TABLE} (
  extractor STRING NOT NULL,
  source_file STRING NOT NULL,
  record_index INT NOT NULL,
  doc_id STRING,
  reason_code STRING NOT NULL,
  reason STRING NOT NULL,
  payload STRING,
  quarantined_at TIMESTAMP NOT NULL
) USING iceberg
TBLPROPERTIES ('format-version'='2', 'write.merge.mode'='copy-on-write')
"""


def write_quarantine(spark: SparkSession, catalog: str, rows: Sequence[QuarantineRow]) -> int:
    """MERGE rows into ``<catalog>.bronze.quarantine`` on (extractor, source_file, record_index).

    ``quarantined_at`` keeps the first-seen time, so a re-run changes no values.
    """
    from pyspark.sql.types import (
        IntegerType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    if not rows:
        return 0
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {catalog}.{NAMESPACE}")
    spark.sql(_DDL.format(catalog=catalog))
    schema = StructType(
        [
            StructField("extractor", StringType(), False),
            StructField("source_file", StringType(), False),
            StructField("record_index", IntegerType(), False),
            StructField("doc_id", StringType(), True),
            StructField("reason_code", StringType(), False),
            StructField("reason", StringType(), False),
            StructField("payload", StringType(), True),
            StructField("quarantined_at", TimestampType(), False),
        ]
    )
    data = [
        (
            r.extractor,
            r.source_file,
            r.record_index,
            r.doc_id,
            r.reason_code,
            r.reason,
            r.payload,
            r.quarantined_at,
        )
        for r in rows
    ]
    spark.createDataFrame(data, schema).createOrReplaceTempView("_quarantine_src")
    spark.sql(
        f"""
        MERGE INTO {catalog}.{NAMESPACE}.{QUARANTINE_TABLE} t
        USING _quarantine_src s
        ON t.extractor = s.extractor AND t.source_file = s.source_file
           AND t.record_index = s.record_index
        WHEN MATCHED THEN UPDATE SET
          t.doc_id = s.doc_id, t.reason_code = s.reason_code, t.reason = s.reason,
          t.payload = s.payload
        WHEN NOT MATCHED THEN INSERT *
        """
    )
    return len(rows)
