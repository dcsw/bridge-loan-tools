"""WU6 DoD 6: semantic_loans_v1 is defined in a versioned SQL file and equals silver loans."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import duckdb
import pyarrow as pa
import pytest
from silver_helpers import make_instrument

from backend.common.models import FieldValue, Loan, LoanFeature
from backend.common.snapshot import _DETAIL_SCHEMA, _LIST_SCHEMA
from backend.ingest import views
from backend.ingest.unify import UnifiedInstrument

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def doc(n):
    return f"{n:064x}"


def sample():
    fields = {
        "principal": FieldValue(value="100000.00", source_extractor="pymupdf", confidence=0.9)
    }
    m1 = make_instrument(
        0, doc_id=doc(1), fields=fields, maturity_date=date(2025, 1, 10), source_url="https://x/1"
    )
    m2 = make_instrument(
        0, doc_id=doc(2), town="rye", grantee="First Bank NA", lender_type="bank",
        principal=None, term_months=None, address_raw=None, address_norm=None, recorded_page="2",
    )  # fmt: skip
    d = make_instrument(
        0, doc_id=doc(3), doc_type="discharge", lender_type=None, principal=None, term_months=None
    )
    recs = [
        UnifiedInstrument(m1, "ACME CAPITAL LLC", "txt", T0),
        UnifiedInstrument(m2, "FIRST BANK NA", "txt", T0),
        UnifiedInstrument(d, None, "txt", T0),
    ]
    loans = [
        Loan(
            loan_id=m1.instrument_id, lat=41.6, lon=-70.8, geocode_status="matched",
            classifier_version="placeholder-all-true", signals=["short_term"],
            signals_unevaluable=["keyword"], last_ingested_at=T0,
        ),
        Loan(
            loan_id=m2.instrument_id, geocode_status="no_match",
            classifier_version="placeholder-all-true", signals=[],
            signals_unevaluable=["no_discharge", "second_lien"], last_ingested_at=T0,
        ),
    ]  # fmt: skip
    return recs, loans


def connect():
    recs, loans = sample()
    con = duckdb.connect()
    views.register_silver(con, views.loans_table(loans), views.instruments_table(recs))
    views.create_semantic_views(con)
    return con, recs, loans


def test_view_definition_lives_in_a_versioned_sql_file():
    sql = views.view_sql()
    assert views.SQL_PATH.name == "semantic_loans_v1.sql"
    assert "semantic_loans_v1" in sql and "silver_loans" in sql and "silver_instruments" in sql
    assert views.VIEW_NAME == "semantic_loans_v1"


def test_view_equals_silver_loans_row_for_row():
    con, recs, loans = connect()
    got = con.sql("select * from semantic_loans_v1 order by loan_id").to_arrow_table()
    by_id = {r.instrument.instrument_id: r.instrument for r in recs}
    lender = {r.instrument.instrument_id: r.lender_norm for r in recs}
    assert got.num_rows == len(loans)
    for row, loan in zip(got.to_pylist(), sorted(loans, key=lambda x: x.loan_id), strict=True):
        ins = by_id[loan.loan_id]
        expected_feature = LoanFeature(
            loan_id=loan.loan_id, address_norm=ins.address_norm, lender=lender[loan.loan_id],
            lender_type=ins.lender_type, principal=ins.principal,
            recorded_date=ins.recorded_date, maturity_date=ins.maturity_date,
            term_months=ins.term_months, bridge_score=loan.bridge_score,
            is_bridge_loan=loan.is_bridge_loan, classifier_version=loan.classifier_version,
            signals=loan.signals, geocode_status=loan.geocode_status,
        )  # fmt: skip
        feature_fields = set(LoanFeature.model_fields)
        assert LoanFeature(**{k: v for k, v in row.items() if k in feature_fields}) == (
            expected_feature
        )
        assert (row["lat"], row["lon"]) == (loan.lat, loan.lon)
        assert row["town"] == ins.town
        assert row["signals_unevaluable"] == loan.signals_unevaluable
        assert row["last_ingested_at"] == loan.last_ingested_at


def test_view_columns_match_the_published_list_schema_and_have_no_borrower():
    con, _, _ = connect()
    cols = [c[0] for c in con.sql("select * from semantic_loans_v1").description]
    assert cols == _LIST_SCHEMA.names
    assert "borrower" not in cols and "grantor" not in cols


def test_non_mortgage_instruments_are_not_loans():
    con, recs, _ = connect()
    ids = {r[0] for r in con.sql("select loan_id from semantic_loans_v1").fetchall()}
    assert doc(3) + "-0" not in ids and len(ids) == 2


def test_borrower_view_is_separate_and_carries_pii_and_fields_json():
    con, recs, _ = connect()
    got = con.sql(f"select * from {views.BORROWER_VIEW_NAME} order by loan_id").to_arrow_table()
    assert got.column_names == [*_DETAIL_SCHEMA.names, "fields_json"]
    first = got.to_pylist()[0]
    assert first["borrower"] == "Jane Doe" and first["source_url"] == "https://x/1"
    assert first["recorded_book"] == "1" and first["doc_id"] == doc(1)
    assert "principal" in first["fields_json"]


def test_semantic_view_can_be_recreated_on_a_fresh_connection():
    """DuckDB views do not persist across connections: consumers rebuild them from the file."""
    recs, loans = sample()
    for _ in range(2):
        con = duckdb.connect()
        views.register_silver(con, views.loans_table(loans), views.instruments_table(recs))
        views.create_semantic_views(con)
        assert con.execute("select count(*) from semantic_loans_v1").fetchone() == (2,)


# -- row converters -------------------------------------------------------------------------


def test_instrument_row_roundtrip_preserves_everything():
    recs, _ = sample()
    for r in recs:
        row = views.instrument_to_row(r)
        back = views.row_to_unified(row)
        assert back == r
        assert row["fields_json"] is not None


def test_loan_row_roundtrip_and_naive_timestamps_are_utc():
    _, loans = sample()
    for loan in loans:
        row = views.loan_to_row(loan)
        assert views.row_to_loan(row) == loan
        row["last_ingested_at"] = loan.last_ingested_at.replace(tzinfo=None)
        assert views.row_to_loan(row) == loan


def test_tables_have_the_declared_schemas_and_types():
    recs, loans = sample()
    t = views.instruments_table(recs)
    assert t.schema.field("principal").type == pa.decimal128(14, 2)
    assert t.schema.field("recorded_date").type == pa.date32()
    assert views.loans_table(loans).schema.field("signals").type == pa.list_(pa.string())
    assert views.instruments_table([]).num_rows == 0
    assert t.column("principal").to_pylist()[1] is None
    assert t.column("principal").to_pylist()[0] == Decimal("100000.00")


def test_spark_ddl_is_generated_from_the_same_column_spec():
    ddl = views.spark_ddl("lk.silver.instruments", views.INSTRUMENT_COLUMNS)
    assert "instrument_id STRING NOT NULL" in ddl and "principal DECIMAL(14,2)" in ddl
    assert "USING iceberg" in ddl and "'format-version'='2'" in ddl
    assert "ARRAY<STRING>" in views.spark_ddl("lk.silver.loans", views.LOAN_COLUMNS)


def test_unknown_column_kind_is_rejected():
    with pytest.raises(ValueError, match="kind"):
        views.spark_ddl("t", [("x", "weird")])
