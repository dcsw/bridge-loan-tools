"""API request/response schemas (OpenAPI is the source of truth, design 3.4)."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.common.models import FieldValue, GeocodeStatus, LenderType, LoanFeature, SignalName

Town = Literal["mattapoisett", "rye", "madison"]
ScoreBand = Literal["low", "mid", "high"]

DEFAULT_LIMIT = 500
MAX_LIMIT = 2000
LON_RANGE = (-180.0, 180.0)
LAT_RANGE = (-90.0, 90.0)


def score_band(score: int) -> ScoreBand:
    """0-39 low, 40-69 mid, 70-100 high."""
    if not 0 <= score <= 100:
        raise ValueError("bridge_score must be within 0..100")
    if score < 40:
        return "low"
    return "mid" if score < 70 else "high"


class LoanFeatureProperties(LoanFeature):
    """GeoJSON feature properties for GET /loans (PII-free common model plus score band)."""

    score_band: ScoreBand


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float] = Field(description="[longitude, latitude]")


class Feature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: Point | None = Field(description="null for loans that could not be geocoded")
    properties: LoanFeatureProperties


class FeatureCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[Feature]
    total: int = Field(ge=0, description="Rows matching all filters (bbox included)")
    unmapped_count: int = Field(
        ge=0,
        description="Rows matching all filters except bbox that have no geometry (no_match)",
    )
    next_cursor: str | None
    generated_at: str = Field(description="Snapshot generation time (ISO 8601)")


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str
    recorded_book: str | None
    recorded_page: str | None
    source_url: str | None
    document_path: str = Field(description="Authenticated API path streaming the source PDF")


class LoanDetail(LoanFeatureProperties):
    """GET /loans/{loan_id}: list properties plus borrower (PII), provenance and source."""

    borrower: str | None
    signals_unevaluable: list[SignalName]
    fields: dict[str, FieldValue] = Field(
        description="Per-field value/source_extractor/confidence/disagreement"
    )
    source: SourceRef
    last_ingested_at: datetime | None = None


class GapCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paid: int = Field(ge=0)
    blocked: int = Field(ge=0)
    failed: int = Field(ge=0)


class TownCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    town: str
    documents_fetched: int = Field(ge=0)
    gaps: GapCounts | None = Field(description="null when the snapshot carries no gap data")
    last_ingested_at: str | None


class Coverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generated_at: str
    towns: list[TownCoverage]


class FieldError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    message: str
    code: str


class Problem(BaseModel):
    """RFC 7807 problem+json with a machine code and per-field errors."""

    model_config = ConfigDict(extra="forbid")
    type: str
    title: str
    status: int
    detail: str | None = None
    code: str
    errors: list[FieldError] = Field(default_factory=list)


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]
    snapshot_id: str | None = None


class LoanFilters(BaseModel):
    """Validated, canonical list filters (also the cursor's filter-hash input)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    bbox: tuple[float, float, float, float] | None = None
    town: Town | None = None
    min_score: int | None = Field(default=None, ge=0, le=100)
    lender_type: LenderType | None = None
    recorded_from: date | None = None
    recorded_to: date | None = None

    @model_validator(mode="after")
    def _ordered(self) -> LoanFilters:
        if self.recorded_from and self.recorded_to and self.recorded_from > self.recorded_to:
            raise ValueError("recorded_from must not be after recorded_to")
        return self


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    """``minLon,minLat,maxLon,maxLat``: four finite numbers, ordered, within world bounds."""
    parts = raw.split(",")
    if len(parts) != 4:
        raise ValueError("bbox must be minLon,minLat,maxLon,maxLat")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    except ValueError as exc:
        raise ValueError("bbox values must be numbers") from exc
    if not all(math.isfinite(v) for v in (min_lon, min_lat, max_lon, max_lat)):
        raise ValueError("bbox values must be finite")
    if not (LON_RANGE[0] <= min_lon <= max_lon <= LON_RANGE[1]):
        raise ValueError("bbox longitudes must be ordered and within -180..180")
    if not (LAT_RANGE[0] <= min_lat <= max_lat <= LAT_RANGE[1]):
        raise ValueError("bbox latitudes must be ordered and within -90..90")
    return min_lon, min_lat, max_lon, max_lat


LoanIdPath = Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")]

BboxQuery = Annotated[
    str | None,
    Query(description="minLon,minLat,maxLon,maxLat (ordered, within world bounds)"),
]

__all__ = [
    "GeocodeStatus",
    "LoanIdPath",
    "BboxQuery",
]
