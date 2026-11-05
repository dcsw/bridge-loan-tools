"""RFC 7807 problem+json errors with machine ``code`` and per-field ``errors[]``."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.schemas import Problem

PROBLEM_MEDIA_TYPE = "application/problem+json"
TYPE_BASE = "https://bridge-loan.invalid/problems/"

TITLES = {
    400: "Invalid request",
    401: "Authentication required",
    404: "Not found",
    405: "Method not allowed",
    410: "Gone",
    413: "Response too large",
    422: "Unprocessable request",
    500: "Internal error",
    502: "Bad gateway",
    503: "Service unavailable",
}
DESCRIPTIONS = {
    400: "Invalid parameters or cursor (problem+json with errors[])",
    401: "Missing or invalid gateway credentials",
    404: "Unknown loan or document",
    410: "Cursor refers to a superseded snapshot; restart pagination",
    413: "Response would exceed the size cap",
    502: "Stored document is not a valid PDF",
    503: "Snapshot or dependency unavailable (Retry-After set for snapshot outages)",
}
HTTP_CODES = {404: "not_found", 405: "method_not_allowed", 401: "unauthorized"}


class ApiProblem(Exception):
    """Raise anywhere to produce a problem+json response."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str | None = None,
        *,
        title: str | None = None,
        errors: Sequence[dict[str, str]] = (),
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code
        self.detail = detail
        self.title = title or TITLES.get(status, "Error")
        self.errors = list(errors)
        self.headers = headers or {}


def problem_response(problem: ApiProblem) -> JSONResponse:
    body: dict[str, Any] = {
        "type": TYPE_BASE + problem.code,
        "title": problem.title,
        "status": problem.status,
        "detail": problem.detail,
        "code": problem.code,
        "errors": problem.errors,
    }
    return JSONResponse(
        body, status_code=problem.status, media_type=PROBLEM_MEDIA_TYPE, headers=problem.headers
    )


def _field_errors(exc: RequestValidationError) -> list[dict[str, str]]:
    out = []
    for err in exc.errors():
        loc = [str(p) for p in err["loc"] if p not in ("query", "path", "header", "body")]
        message = str(err["msg"]).removeprefix("Value error, ")
        out.append({"field": ".".join(loc) or "request", "message": message, "code": err["type"]})
    return out


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiProblem)
    async def _problem(_: Request, exc: ApiProblem) -> JSONResponse:
        return problem_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        is_body = any(e["loc"] and e["loc"][0] == "body" for e in exc.errors())
        status, code = (422, "validation_error") if is_body else (400, "invalid_parameters")
        return problem_response(
            ApiProblem(
                status, code, "One or more parameters are invalid", errors=_field_errors(exc)
            )
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
        detail = exc.detail if isinstance(exc.detail, str) else None
        return problem_response(
            ApiProblem(exc.status_code, code, detail, headers=dict(exc.headers or {}))
        )


def problem_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses=`` entries for problem+json statuses (content type fixed up later)."""
    return {c: {"model": Problem, "description": DESCRIPTIONS[c]} for c in codes}
