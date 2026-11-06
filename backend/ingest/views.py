"""Silver table layout, row converters, and the ``semantic_loans_v1`` view loader (WU6).

One column spec (:data:`INSTRUMENT_COLUMNS`, :data:`LOAN_COLUMNS`) drives the Spark DDL, the
Arrow schemas used by publish/tests, and the row converters, so they cannot drift apart.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.common.models import FieldValue, Instrument, Loan
from backend.ingest.unify import UnifiedInstrument

if TYPE_CHECKING:  # duckdb/pyarrow are not installed in the Spark ingest image: import lazily
    import duckdb
    import pyarrow as pa

SQL_PATH = Path(__file__).resolve().parent / "sql" / "semantic_loans_v1.sql"
VIEW_NAME = "semantic_loans_v1"
BORROWER_VIEW_NAME = "semantic_loans_v1_borrower"
NAMESPACE = "silver"
INSTRUMENTS_TABLE = "instruments"
LOANS_TABLE = "loans"

Column = tuple[str, str]
INSTRUMENT_COLUMNS: list[Column] = [
    ("instrument_id", "string"),
    ("doc_id", "string"),
    ("ordinal", "int"),
    ("doc_type", "string"),
    ("town", "string"),
    ("parcel_key", "string"),
    ("address_raw", "string"),
    ("address_norm", "string"),
    ("grantor", "string"),
    ("grantee", "string"),
    ("lender_norm", "string"),
    ("lender_type", "string"),
    ("principal", "decimal"),
    ("recorded_date", "date"),
    ("maturity_date", "date"),
    ("term_months", "int"),
    ("recorded_book", "string"),
    ("recorded_page", "string"),
    ("refers_to_ref", "string"),
    ("source_url", "string"),
    ("fields_json", "string"),
    ("source_text", "string"),
    ("ingested_at", "timestamp"),
]
LOAN_COLUMNS: list[Column] = [
    ("loan_id", "string"),
    ("lat", "double"),
    ("lon", "double"),
    ("geocode_status", "string"),
    ("bridge_score", "int"),
    ("is_bridge_loan", "bool"),
    ("classifier_version", "string"),
    ("signals", "array_string"),
    ("signals_unevaluable", "array_string"),
    ("last_ingested_at", "timestamp"),
]
NOT_NULL = frozenset(
    {
        "instrument_id", "doc_id", "ordinal", "doc_type", "town", "ingested_at",
        "loan_id", "geocode_status", "bridge_score", "is_bridge_loan", "classifier_version",
        "last_ingested_at",
    }
)  # fmt: skip

_SPARK = {
    "string": "STRING",
    "int": "INT",
    "double": "DOUBLE",
    "bool": "BOOLEAN",
    "decimal": "DECIMAL(14,2)",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "array_string": "ARRAY<STRING>",
}


def arrow_schema(columns: Sequence[Column]) -> pa.Schema:
    import pyarrow as pa

    types = {
        "string": pa.string(),
        "int": pa.int32(),
        "double": pa.float64(),
        "bool": pa.bool_(),
        "decimal": pa.decimal128(14, 2),
        "date": pa.date32(),
        "timestamp": pa.timestamp("us", tz="UTC"),
        "array_string": pa.list_(pa.string()),
    }
    return pa.schema(
        [pa.field(name, types[kind], nullable=name not in NOT_NULL) for name, kind in columns]
    )


def spark_ddl(table: str, columns: Sequence[Column]) -> str:
    """Iceberg v2 copy-on-write DDL (design 3.1: avoids delete files for DuckDB readers)."""
    unknown = [kind for _, kind in columns if kind not in _SPARK]
    if unknown:
        raise ValueError(f"unsupported column kind(s): {unknown}")
    body = ",\n  ".join(
        f"{name} {_SPARK[kind]}{' NOT NULL' if name in NOT_NULL else ''}" for name, kind in columns
    )
    return (
        f"CREATE TABLE IF NOT EXISTS {table} (\n  {body}\n) USING iceberg\n"
        "TBLPROPERTIES ('format-version'='2', 'write.merge.mode'='copy-on-write')"
    )


# -- row converters ---------------------------------------------------------------------------


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def instrument_to_row(rec: UnifiedInstrument) -> dict[str, Any]:
    ins = rec.instrument
    return {
        "instrument_id": ins.instrument_id,
        "doc_id": ins.doc_id,
        "ordinal": ins.ordinal,
        "doc_type": ins.doc_type,
        "town": ins.town,
        "parcel_key": ins.parcel_key,
        "address_raw": ins.address_raw,
        "address_norm": ins.address_norm,
        "grantor": ins.grantor,
        "grantee": ins.grantee,
        "lender_norm": rec.lender_norm,
        "lender_type": ins.lender_type,
        "principal": ins.principal,
        "recorded_date": ins.recorded_date,
        "maturity_date": ins.maturity_date,
        "term_months": ins.term_months,
        "recorded_book": ins.recorded_book,
        "recorded_page": ins.recorded_page,
        "refers_to_ref": ins.refers_to_ref,
        "source_url": ins.source_url,
        "fields_json": json.dumps(
            {k: v.model_dump(mode="json") for k, v in ins.fields.items()}, sort_keys=True
        ),
        "source_text": rec.text,
        "ingested_at": _utc(rec.ingested_at),
    }


def row_to_unified(row: Mapping[str, Any]) -> UnifiedInstrument:
    fields = {
        k: FieldValue.model_validate(v) for k, v in json.loads(row["fields_json"] or "{}").items()
    }
    instrument = Instrument(
        **{k: row[k] for k in Instrument.model_fields if k != "fields" and k in row},
        fields=fields,
    )
    return UnifiedInstrument(
        instrument, row["lender_norm"], row["source_text"] or "", _utc(row["ingested_at"])
    )


def loan_to_row(loan: Loan) -> dict[str, Any]:
    row = loan.model_dump()
    row["last_ingested_at"] = _utc(loan.last_ingested_at)
    return {name: row[name] for name, _ in LOAN_COLUMNS}


def row_to_loan(row: Mapping[str, Any]) -> Loan:
    data = {name: row[name] for name, _ in LOAN_COLUMNS}
    data["signals"] = list(data["signals"] or [])
    data["signals_unevaluable"] = list(data["signals_unevaluable"] or [])
    data["last_ingested_at"] = _utc(data["last_ingested_at"])
    return Loan(**data)


def table_from_rows(rows: Sequence[Mapping[str, Any]], columns: Sequence[Column]) -> pa.Table:
    import pyarrow as pa

    return pa.Table.from_pylist([dict(r) for r in rows], schema=arrow_schema(columns))


def instruments_table(records: Sequence[UnifiedInstrument]) -> pa.Table:
    import pyarrow as pa

    return pa.Table.from_pylist(
        [instrument_to_row(r) for r in records], schema=arrow_schema(INSTRUMENT_COLUMNS)
    )


def loans_table(loans: Sequence[Loan]) -> pa.Table:
    import pyarrow as pa

    return pa.Table.from_pylist([loan_to_row(x) for x in loans], schema=arrow_schema(LOAN_COLUMNS))


# -- the semantic view ------------------------------------------------------------------------


def view_sql() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def register_silver(con: duckdb.DuckDBPyConnection, loans: pa.Table, instruments: pa.Table) -> None:
    """Expose the relations the view file expects (silver_loans, silver_instruments)."""
    con.register("silver_loans", loans)
    con.register("silver_instruments", instruments)


def create_semantic_views(con: duckdb.DuckDBPyConnection) -> None:
    """(Re)create ``semantic_loans_v1`` and its borrower companion from the versioned file."""
    con.execute(view_sql())
