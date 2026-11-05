"""Schema tests (list vs detail), score bands, and openapi.json drift."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.api.schemas import (
    FeatureCollection,
    LoanDetail,
    LoanFeatureProperties,
    score_band,
)

ROOT = Path(__file__).resolve().parents[2]

REQUIRED = {"is_bridge_loan", "classifier_version", "bridge_score", "score_band"}


@pytest.mark.parametrize(
    "score,band",
    [(0, "low"), (39, "low"), (40, "mid"), (69, "mid"), (70, "high"), (100, "high")],
)
def test_score_band_boundaries(score, band):
    assert score_band(score) == band


@pytest.mark.parametrize("bad", [-1, 101])
def test_score_band_out_of_range(bad):
    with pytest.raises(ValueError):
        score_band(bad)


def test_list_and_detail_schemas_include_classifier_fields():
    assert REQUIRED <= set(LoanFeatureProperties.model_fields)
    assert REQUIRED <= set(LoanDetail.model_fields)


def test_list_schema_has_no_borrower_anywhere():
    schema = json.dumps(FeatureCollection.model_json_schema()).lower()
    assert "borrower" not in schema and "grantor" not in schema
    assert "borrower" not in LoanFeatureProperties.model_fields


def test_detail_schema_has_borrower_and_unevaluable():
    assert {"borrower", "signals", "signals_unevaluable", "fields", "source"} <= set(
        LoanDetail.model_fields
    )


def test_openapi_json_matches_app():
    from backend.api.openapi import build_openapi

    committed = json.loads((ROOT / "openapi.json").read_text())
    assert committed == build_openapi()


def test_openapi_documents_problem_responses_and_no_default_422():
    from backend.api.openapi import build_openapi

    spec = build_openapi()
    loans = spec["paths"]["/loans"]["get"]["responses"]
    for status in ("200", "400", "401", "410", "413", "503"):
        assert status in loans, status
    assert "422" not in loans
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    assert loans["400"]["content"]["application/problem+json"]["schema"]["$ref"].endswith(
        "/Problem"
    )
    assert "Retry-After" in loans["503"]["headers"]
    detail = spec["paths"]["/loans/{loan_id}"]["get"]["responses"]
    assert {"200", "400", "401", "404", "503"} <= set(detail)
    assert set(spec["paths"]) == {
        "/loans",
        "/loans/{loan_id}",
        "/loans/{loan_id}/source",
        "/coverage",
        "/health/live",
        "/health/ready",
    }


def test_openapi_security_scheme_declared():
    from backend.api.openapi import build_openapi

    spec = build_openapi()
    assert "GatewayAuth" in spec["components"]["securitySchemes"]
