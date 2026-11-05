"""FastAPI read API over the published snapshot (design 3.3-3.5)."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Callable, Iterator
from typing import Annotated, TextIO

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.responses import StreamingResponse

from backend.api import queries
from backend.api.audit import AuditLog
from backend.api.auth import Principal, gateway_auth
from backend.api.config import Settings
from backend.api.deps import FiltersDep, StateDep, get_fetcher, unavailable
from backend.api.documents import (
    DocumentStoreError,
    ObjectFetcher,
    ObjectNotFound,
    ObjectStream,
    S3ObjectFetcher,
)
from backend.api.errors import (
    ApiProblem,
    install_error_handlers,
    problem_response,
    problem_responses,
)
from backend.api.logging_setup import API_LOGGER, configure_logging
from backend.api.schemas import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Coverage,
    FeatureCollection,
    Health,
    LoanDetail,
    LoanIdPath,
)
from backend.api.snapshot import SnapshotStore, SnapshotUnavailable

DOC_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PDF_MAGIC = b"%PDF-"
DEFAULT_KEY_TEMPLATE = "{doc_id}.pdf"
log = logging.getLogger(API_LOGGER)


class GeoJSONResponse(Response):
    media_type = "application/geo+json"


def _peek(stream: ObjectStream, n: int) -> bytes:
    """Read until ``n`` bytes (or EOF) so the magic-byte check never sees a short first chunk."""
    buf = b""
    while len(buf) < n:
        block = next(stream.chunks, b"")
        if not block:
            break
        buf += block
    return buf


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(gateway_auth)])

    @router.get(
        "/loans",
        response_model=FeatureCollection,
        response_class=GeoJSONResponse,
        summary="List bridge-loan candidates as GeoJSON (no borrower names)",
        responses=problem_responses(400, 401, 410, 413, 503),
    )
    def list_loans(
        request: Request,
        state: StateDep,
        filters: FiltersDep,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
    ) -> Response:
        collection = queries.list_loans(state, filters, limit, cursor)
        body = collection.model_dump_json().encode()
        if len(body) > request.app.state.settings.response_cap_bytes:
            raise ApiProblem(413, "response_too_large", "narrow the filters or lower limit")
        return GeoJSONResponse(content=body)

    @router.get(
        "/loans/{loan_id}",
        response_model=LoanDetail,
        summary="Loan detail (borrower)",
        responses=problem_responses(400, 401, 404, 503),
    )
    def get_loan(
        loan_id: LoanIdPath,
        request: Request,
        response: Response,
        state: StateDep,
        principal: Annotated[Principal, Depends(gateway_auth)],
    ) -> LoanDetail:
        detail = queries.get_detail(state, loan_id)
        if detail is None:
            raise ApiProblem(404, "loan_not_found", "no loan with that id")
        _audit(request, principal, loan_id, "detail")
        response.headers["Cache-Control"] = "no-store"
        return detail

    @router.get(
        "/loans/{loan_id}/source",
        summary="Stream the source PDF",
        response_class=StreamingResponse,
        responses={
            200: {
                "description": "The source PDF",
                "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
            },
            **problem_responses(400, 401, 404, 502, 503),
        },
    )
    def get_source(
        loan_id: LoanIdPath,
        request: Request,
        state: StateDep,
        principal: Annotated[Principal, Depends(gateway_auth)],
        fetcher: Annotated[ObjectFetcher | None, Depends(get_fetcher)],
    ) -> StreamingResponse:
        detail = queries.get_detail(state, loan_id)
        if detail is None or not DOC_ID_PATTERN.fullmatch(detail.source.doc_id):
            raise ApiProblem(404, "loan_not_found", "no loan with that id")
        if fetcher is None:
            raise ApiProblem(503, "documents_unavailable", "source documents are not configured")
        doc_id = detail.source.doc_id
        raw = request.app.state.settings.raw_store
        template = raw.key_template if raw else DEFAULT_KEY_TEMPLATE
        try:
            stream = fetcher.open(template.format(doc_id=doc_id))
        except ObjectNotFound as exc:
            raise ApiProblem(404, "document_not_found", "source document not found") from exc
        except DocumentStoreError as exc:
            raise ApiProblem(503, "documents_unavailable", "document store unavailable") from exc
        try:
            head = _peek(stream, len(PDF_MAGIC))
            if not head.startswith(PDF_MAGIC):
                raise ApiProblem(502, "invalid_document", "stored object is not a PDF")
            _audit(request, principal, loan_id, "source")
        except BaseException:
            stream.close()
            raise

        def body() -> Iterator[bytes]:
            try:
                yield head
                yield from stream.chunks
            finally:
                stream.close()

        headers = {
            "Content-Disposition": f'attachment; filename="{doc_id}.pdf"',
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        }
        if stream.content_length is not None:
            headers["Content-Length"] = str(stream.content_length)
        return StreamingResponse(body(), media_type="application/pdf", headers=headers)

    @router.get(
        "/coverage",
        response_model=Coverage,
        summary="Per-town ingestion coverage",
        responses=problem_responses(401, 503),
    )
    def coverage(state: StateDep) -> Coverage:
        return queries.get_coverage(state)

    return router


def _audit(request: Request, principal: Principal, loan_id: str, action: str) -> None:
    """Fail closed: PII is only released after the access has been recorded."""
    try:
        request.app.state.audit.record(principal.key_id, loan_id, action)
    except Exception as exc:
        log.exception("audit write failed")
        raise ApiProblem(503, "audit_unavailable", "access could not be recorded") from exc


def build_health_router() -> APIRouter:
    router = APIRouter(prefix="/health", tags=["health"])

    @router.get(
        "/live", response_model=Health, response_model_exclude_none=True, summary="Process liveness"
    )
    def live() -> Health:
        return Health(status="ok")

    @router.get(
        "/ready",
        response_model=Health,
        summary="Snapshot readable",
        responses=problem_responses(503),
    )
    def ready(request: Request) -> Health:
        try:
            state = request.app.state.store.current()
        except SnapshotUnavailable as exc:
            raise unavailable(request) from exc
        return Health(status="ok", snapshot_id=state.snapshot_id)

    return router


def create_app(
    settings: Settings,
    *,
    store: SnapshotStore | None = None,
    fetcher: ObjectFetcher | None = None,
    audit: AuditLog | None = None,
    log_stream: TextIO | None = None,
) -> FastAPI:
    configure_logging(log_stream)
    app = FastAPI(
        title="Bridge Loan API",
        version="0.1.0",
        description="Read API over the published bridge-loan snapshot. Gateway-only.",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.store = store or SnapshotStore(settings)
    if fetcher is None and settings.raw_store is not None:
        fetcher = S3ObjectFetcher(settings.raw_store)
    app.state.fetcher = fetcher
    app.state.audit = audit or AuditLog(settings.audit_log_path, settings.audit_retention_days)
    app.state.audit.purge_expired()
    install_error_handlers(app)

    @app.middleware("http")
    async def _never_reraise(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # An exception escaping to uvicorn would be logged there with a raw traceback; log it
        # ourselves through the redacting logger and answer with a generic problem instead.
        try:
            return await call_next(request)
        except Exception:
            log.exception("unhandled error on %s %s", request.method, request.url.path)
            return problem_response(ApiProblem(500, "internal_error", "unexpected server error"))

    app.include_router(build_router())
    app.include_router(build_health_router())
    return app


def app_from_env() -> FastAPI:
    """uvicorn entry point: ``uvicorn backend.api.main:app_from_env --factory``."""
    return create_app(Settings.from_env(os.environ))
