"""Models validate good fixtures and reject bad ones."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.common.models import (
    BronzeRecord,
    FieldValue,
    Instrument,
    Loan,
    LoanFeature,
    Manifest,
    Problem,
)

SHA = "a" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_instrument(**kw):
    base = {
        "instrument_id": f"{SHA}-0",
        "doc_id": SHA,
        "ordinal": 0,
        "doc_type": "mortgage",
        "town": "mattapoisett",
    }
    base.update(kw)
    return Instrument(**base)


def test_manifest_good_and_bad():
    m = Manifest(
        doc_id=SHA,
        source_url="https://example.test/a.pdf",
        fetched_at=NOW,
        town="mattapoisett",
        status="fetched",
        size_bytes=10,
    )
    assert m.gap_reason is None
    with pytest.raises(ValidationError):
        Manifest(
            doc_id="nothex",
            source_url="x",
            fetched_at=NOW,
            town="t",
            status="fetched",
            size_bytes=1,
        )
    with pytest.raises(ValidationError):
        Manifest(
            doc_id=SHA,
            source_url="x",
            fetched_at=NOW,
            town="t",
            status="gap",
            gap_reason="bogus",
            size_bytes=0,
        )
    with pytest.raises(ValidationError):  # a gap needs a reason
        Manifest(doc_id=SHA, source_url="x", fetched_at=NOW, town="t", status="gap", size_bytes=0)
    gap = Manifest(
        doc_id=SHA,
        source_url="x",
        fetched_at=NOW,
        town="t",
        status="gap",
        gap_reason="paid",
        size_bytes=0,
    )
    assert gap.gap_reason == "paid"


def test_bronze_record():
    b = BronzeRecord(
        doc_id=SHA,
        extractor="pdfplumber",
        extractor_version="1",
        page_no=1,
        text="t",
        tables_json="[]",
        extracted_at=NOW,
        status="ok",
    )
    assert b.error is None
    with pytest.raises(ValidationError):
        BronzeRecord(
            doc_id=SHA,
            extractor="e",
            extractor_version="1",
            page_no=0,
            text="",
            tables_json="[]",
            extracted_at=NOW,
            status="ok",
        )
    with pytest.raises(ValidationError):
        BronzeRecord(
            doc_id=SHA,
            extractor="e",
            extractor_version="1",
            page_no=1,
            text="",
            tables_json="[]",
            extracted_at=NOW,
            status="failed",
        )  # failed needs error


def test_field_value_bounds():
    FieldValue(value="x", source_extractor="a", confidence=0.9, disagreement=False)
    with pytest.raises(ValidationError):
        FieldValue(value="x", source_extractor="a", confidence=1.5, disagreement=False)


def test_instrument_good_and_bad():
    i = make_instrument(
        principal=Decimal("100000.00"),
        recorded_date=date(2024, 1, 2),
        lender_type="bank",
        recorded_book="1",
        recorded_page="2",
    )
    assert i.recorded_ref == "1/2"
    assert make_instrument().recorded_ref is None
    with pytest.raises(ValidationError):
        make_instrument(doc_type="lease")
    with pytest.raises(ValidationError):
        make_instrument(lender_type="martian")
    with pytest.raises(ValidationError):
        make_instrument(principal=Decimal("-1"))
    with pytest.raises(ValidationError):
        make_instrument(ordinal=-1)
    with pytest.raises(ValidationError):  # id must be doc_id + ordinal
        make_instrument(instrument_id="other")
    with pytest.raises(ValidationError):
        make_instrument(recorded_date=date(2024, 1, 2), maturity_date=date(2023, 1, 1))


def make_loan(**kw):
    base = {
        "loan_id": f"{SHA}-0",
        "geocode_status": "matched",
        "lat": 41.6,
        "lon": -70.8,
        "last_ingested_at": NOW,
        "signals": ["short_term"],
        "signals_unevaluable": ["keyword"],
        "classifier_version": "placeholder-all-true",
    }
    base.update(kw)
    return Loan(**base)


def test_loan_defaults_and_bad():
    loan = make_loan()
    assert loan.is_bridge_loan is True and loan.bridge_score == 100
    with pytest.raises(ValidationError):
        make_loan(bridge_score=101)
    with pytest.raises(ValidationError):
        make_loan(signals=["nope"])
    with pytest.raises(ValidationError):
        make_loan(lat=91.0)
    with pytest.raises(ValidationError):  # matched needs coordinates
        make_loan(lat=None, lon=None)
    nm = make_loan(geocode_status="no_match", lat=None, lon=None)
    assert nm.lat is None
    with pytest.raises(ValidationError):  # no_match must not carry coordinates
        make_loan(geocode_status="no_match")
    with pytest.raises(ValidationError):  # signal both fired and unevaluable
        make_loan(signals=["keyword"], signals_unevaluable=["keyword"])


def test_loan_feature_forbids_borrower():
    kw = {
        "loan_id": "x",
        "address_norm": "1 MAIN ST",
        "lender": "ACME",
        "lender_type": "bank",
        "principal": Decimal("1"),
        "recorded_date": date(2024, 1, 1),
        "maturity_date": None,
        "term_months": 12,
        "bridge_score": 100,
        "signals": [],
        "geocode_status": "matched",
        "classifier_version": "placeholder-all-true",
        "is_bridge_loan": True,
    }
    LoanFeature(**kw)
    with pytest.raises(ValidationError):
        LoanFeature(**kw, borrower="Jane Doe")
    assert "borrower" not in LoanFeature.model_fields


def test_problem():
    p = Problem(
        type="about:blank",
        title="Bad",
        status=400,
        code="invalid_param",
        errors=[{"field": "bbox", "message": "bad"}],
    )
    assert p.status == 400
    with pytest.raises(ValidationError):
        Problem(type="about:blank", title="Bad", status=200)
