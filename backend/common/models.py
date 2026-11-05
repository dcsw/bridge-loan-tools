"""Shared pydantic data contracts (design section 3.2 / 3.4)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DocType = Literal["mortgage", "deed", "discharge"]
LenderType = Literal["bank", "credit_union", "private_llc", "individual", "other", "unknown"]
GeocodeStatus = Literal["matched", "low_match", "no_match"]
GapReason = Literal["paid", "blocked", "failed"]
SignalName = Literal[
    "purchase_then_mortgage_90d",
    "second_lien",
    "short_term",
    "private_lender",
    "no_discharge",
    "keyword",
]

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Manifest(_Model):
    """One fetched (or gap) document row. doc_id = sha256 of the PDF bytes."""

    doc_id: str = Field(pattern=SHA256_PATTERN)
    source_url: str
    fetched_at: datetime
    town: str
    status: Literal["fetched", "gap"]
    gap_reason: GapReason | None = None
    size_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def _gap_needs_reason(self) -> Manifest:
        if self.status == "gap" and self.gap_reason is None:
            raise ValueError("gap_reason is required when status == 'gap'")
        return self


class BronzeRecord(_Model):
    """Per-extractor, per-page raw extraction output."""

    doc_id: str = Field(pattern=SHA256_PATTERN)
    extractor: str
    extractor_version: str
    page_no: int = Field(ge=1)
    text: str
    tables_json: str
    extracted_at: datetime
    status: Literal["ok", "partial", "failed"]
    error: str | None = None

    @model_validator(mode="after")
    def _failed_needs_error(self) -> BronzeRecord:
        if self.status == "failed" and not self.error:
            raise ValueError("error is required when status == 'failed'")
        return self


class FieldValue(_Model):
    """Per-field provenance for unified values."""

    value: Any = None
    source_extractor: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    disagreement: bool = False


class Instrument(_Model):
    """Silver instrument: one recorded instrument inside a document."""

    instrument_id: str
    doc_id: str = Field(pattern=SHA256_PATTERN)
    ordinal: int = Field(ge=0)
    doc_type: DocType
    town: str
    parcel_key: str | None = None
    address_raw: str | None = None
    address_norm: str | None = None
    grantor: str | None = None  # borrower on a mortgage (PII; never in list outputs)
    grantee: str | None = None  # lender on a mortgage
    lender_type: LenderType | None = None
    principal: Decimal | None = Field(default=None, ge=0)
    recorded_date: date | None = None
    maturity_date: date | None = None
    term_months: int | None = Field(default=None, ge=0)
    recorded_book: str | None = None
    recorded_page: str | None = None
    refers_to_ref: str | None = None  # discharges: "book/page" of the mortgage discharged
    source_url: str | None = None
    fields: dict[str, FieldValue] = Field(default_factory=dict)

    @property
    def recorded_ref(self) -> str | None:
        if self.recorded_book and self.recorded_page:
            return f"{self.recorded_book}/{self.recorded_page}"
        return None

    @model_validator(mode="after")
    def _consistent(self) -> Instrument:
        if self.instrument_id != f"{self.doc_id}-{self.ordinal}":
            raise ValueError("instrument_id must equal '<doc_id>-<ordinal>'")
        if self.recorded_date and self.maturity_date and self.maturity_date < self.recorded_date:
            raise ValueError("maturity_date precedes recorded_date")
        return self


class Loan(_Model):
    """Silver derived loan. loan_id = instrument_id of the mortgage."""

    loan_id: str
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    geocode_status: GeocodeStatus
    bridge_score: int = Field(default=100, ge=0, le=100)
    is_bridge_loan: bool = True
    classifier_version: str
    signals: list[SignalName] = Field(default_factory=list)
    signals_unevaluable: list[SignalName] = Field(default_factory=list)
    last_ingested_at: datetime

    @model_validator(mode="after")
    def _consistent(self) -> Loan:
        has_xy = self.lat is not None and self.lon is not None
        if self.geocode_status == "no_match" and (self.lat is not None or self.lon is not None):
            raise ValueError("no_match loans must not carry coordinates")
        if self.geocode_status != "no_match" and not has_xy:
            raise ValueError("matched/low_match loans require lat and lon")
        if set(self.signals) & set(self.signals_unevaluable):
            raise ValueError("a signal cannot be both fired and unevaluable")
        return self


class LoanFeature(_Model):
    """GeoJSON feature properties for GET /loans. Deliberately has NO borrower field."""

    loan_id: str
    address_norm: str | None
    lender: str | None
    lender_type: LenderType | None
    principal: Decimal | None
    recorded_date: date | None
    maturity_date: date | None
    term_months: int | None
    bridge_score: int = Field(ge=0, le=100)
    is_bridge_loan: bool
    classifier_version: str
    signals: list[SignalName]
    geocode_status: GeocodeStatus


class Problem(_Model):
    """RFC 7807 problem+json with machine code and per-field errors."""

    type: str
    title: str
    status: int = Field(ge=400, le=599)
    detail: str | None = None
    code: str | None = None
    errors: list[dict[str, str]] = Field(default_factory=list)
