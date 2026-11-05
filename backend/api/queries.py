"""Parameterized DuckDB queries over the published snapshot. No SQL is built from user input."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.api.errors import ApiProblem
from backend.api.logging_setup import register_sensitive
from backend.api.schemas import (
    Coverage,
    Feature,
    FeatureCollection,
    GapCounts,
    LoanDetail,
    LoanFeatureProperties,
    LoanFilters,
    Point,
    SourceRef,
    TownCoverage,
    score_band,
)
from backend.api.snapshot import SnapshotState
from backend.common.models import FieldValue

LIST_COLUMNS = (
    "loan_id, address_norm, lender, lender_type, principal, recorded_date, maturity_date, "
    "term_months, bridge_score, is_bridge_loan, classifier_version, signals, geocode_status, "
    "lat, lon"
)


def _from_epoch(seconds: float | None) -> datetime | None:
    # epoch() in SQL avoids DuckDB's TIMESTAMPTZ -> Python conversion, which needs pytz
    return None if seconds is None else datetime.fromtimestamp(seconds, UTC)


# -- cursor ---------------------------------------------------------------------------------


class CursorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    s: str  # snapshot id
    f: str  # filter hash
    k: tuple[int, str] = Field(description="(bridge_score, loan_id) of the last returned row")


def filter_hash(filters: LoanFilters) -> str:
    canonical = json.dumps(filters.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def encode_cursor(snapshot_id: str, filters: LoanFilters, last: tuple[int, str]) -> str:
    payload = CursorPayload(s=snapshot_id, f=filter_hash(filters), k=last)
    raw = payload.model_dump_json().encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, snapshot_id: str, filters: LoanFilters) -> tuple[int, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = CursorPayload.model_validate_json(raw)
    except (binascii.Error, ValueError, ValidationError) as exc:
        raise ApiProblem(
            400,
            "invalid_cursor",
            "cursor is malformed",
            errors=[{"field": "cursor", "message": "malformed cursor", "code": "invalid_cursor"}],
        ) from exc
    if payload.s != snapshot_id:
        raise ApiProblem(
            410, "stale_cursor", "the snapshot changed; restart pagination without a cursor"
        )
    if payload.f != filter_hash(filters):
        raise ApiProblem(
            400,
            "cursor_filter_mismatch",
            "cursor was issued for different filters",
            errors=[
                {"field": "cursor", "message": "filters changed", "code": "cursor_filter_mismatch"}
            ],
        )
    return payload.k


# -- list -----------------------------------------------------------------------------------


def _where(filters: LoanFilters, *, with_bbox: bool) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if with_bbox and filters.bbox is not None:
        min_lon, min_lat, max_lon, max_lat = filters.bbox
        clauses.append("(lon between ? and ? and lat between ? and ?)")
        params += [min_lon, max_lon, min_lat, max_lat]
    if filters.town is not None:
        clauses.append("town = ?")
        params.append(filters.town)
    if filters.min_score is not None:
        clauses.append("bridge_score >= ?")
        params.append(filters.min_score)
    if filters.lender_type is not None:
        clauses.append("lender_type = ?")
        params.append(filters.lender_type)
    if filters.recorded_from is not None:
        clauses.append("recorded_date >= ?")
        params.append(filters.recorded_from)
    if filters.recorded_to is not None:
        clauses.append("recorded_date <= ?")
        params.append(filters.recorded_to)
    return clauses, params


def _row_to_feature(row: tuple[Any, ...]) -> Feature:
    (
        loan_id,
        address_norm,
        lender,
        lender_type,
        principal,
        recorded_date,
        maturity_date,
        term_months,
        bridge_score,
        is_bridge_loan,
        classifier_version,
        signals,
        geocode_status,
        lat,
        lon,
    ) = row
    geometry = None if lat is None or lon is None else Point(coordinates=(lon, lat))
    props = LoanFeatureProperties(
        loan_id=loan_id,
        address_norm=address_norm,
        lender=lender,
        lender_type=lender_type,
        principal=principal,
        recorded_date=recorded_date,
        maturity_date=maturity_date,
        term_months=term_months,
        bridge_score=bridge_score,
        score_band=score_band(bridge_score),
        is_bridge_loan=is_bridge_loan,
        classifier_version=classifier_version,
        signals=signals or [],
        geocode_status=geocode_status,
    )
    return Feature(id=loan_id, geometry=geometry, properties=props)


def list_loans(
    state: SnapshotState, filters: LoanFilters, limit: int, cursor: str | None
) -> FeatureCollection:
    after = decode_cursor(cursor, state.snapshot_id, filters) if cursor else None
    conn = state.list_conn.cursor()
    try:
        clauses, params = _where(filters, with_bbox=True)
        where = " and ".join(clauses) or "true"
        total_row = conn.execute(f"select count(*) from loans where {where}", params).fetchone()
        un_clauses, un_params = _where(filters, with_bbox=False)
        un_where = " and ".join([*un_clauses, "geocode_status = 'no_match'"])
        un_row = conn.execute(f"select count(*) from loans where {un_where}", un_params).fetchone()
        page_clauses, page_params = list(clauses), list(params)
        if after is not None:
            page_clauses.append("(bridge_score < ? or (bridge_score = ? and loan_id > ?))")
            page_params += [after[0], after[0], after[1]]
        page_where = " and ".join(page_clauses) or "true"
        rows = conn.execute(
            f"select {LIST_COLUMNS} from loans where {page_where} "
            "order by bridge_score desc, loan_id asc limit ?",
            [*page_params, limit + 1],
        ).fetchall()
    finally:
        conn.close()
    page = rows[:limit]
    features = [_row_to_feature(r) for r in page]
    next_cursor = None
    if len(rows) > limit:
        last = features[-1].properties
        next_cursor = encode_cursor(state.snapshot_id, filters, (last.bridge_score, last.loan_id))
    return FeatureCollection(
        features=features,
        total=total_row[0] if total_row else 0,
        unmapped_count=un_row[0] if un_row else 0,
        next_cursor=next_cursor,
        generated_at=state.generated_at,
    )


# -- detail ---------------------------------------------------------------------------------


def get_detail(state: SnapshotState, loan_id: str) -> LoanDetail | None:
    lc = state.list_conn.cursor()
    try:
        row = lc.execute(
            f"select {LIST_COLUMNS}, signals_unevaluable, epoch(last_ingested_at) from loans "
            "where loan_id = ?",
            [loan_id],
        ).fetchone()
    finally:
        lc.close()
    if row is None:
        return None
    feature = _row_to_feature(row[:15])
    dc = state.detail_conn.cursor()
    try:
        cur = dc.execute(
            "select * from read_parquet(?) where loan_id = ?", [state.detail_path, loan_id]
        )
        cols = [c[0] for c in cur.description]
        drow = cur.fetchone()
    finally:
        dc.close()
    if drow is None:
        return None
    detail = dict(zip(cols, drow, strict=True))
    register_sensitive(detail.get("borrower"))
    fields = {
        k: FieldValue.model_validate(v)
        for k, v in json.loads(detail.get("fields_json") or "{}").items()
    }
    return LoanDetail(
        **feature.properties.model_dump(),
        borrower=detail.get("borrower"),
        signals_unevaluable=row[15] or [],
        fields=fields,
        source=SourceRef(
            doc_id=detail["doc_id"],
            recorded_book=detail.get("recorded_book"),
            recorded_page=detail.get("recorded_page"),
            source_url=detail.get("source_url"),
            document_path=f"/loans/{loan_id}/source",
        ),
        last_ingested_at=_from_epoch(row[16]),
    )


# -- coverage -------------------------------------------------------------------------------


def get_coverage(state: SnapshotState) -> Coverage:
    manifest = state.manifest
    if manifest.coverage is not None:
        towns = [
            TownCoverage(
                town=name,
                documents_fetched=entry.documents_fetched,
                gaps=GapCounts(**entry.gaps.model_dump()) if entry.gaps else None,
                last_ingested_at=entry.last_ingested_at,
            )
            for name, entry in manifest.coverage.items()
        ]
        return Coverage(generated_at=manifest.generated_at, towns=towns)
    conn = state.list_conn.cursor()
    try:
        rows = conn.execute(
            "select town, count(distinct regexp_replace(loan_id, '-[0-9]+$', '')), "
            "max(epoch(last_ingested_at)) from loans group by town order by town"
        ).fetchall()
    finally:
        conn.close()
    towns = [
        TownCoverage(
            town=t,
            documents_fetched=n,
            gaps=None,
            last_ingested_at=_from_epoch(ts).isoformat() if ts is not None else None,
        )
        for t, n, ts in rows
    ]
    return Coverage(generated_at=manifest.generated_at, towns=towns)
