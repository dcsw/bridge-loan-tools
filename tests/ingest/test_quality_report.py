"""Phase 2 quality report: agreement and null-rate categories, extractor comparison, privacy."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.common.models import BronzeRecord, Manifest
from backend.ingest import quality_report as qr

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden"
EXTRACTORS = ("docling", "pymupdf", "unstructured")
NOW = datetime(2026, 11, 6, tzinfo=UTC)


def golden(name: str, extractors=EXTRACTORS) -> list[BronzeRecord]:
    return [
        BronzeRecord(**r)
        for e in extractors
        for r in json.loads((GOLDEN / e / f"{name}.json").read_text())
    ]


def manifests_for(records, town="mattapoisett") -> list[Manifest]:
    return [
        Manifest(
            doc_id=d,
            source_url=f"https://example.test/{d}",
            fetched_at=NOW,
            town=town,
            status="fetched",
            size_bytes=10,
        )
        for d in sorted({r.doc_id for r in records})
    ]


@pytest.mark.parametrize(
    ("keys", "category", "consensus"),
    [
        ({"a": None, "b": None}, "all_null", None),
        ({"a": "x", "b": None, "c": None}, "single", None),
        ({"a": "x", "b": "x", "c": "x"}, "unanimous", "x"),
        ({"a": "x", "b": "x", "c": "y"}, "majority", "x"),
        ({"a": "x", "b": "y", "c": None}, "conflict", None),
        ({"a": "x", "b": "x", "c": "y", "d": "y"}, "majority", None),
    ],
)
def test_categorize(keys, category, consensus):
    assert qr._categorize(keys) == (category, consensus)


def test_golden_mortgage_all_extractors_agree():
    recs = golden("mortgage")
    report = qr.build_report(recs, manifests_for(recs), now=NOW)
    town = report["towns"]["mattapoisett"]
    assert (town["documents_extracted"], town["instruments"]) == (1, 1)
    assert town["doc_types"] == {"mortgage": 1}
    assert town["extractors_per_instrument"] == {"3": 1}
    principal = town["fields"]["principal"]
    assert (principal["unanimous"], principal["null_rate"], principal["agreement_rate"]) == (
        1,
        0.0,
        1.0,
    )
    assert town["fields"]["refers_to_ref"]["null_rate"] == 1.0  # a mortgage never refers
    assert report["phase2_exit"] == {
        "documents_extracted": 1,
        "target": qr.PHASE2_DOC_TARGET,
        "target_met": False,
    }


def test_multi_instrument_document_counts_three_instruments():
    recs = golden("multi_instrument")
    town = qr.build_report(recs, manifests_for(recs))["towns"]["mattapoisett"]
    assert town["instruments"] == 3
    assert town["doc_types"] == {"deed": 1, "discharge": 1, "mortgage": 1}


def test_conflicting_extractor_is_visible_in_comparison():
    recs = golden("mortgage")
    for r in recs:
        if r.extractor == "docling":
            r.text = r.text.replace("$530,000.00", "$580,000.00")
    town = qr.build_report(recs, manifests_for(recs))["towns"]["mattapoisett"]
    principal = town["fields"]["principal"]
    assert (principal["majority"], principal["unanimous"]) == (1, 0)
    assert principal["agreement_rate"] == 1.0  # 2 of 3 agree: agreement is not accuracy
    per = principal["per_extractor"]
    assert per["docling"]["consensus_match_rate"] == 0.0
    assert per["pymupdf"]["consensus_match_rate"] == 1.0


def test_unparseable_document_is_counted_not_dropped():
    recs = golden("mortgage")
    other = "b" * 64
    recs += [
        r.model_copy(update={"doc_id": other, "text": "no recognizable heading"}) for r in recs
    ]
    town = qr.build_report(recs, manifests_for(recs))["towns"]["mattapoisett"]
    assert town["documents_extracted"] == 2
    assert town["documents_without_instruments"] == 1


def test_unknown_town_when_no_manifest():
    recs = golden("mortgage")
    assert "unknown" in qr.build_report(recs, [])["towns"]


def test_coverage_counts_gaps_and_unextracted_documents():
    recs = golden("mortgage")
    rows = manifests_for(recs)
    rows.append(rows[0].model_copy(update={"doc_id": "c" * 64}))  # fetched, never extracted
    rows.append(
        Manifest(
            doc_id="d" * 64,
            source_url="ref",
            fetched_at=NOW,
            town="madison",
            status="gap",
            gap_reason="paid",
            size_bytes=0,
        )
    )
    report = qr.build_report(recs, rows)
    cov = report["towns"]["mattapoisett"]["coverage"]
    assert (cov["fetched"], cov["fetched_not_extracted"], cov["gaps"]) == (2, 1, {})
    assert report["coverage_towns_without_extracted_docs"] == ["madison"]


def test_report_contains_no_document_content():
    recs = golden("mortgage")
    blob = json.dumps(qr.build_report(recs, manifests_for(recs))) + qr.render_markdown(
        qr.build_report(recs, manifests_for(recs))
    )
    for secret in ("Christina Cook", "Lee Product", "Obrien", "530,000", "MATT-11"):
        assert secret not in blob


def test_page_status_marks_needs_ocr():
    recs = golden("scanned_image")
    town = qr.build_report(recs, manifests_for(recs))["towns"]["mattapoisett"]
    flat = {s for per in town["page_status"].values() for s in per}
    assert flat & {"partial:needs_ocr", "ok", "partial", "failed"}


def test_cli_writes_json_and_markdown(tmp_path):
    out_dir = tmp_path / "extract-out"
    for e in EXTRACTORS:
        (out_dir / e).mkdir(parents=True)
        (out_dir / e / "mortgage.json").write_text((GOLDEN / e / "mortgage.json").read_text())
    recs = golden("mortgage")
    mdir = tmp_path / "manifests"
    mdir.mkdir()
    for m in manifests_for(recs):
        (mdir / f"{m.doc_id}.json").write_text(m.model_dump_json())
    out = tmp_path / "reports" / "q"
    rc = qr.main(["--extract-out", str(out_dir), "--manifests", str(mdir), "--out", str(out)])
    assert rc == 0
    data = json.loads(out.with_suffix(".json").read_text())
    assert data["towns"]["mattapoisett"]["instruments"] == 1
    assert out.with_suffix(".md").read_text().startswith("# Quality report")


def test_cli_with_no_inputs_reports_zero_documents(tmp_path, capsys):
    rc = qr.main(["--extract-out", str(tmp_path / "x"), "--manifests", str(tmp_path / "y")])
    assert rc == 0 and "0 / 100" in capsys.readouterr().out


def test_extractor_that_missed_an_instrument_is_counted():
    recs = [
        r for r in golden("multi_instrument") if not (r.extractor == "docling" and r.page_no > 1)
    ]
    town = qr.build_report(recs, manifests_for(recs))["towns"]["mattapoisett"]
    per = town["fields"]["doc_type"]["per_extractor"]["docling"]
    assert per["missed_instruments"] >= 1 and per["null_rate"] == 0.0
    assert per["null_or_missed_rate"] > per["null_rate"]


def test_null_is_not_a_consensus_mismatch():
    recs = golden("mortgage")
    for r in recs:
        if r.extractor == "docling":
            r.text = r.text.replace("Principal: $530,000.00", "")
    fields = qr.build_report(recs, manifests_for(recs))["towns"]["mattapoisett"]["fields"]
    per = fields["principal"]["per_extractor"]["docling"]
    assert per["null_rate"] == 1.0 and per["consensus_match_rate"] is None


def test_unknown_town_documents_do_not_count_to_the_target():
    recs = golden("mortgage")
    assert qr.build_report(recs, [])["phase2_exit"]["documents_extracted"] == 0
