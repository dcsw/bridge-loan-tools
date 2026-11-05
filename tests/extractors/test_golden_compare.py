"""Tolerance-based golden comparison helpers (WU5a DoD 1)."""

from __future__ import annotations

import copy

from backend.ingest.extractors.golden import (
    TEXT_SIMILARITY_THRESHOLD,
    compare_to_golden,
    missing_key_fields,
    normalize_text,
    text_similarity,
)

REC = {
    "doc_id": "a" * 64,
    "extractor": "x",
    "extractor_version": "x==1",
    "page_no": 1,
    "text": "MORTGAGE\nGrantor: Jane Roe\nPrincipal: $1,000.00",
    "tables_json": "[]",
    "extracted_at": "2026-01-01T00:00:00Z",
    "status": "ok",
    "error": None,
}


def test_threshold_documented_value():
    assert TEXT_SIMILARITY_THRESHOLD == 0.90


def test_normalize_collapses_whitespace_and_case():
    assert normalize_text("A  b\n\tC ") == "a b c"


def test_similarity_bounds():
    assert text_similarity("same", "SAME ") == 1.0
    assert text_similarity("abc", "xyz") < 0.2


def test_identical_matches():
    assert compare_to_golden([REC], [REC]) == []


def test_extracted_at_and_whitespace_noise_tolerated():
    actual = copy.deepcopy(REC)
    actual["extracted_at"] = "2030-05-05T00:00:00Z"
    actual["text"] = REC["text"].replace("\n", "\n\n")
    assert compare_to_golden([actual], [REC]) == []


def test_small_text_drift_within_threshold():
    actual = copy.deepcopy(REC)
    actual["text"] = REC["text"] + " ."
    assert compare_to_golden([actual], [REC]) == []


def test_large_text_drift_flagged():
    actual = copy.deepcopy(REC)
    actual["text"] = "completely different content here"
    problems = compare_to_golden([actual], [REC])
    assert any("similarity" in p for p in problems)


def test_key_field_mismatch_flagged():
    for field, value in [
        ("status", "failed"),
        ("extractor_version", "x==2"),
        ("page_no", 2),
        ("doc_id", "b" * 64),
        ("extractor", "y"),
    ]:
        actual = copy.deepcopy(REC)
        actual[field] = value
        assert any(field in p for p in compare_to_golden([actual], [REC])), field


def test_record_count_mismatch_flagged():
    assert any("record count" in p for p in compare_to_golden([REC, REC], [REC]))


def test_error_code_prefix_must_match():
    g = {**REC, "status": "partial", "error": "needs_ocr: no text layer"}
    a = {**g, "error": "needs_ocr: different wording"}
    assert compare_to_golden([a], [g]) == []
    b = {**g, "error": "timeout: killed"}
    assert any("error" in p for p in compare_to_golden([b], [g]))


def test_missing_key_fields_checks_truth_values_in_text():
    truth = {
        "instruments": [
            {
                "grantor": "Jane Roe",
                "grantee": "Acme Capital LLC",
                "address_raw": "1 Main St, Town, MA",
                "parcel_key": "T-1-1",
                "principal": "1000.00",
            }
        ]
    }
    text = (
        "Grantor: Jane  Roe Grantee: Acme Capital LLC Property: 1 Main St, Town, MA T-1-1 $1,000.00"
    )
    assert missing_key_fields(text, truth) == []
    assert missing_key_fields("nothing here", truth) == [
        "Jane Roe",
        "Acme Capital LLC",
        "1 Main St, Town, MA",
        "T-1-1",
        "$1,000.00",
    ]


def test_canonicalize_pins_timestamp_and_keeps_content():
    from backend.ingest.extractors.golden import GOLDEN_TIMESTAMP, canonicalize

    out = canonicalize([{**REC, "extracted_at": "2031-01-01T00:00:00Z"}])
    assert out[0]["extracted_at"] == GOLDEN_TIMESTAMP
    assert out[0]["text"] == REC["text"]


def test_unexpected_failures_flags_failed_non_reject_fixtures_only():
    from backend.ingest.extractors.golden import unexpected_failures

    failed = [{**REC, "status": "failed", "error": "extract_error: boom"}]
    by_fixture = {"mortgage": failed, "corrupt": failed, "deed": [REC]}
    assert unexpected_failures(by_fixture) == ["mortgage: extract_error: boom"]
