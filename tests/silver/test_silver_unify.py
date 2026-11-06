"""WU6 DoD 1: unification (agreement rule, precedence, disagreement, no imputation, alignment)."""

from __future__ import annotations

import pytest
from silver_helpers import EXTRACTORS, SHA, golden_records, meta_for, record, truth

from backend.ingest import unify as u

MORTGAGE_TEXT = (
    "MORTGAGE\nGrantor: Diane Webb\nGrantee: Ward Alone Lending LLC\n"
    "Property: 5114 Ward Ville Suite 727, Mattapoisett, MA\nParcel: MATT-50-5\n"
    "Recorded: March 15, 2023  Book 200 Page 2\nPrincipal: $1,060,000.00\nTerm: 12 months\n"
    "SYNTHETIC TEST DOCUMENT - NOT A REAL RECORD\n"
)
META = {SHA: u.DocMeta("mattapoisett", None)}


def _one(text, extractors=EXTRACTORS):
    return u.unify_records([record(e, text) for e in extractors], META)


# -- parsing ------------------------------------------------------------------------------


def test_parse_page_newline_and_flattened_forms_agree():
    flat = " ".join(MORTGAGE_TEXT.split("\n"))
    a, b = u.parse_page(MORTGAGE_TEXT), u.parse_page(flat)
    assert a == b
    assert a["doc_type"] == "mortgage"
    assert a["grantor"] == "Diane Webb"
    assert a["grantee"] == "Ward Alone Lending LLC"
    assert a["address_raw"] == "5114 Ward Ville Suite 727, Mattapoisett, MA"
    assert a["parcel_key"] == "MATT-50-5"
    assert (a["recorded_date"], a["recorded_book"], a["recorded_page"]) == (
        "March 15, 2023",
        "200",
        "2",
    )
    assert a["principal"] == "$1,060,000.00" and a["term_months"] == "12 months"


@pytest.mark.parametrize(
    ("heading", "doc_type"),
    [
        ("QUITCLAIM DEED", "deed"),
        ("WARRANTY DEED", "deed"),
        ("DISCHARGE OF MORTGAGE", "discharge"),
        ("MORTGAGE", "mortgage"),
        ("MORTGAGE DEED", "mortgage"),
    ],
)
def test_parse_page_doc_type_headings(heading, doc_type):
    assert u.parse_page(f"{heading}\nGrantor: A B\n")["doc_type"] == doc_type


def test_parse_page_discharge_reference_and_maturity():
    parsed = u.parse_page(
        "DISCHARGE OF MORTGAGE\nGrantor: X Bank\nMaturity: 3/15/2024\n"
        "Discharges mortgage recorded at 200/2\n"
    )
    assert parsed["refers_to_ref"] == "200/2"
    assert parsed["maturity_date"] == "3/15/2024"


def test_parse_page_without_heading_has_no_doc_type_and_blank_is_none():
    assert u.parse_page("Principal: $5.00\n")["doc_type"] is None
    assert u.parse_page("   ") is None
    assert u.parse_page("nothing recognizable here") is None


# -- field normalization ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "a", "b"),
    [
        ("principal", "$1,060,000.00", "1060000"),
        ("recorded_date", "March 15, 2023", "2023-03-15"),
        ("grantee", "Ward Alone Lending L.L.C.", "WARD ALONE LENDING LLC"),
        ("address_raw", "5114 Ward Ville Street", "5114 WARD VILLE ST"),
        ("term_months", "12 months", "12"),
        ("parcel_key", " matt-50-5 ", "MATT-50-5"),
        ("recorded_book", " 200 ", "200"),
        ("refers_to_ref", "200 / 2", "200/2"),
    ],
)
def test_canonical_equates_equivalent_spellings(field, a, b):
    assert u.canonical(field, a) is not None
    assert u.canonical(field, a) == u.canonical(field, b)


@pytest.mark.parametrize(
    ("field", "raw"),
    [
        ("principal", "n/a"),
        ("recorded_date", "March 2021"),
        ("term_months", "twelve"),
        ("grantor", "   "),
        ("parcel_key", None),
        ("term_months", ""),
        ("doc_type", "lease"),
    ],
)
def test_canonical_unparseable_is_null_never_imputed(field, raw):
    assert u.canonical(field, raw) is None


# -- unify_field --------------------------------------------------------------------------


def test_three_agree():
    fv = u.unify_field("principal", {"docling": "$5.00", "pymupdf": "5.00", "unstructured": "5"})
    assert (fv.value, fv.confidence, fv.disagreement) == ("5.00", 0.9, False)
    assert fv.source_extractor == "pymupdf"  # highest precedence among the agreeing


def test_two_of_three_agree_wins_with_confidence_09():
    fv = u.unify_field(
        "grantee", {"docling": "ACME", "pymupdf": "Acme", "unstructured": "Zeta Corp"}
    )
    assert fv.confidence == 0.9 and fv.disagreement is False
    assert fv.value == "ACME"
    assert fv.source_extractor == "docling"  # names: unstructured > docling > pymupdf


def test_two_of_three_agreeing_on_a_wrong_value_still_wins():
    """Known limitation by design: agreement is not truth. The odd one out was right."""
    fv = u.unify_field(
        "principal",
        {"docling": "$100,000.00", "pymupdf": "$100,000.00", "unstructured": "$10,000.00"},
    )
    assert fv.value == "100000.00" and fv.confidence == 0.9


def test_all_different_uses_precedence_with_disagreement_flag():
    cand = {"docling": "$1.00", "pymupdf": "$2.00", "unstructured": "$3.00"}
    fv = u.unify_field("principal", cand)
    assert (fv.value, fv.source_extractor, fv.confidence, fv.disagreement) == (
        "2.00",
        "pymupdf",
        0.5,
        True,
    )
    fv = u.unify_field(
        "grantor", {"docling": "A One", "pymupdf": "B Two", "unstructured": "C Three"}
    )
    assert (fv.value, fv.source_extractor) == ("C Three", "unstructured")


def test_two_extractors_disagree():
    fv = u.unify_field("recorded_date", {"docling": "2023-03-15", "pymupdf": "2023-03-16"})
    assert fv.source_extractor == "pymupdf" and fv.disagreement and fv.confidence == 0.5


def test_single_source_has_no_disagreement_but_low_confidence():
    fv = u.unify_field("parcel_key", {"docling": None, "pymupdf": None, "unstructured": "MATT-1-1"})
    assert (fv.value, fv.source_extractor, fv.confidence, fv.disagreement) == (
        "MATT-1-1",
        "unstructured",
        0.5,
        False,
    )


def test_all_null_stays_null():
    fv = u.unify_field("principal", {"docling": None, "pymupdf": "garbage", "unstructured": ""})
    assert fv.value is None and fv.source_extractor is None and fv.confidence == 0.0
    assert fv.disagreement is False
    assert u.unify_field("principal", {}).value is None


def test_typed_values_are_json_safe():
    assert u.unify_field("term_months", {"docling": "18 months"}).value == 18
    assert u.unify_field("recorded_date", {"docling": "Feb 1, 2023"}).value == "2023-02-01"


def test_two_agreeing_groups_resolve_to_the_group_with_highest_precedence():
    cand = {"docling": "$1.00", "pymupdf": "$2.00", "unstructured": "$2.00", "extra": "$1.00"}
    fv = u.unify_field("principal", cand)
    assert fv.value == "2.00" and fv.source_extractor == "pymupdf" and fv.confidence == 0.9


def test_precedence_table_covers_every_field_and_extractor():
    for field in u.FIELD_NAMES:
        assert sorted(u.precedence(field)) == sorted(EXTRACTORS)
    assert u.precedence("grantee")[0] == "unstructured"
    assert u.precedence("principal")[0] == "pymupdf"
    assert u.precedence("no_such_field") == u.DEFAULT_PRECEDENCE


def test_unknown_extractor_ranks_last():
    fv = u.unify_field("principal", {"zzz": "$1.00", "docling": "$2.00"})
    assert fv.source_extractor == "docling"


# -- alignment ----------------------------------------------------------------------------


def _seg(extractor, start, end=None, ref=None, doc_type="mortgage"):
    raw = {"doc_type": doc_type}
    if ref:
        raw["recorded_book"], raw["recorded_page"] = ref.split("/")
    return u.Segment(extractor, start, end or start, raw, "")


def test_align_by_recorded_ref():
    groups = u.align_segments(
        [
            _seg("docling", 1, ref="1/1"),
            _seg("pymupdf", 1, ref="1/1"),
            _seg("docling", 2, ref="1/2"),
        ]
    )
    assert [sorted(s.extractor for s in g) for g in groups] == [["docling", "pymupdf"], ["docling"]]


def test_align_by_page_range_when_ref_missing_or_garbled():
    groups = u.align_segments(
        [
            _seg("docling", 2, 3),  # no ref
            _seg("pymupdf", 2, 3, ref="1/2"),
            _seg("unstructured", 2, 3, ref="1/9"),  # garbled ref, same pages
            _seg("docling", 1),
        ]
    )
    assert len(groups) == 2
    assert [s.page_start for s in groups[0]] == [1]  # ordered by first page
    assert sorted(s.extractor for s in groups[1]) == ["docling", "pymupdf", "unstructured"]


def test_same_extractor_twice_never_merges_and_distinct_pages_stay_apart():
    groups = u.align_segments([_seg("docling", 1, ref="1/1"), _seg("docling", 1, ref="1/1")])
    assert len(groups) == 2
    groups = u.align_segments([_seg("docling", 1, 1), _seg("pymupdf", 2, 2)])
    assert len(groups) == 2


def test_segment_pages_spans_continuation_pages():
    pages = [
        record("docling", "MORTGAGE\nGrantor: A B\nParcel: P-1\n", page_no=1),
        record("docling", "continued\nPrincipal: $9.00\nParcel: IGNORED\n", page_no=2),
        record("docling", "DEED\nGrantor: C D\n", page_no=3),
    ]
    segs = u.segment_pages("docling", pages)
    assert [(s.page_start, s.page_end) for s in segs] == [(1, 2), (3, 3)]
    assert segs[0].raw["principal"] == "$9.00" and segs[0].raw["parcel_key"] == "P-1"


def test_segment_pages_skips_failed_empty_and_headerless_leading_pages():
    pages = [
        record("docling", "", page_no=1, status="failed", error="boom"),
        record("docling", "random cover sheet", page_no=2),
        record("docling", "Principal: $1.00", page_no=3),
        record("docling", "MORTGAGE\nGrantor: A B\n", page_no=4),
    ]
    segs = u.segment_pages("docling", pages)
    assert [(s.page_start, s.page_end) for s in segs] == [(4, 4)]


# -- documents (golden fixtures vs truth) -------------------------------------------------


def _unify(name, extractors=EXTRACTORS):
    recs = golden_records(name, extractors)
    return u.unify_records(recs, meta_for(recs))


@pytest.mark.parametrize("name", ["mortgage", "deed", "discharge", "multi_instrument"])
def test_golden_documents_match_truth(name):
    res = _unify(name)
    want = truth(name)["instruments"]
    assert not res.skipped
    assert len(res.instruments) == len(want)
    for got, exp in zip(res.instruments, want, strict=True):
        ins = got.instrument
        assert ins.instrument_id == f"{ins.doc_id}-{exp['ordinal']}"
        assert ins.doc_type == exp["doc_type"]
        assert ins.parcel_key == exp["parcel_key"]
        assert ins.grantor == exp["grantor"] and ins.grantee == exp["grantee"]
        assert ins.address_raw == exp["address_raw"]
        assert ins.recorded_date.isoformat() == exp["recorded_date"]
        assert (ins.recorded_book, ins.recorded_page) == (
            exp["recorded_book"],
            exp["recorded_page"],
        )
        assert (None if ins.principal is None else str(ins.principal)) == exp.get("principal")
        assert ins.term_months == exp.get("term_months")
        assert ins.refers_to_ref == exp.get("refers_to_ref")
        assert ins.town == "mattapoisett" and ins.source_url.endswith(f"{ins.doc_id}.pdf")
        assert all(f.disagreement is False for f in ins.fields.values())
        assert ins.fields["grantor"].confidence == 0.9


def test_multi_instrument_alignment_order_and_lender_type():
    ins = [x.instrument for x in _unify("multi_instrument").instruments]
    assert [i.doc_type for i in ins] == ["deed", "mortgage", "discharge"]
    assert [i.ordinal for i in ins] == [0, 1, 2]
    assert ins[1].lender_type == "private_llc" and ins[0].lender_type is None
    assert ins[1].address_norm == "5114 WARD VILLE SUITE 727 MATTAPOISETT MA"


def test_scanned_image_garbled_docling_and_empty_pymupdf():
    (got,) = _unify("scanned_image").instruments
    ins = got.instrument
    assert ins.grantee == "Wong Behavior Credit Union"  # unstructured wins the name tie
    f = ins.fields["grantee"]
    assert f.disagreement and f.confidence == 0.5 and f.source_extractor == "unstructured"
    addr = ins.fields["address_raw"]  # docling lost the Property line, pymupdf had no text
    assert addr.source_extractor == "unstructured" and not addr.disagreement
    assert addr.confidence == 0.5
    assert ins.parcel_key == "MATT-14-4"


def test_extractor_failure_is_isolated():
    full = _unify("multi_instrument").instruments
    for dropped in EXTRACTORS:
        keep = tuple(e for e in EXTRACTORS if e != dropped)
        res = _unify("multi_instrument", keep)
        assert [x.instrument.instrument_id for x in res.instruments] == [
            x.instrument.instrument_id for x in full
        ]
        assert [x.instrument.principal for x in res.instruments] == [
            x.instrument.principal for x in full
        ]
    only = _unify("multi_instrument", ("docling",))
    assert len(only.instruments) == 3
    assert only.instruments[1].instrument.fields["principal"].confidence == 0.5


def test_failed_documents_yield_nothing():
    recs = golden_records("corrupt") + golden_records("oversize")
    res = u.unify_records(recs, meta_for(recs))
    assert res.instruments == [] and res.skipped == []


def test_missing_manifest_meta_is_reported_not_dropped_silently():
    recs = golden_records("mortgage")
    res = u.unify_records(recs, {})
    assert res.instruments == []
    assert [(s.doc_id, s.reason) for s in res.skipped] == [
        (recs[0].doc_id, "missing_manifest_meta")
    ]


def test_ingested_at_is_deterministic_and_text_kept_for_keyword_signal():
    recs = golden_records("mortgage")
    (got,) = u.unify_records(recs, meta_for(recs)).instruments
    assert got.ingested_at == max(r.extracted_at for r in recs)
    assert "MORTGAGE" in got.text


def test_record_order_does_not_change_the_result():
    recs = golden_records("multi_instrument")
    a = u.unify_records(recs, meta_for(recs))
    b = u.unify_records(recs[::-1], meta_for(recs))
    assert [x.instrument.model_dump() for x in a.instruments] == [
        x.instrument.model_dump() for x in b.instruments
    ]


def test_maturity_date_taken_only_when_present():
    text = MORTGAGE_TEXT.replace("Term:", "Maturity: 3/15/2024\nTerm:")
    (got,) = _one(text).instruments
    assert got.instrument.maturity_date.isoformat() == "2024-03-15"
    assert got.instrument.source_url is None
    plain = _one(MORTGAGE_TEXT, ("docling",))
    assert plain.instruments[0].instrument.maturity_date is None


def test_missing_grantee_gives_unknown_lender_type():
    (got,) = _one(MORTGAGE_TEXT.replace("Ward Alone Lending LLC", "")).instruments
    assert got.instrument.grantee is None and got.instrument.lender_type == "unknown"


def test_unknown_lender_name_falls_back_to_individual_as_in_wu2():
    """classify_lender_type returns 'individual' for any unrecognized name (WU2 note, unchanged)."""
    (got,) = _one(MORTGAGE_TEXT.replace("Ward Alone Lending LLC", "Zorb Quux")).instruments
    assert got.instrument.lender_type == "individual"


def test_ambiguous_numeric_dates_are_month_first():
    (got,) = _one(MORTGAGE_TEXT.replace("March 15, 2023", "03/04/2023")).instruments
    assert got.instrument.recorded_date.isoformat() == "2023-03-04"


def test_town_falls_back_to_address_when_meta_town_blank_is_not_invented():
    res = u.unify_records([record("docling", MORTGAGE_TEXT)], {SHA: u.DocMeta("", None)})
    assert res.instruments == []
    assert res.skipped[0].reason == "missing_manifest_meta"


def test_instrument_failing_model_validation_is_reported_not_raised():
    text = MORTGAGE_TEXT.replace("Term:", "Maturity: 1/1/2020\nTerm:")  # before recorded date
    res = _one(text)
    assert res.instruments == []
    assert [s.reason for s in res.skipped] == ["invalid_instrument"]
    assert res.skipped[0].doc_id == f"{SHA}-0"
