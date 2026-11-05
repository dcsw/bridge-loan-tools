"""FastAPI dependencies: snapshot state, filters, audit, document fetcher."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import AfterValidator, ValidationError

from backend.api.documents import ObjectFetcher
from backend.api.errors import ApiProblem
from backend.api.schemas import LoanFilters, Town, parse_bbox
from backend.api.snapshot import SnapshotState, SnapshotUnavailable
from backend.common.models import LenderType


def _check_bbox(value: str | None) -> str | None:
    if value is not None:
        parse_bbox(value)
    return value


def unavailable(request: Request) -> ApiProblem:
    retry = request.app.state.settings.retry_after_seconds
    return ApiProblem(
        503,
        "snapshot_unavailable",
        "the loan snapshot is not available; retry shortly",
        headers={"Retry-After": str(retry)},
    )


def current_state(request: Request) -> SnapshotState:
    try:
        return request.app.state.store.current()
    except SnapshotUnavailable as exc:
        raise unavailable(request) from exc


def loan_filters(
    bbox: Annotated[
        str | None,
        AfterValidator(_check_bbox),
        Query(description="minLon,minLat,maxLon,maxLat (ordered, within world bounds)"),
    ] = None,
    town: Annotated[Town | None, Query()] = None,
    min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    lender_type: Annotated[LenderType | None, Query()] = None,
    recorded_from: Annotated[date | None, Query(description="ISO date, inclusive")] = None,
    recorded_to: Annotated[date | None, Query(description="ISO date, inclusive")] = None,
) -> LoanFilters:
    try:
        return LoanFilters(
            bbox=parse_bbox(bbox) if bbox else None,
            town=town,
            min_score=min_score,
            lender_type=lender_type,
            recorded_from=recorded_from,
            recorded_to=recorded_to,
        )
    except ValidationError as exc:
        errors = [
            {
                "type": "value_error",
                "loc": ("query", "recorded_from"),
                "msg": str(e["msg"]),
                "input": None,
            }
            for e in exc.errors()
        ]
        raise RequestValidationError(errors) from exc


StateDep = Annotated[SnapshotState, Depends(current_state)]
FiltersDep = Annotated[LoanFilters, Depends(loan_filters)]


def get_fetcher(request: Request) -> ObjectFetcher | None:
    return request.app.state.fetcher
