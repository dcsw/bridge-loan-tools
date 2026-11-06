"""WU6 DoD 2, 3, 5 at pipeline level: MERGE idempotency, per-parcel recompute, isolation."""

from __future__ import annotations

import json

from silver_helpers import EXTRACTORS, golden_records, meta_for, record

from backend.common.models import Manifest
from backend.ingest import geocode as g
from backend.ingest import silver_job as sj
from backend.ingest import views
from backend.ingest.unify import DocMeta


class CountingGeocoder:
    def __init__(self):
        self.calls = []

    def geocode(self, address):
        self.calls.append(address)
        return g.GeocodeResult("matched", 41.65, -70.81)


ALL = ["mortgage", "deed", "discharge", "multi_instrument", "scanned_image"]


def all_records(names=ALL, extractors=EXTRACTORS):
    recs = []
    for n in names:
        recs += golden_records(n, extractors)
    return recs


def snapshot(store):
    return (
        sorted(store.read_instruments(), key=lambda r: r["instrument_id"]),
        sorted(store.read_loans(), key=lambda r: r["loan_id"]),
    )


def test_instruments_merge_is_idempotent_and_loans_stable():
    recs = all_records()
    meta = meta_for(recs)
    store = sj.InMemoryStore()
    sj.run_silver(recs, meta, store, CountingGeocoder())
    first = snapshot(store)
    report = sj.run_silver(recs, meta, store, CountingGeocoder())
    assert snapshot(store) == first
    assert len(first[0]) == 7 and len(first[1]) == 3  # 1+1+1+3+1 instruments; 3 mortgages
    assert report.skipped == []


def test_every_loan_is_a_placeholder_bridge_loan_with_classifier_version():
    recs = all_records()
    store = sj.InMemoryStore()
    sj.run_silver(recs, meta_for(recs), store, CountingGeocoder())
    assert store.read_loans()
    for row in store.read_loans():
        assert row["is_bridge_loan"] is True and row["bridge_score"] == 100
        assert row["classifier_version"] == "placeholder-all-true"


def test_multi_instrument_document_yields_one_loan_with_parcel_signals():
    recs = golden_records("multi_instrument")
    store = sj.InMemoryStore()
    sj.run_silver(recs, meta_for(recs), store, CountingGeocoder())
    (loan,) = [views.row_to_loan(r) for r in store.read_loans()]
    assert loan.loan_id.endswith("-1")
    assert {"purchase_then_mortgage_90d", "short_term", "private_lender"} <= set(loan.signals)
    assert "no_discharge" not in loan.signals  # the document's own discharge clears it


MORTGAGE = (
    "MORTGAGE\nGrantor: Pat Doe\nGrantee: Acme Capital LLC\nProperty: 1 Main St, Mattapoisett, MA\n"
    "Parcel: MATT-1-1\nRecorded: March 15, 2023  Book 1 Page 1\nPrincipal: $90,000.00\n"
    "Term: 12 months\n"
)
OTHER_DISCHARGE = (
    "DISCHARGE OF MORTGAGE\nGrantor: Some Bank NA\nGrantee: Lee Roe\n"
    "Property: 9 Elm St, Mattapoisett, MA\nParcel: MATT-9-9\n"
    "Recorded: June 1, 2024  Book 5 Page 5\n"
    "Discharges mortgage recorded at 4/4\n"
)
LATE_DISCHARGE = (
    "DISCHARGE OF MORTGAGE\nGrantor: Acme Capital LLC\nGrantee: Pat Doe\n"
    "Property: 1 Main St, Mattapoisett, MA\nParcel: MATT-1-1\n"
    "Recorded: June 1, 2024  Book 7 Page 7\n"
    "Discharges mortgage recorded at 1/1\n"
)
OTHER_MORTGAGE = MORTGAGE.replace("MATT-1-1", "MATT-2-2").replace("Book 1 Page 1", "Book 2 Page 2")


def doc_records(doc_id, text):
    return [record(e, text, doc_id=doc_id) for e in EXTRACTORS]


def loans_by_id(store):
    return {r["loan_id"]: views.row_to_loan(r) for r in store.read_loans()}


def test_late_discharge_flips_no_discharge_and_only_recomputes_its_parcel():
    m, o, om, late = "1" * 64, "2" * 64, "4" * 64, "3" * 64
    meta = {d: DocMeta("mattapoisett", None) for d in (m, o, om, late)}
    store = sj.InMemoryStore()
    run1 = (
        doc_records(m, MORTGAGE) + doc_records(o, OTHER_DISCHARGE) + doc_records(om, OTHER_MORTGAGE)
    )
    sj.run_silver(run1, meta, store, CountingGeocoder())
    before = loans_by_id(store)
    assert "no_discharge" in before[f"{m}-0"].signals
    assert "no_discharge" in before[f"{om}-0"].signals

    geo = CountingGeocoder()
    report = sj.run_silver(doc_records(late, LATE_DISCHARGE), meta, store, geo)
    after = loans_by_id(store)
    assert "no_discharge" not in after[f"{m}-0"].signals
    assert "no_discharge" in after[f"{om}-0"].signals  # untouched parcel unchanged
    assert report.loans_recomputed == 1 and len(geo.calls) == 1


def test_first_discharge_in_a_town_recomputes_the_whole_town():
    m, late = "1" * 64, "3" * 64
    meta = {d: DocMeta("mattapoisett", None) for d in (m, late)}
    store = sj.InMemoryStore()
    sj.run_silver(doc_records(m, MORTGAGE), meta, store, CountingGeocoder())
    assert "no_discharge" in loans_by_id(store)[f"{m}-0"].signals_unevaluable
    sj.run_silver(doc_records(late, LATE_DISCHARGE), meta, store, CountingGeocoder())
    assert "no_discharge" not in loans_by_id(store)[f"{m}-0"].signals_unevaluable


def test_null_parcel_key_loan_has_parcel_signals_unevaluable():
    d = "5" * 64
    text = MORTGAGE.replace("Parcel: MATT-1-1\n", "")
    store = sj.InMemoryStore()
    sj.run_silver(
        doc_records(d, text), {d: DocMeta("mattapoisett", None)}, store, CountingGeocoder()
    )
    loan = loans_by_id(store)[f"{d}-0"]
    assert {"second_lien", "no_discharge", "purchase_then_mortgage_90d"} <= set(
        loan.signals_unevaluable
    )


def test_extractor_failure_is_isolated_in_the_pipeline():
    full = all_records()
    store_a, store_b = sj.InMemoryStore(), sj.InMemoryStore()
    sj.run_silver(full, meta_for(full), store_a, CountingGeocoder())
    partial = [r for r in full if r.extractor != "pymupdf"]
    sj.run_silver(partial, meta_for(partial), store_b, CountingGeocoder())
    assert {r["instrument_id"] for r in store_a.read_instruments()} == {
        r["instrument_id"] for r in store_b.read_instruments()
    }
    assert len(store_b.read_loans()) == len(store_a.read_loans())


def test_failed_documents_do_not_block_others():
    recs = all_records() + golden_records("corrupt") + golden_records("oversize")
    store = sj.InMemoryStore()
    report = sj.run_silver(recs, meta_for(recs), store, CountingGeocoder())
    assert len(store.read_instruments()) == 7 and report.skipped == []


def test_reprocessing_a_document_replaces_its_instruments_without_leaving_stale_ones():
    d = "6" * 64
    meta = {d: DocMeta("mattapoisett", None)}
    store = sj.InMemoryStore()
    pages = [
        record(e, text, doc_id=d, page_no=i + 1)
        for e in EXTRACTORS
        for i, text in enumerate([MORTGAGE, OTHER_DISCHARGE])
    ]
    sj.run_silver(pages, meta, store, CountingGeocoder())
    assert len(store.read_instruments()) == 2
    sj.run_silver(doc_records(d, MORTGAGE), meta, store, CountingGeocoder())
    assert [r["instrument_id"] for r in store.read_instruments()] == [f"{d}-0"]


def test_pending_geocodes_are_reported_when_running_offline(tmp_path):
    recs = golden_records("mortgage")
    cache = g.JsonlGeocodeCache(tmp_path / "c.jsonl")
    geo = g.CachedGeocoder(cache, inner=None)
    store = sj.InMemoryStore()
    report = sj.run_silver(recs, meta_for(recs), store, geo)
    assert report.geocode_pending == ["12525 Obrien Squares Suite 947, Mattapoisett, MA"]
    assert store.read_loans()[0]["geocode_status"] == "no_match"
    # the host step fills the cache; the next run picks it up
    cache.put(report.geocode_pending[0], g.GeocodeResult("matched", 41.6, -70.8))
    again = sj.run_silver(recs, meta_for(recs), store, g.CachedGeocoder(cache, inner=None))
    assert again.geocode_pending == []
    assert store.read_loans()[0]["geocode_status"] == "matched"


def test_skipped_documents_are_in_the_report():
    recs = golden_records("mortgage")
    report = sj.run_silver(recs, {}, sj.InMemoryStore(), CountingGeocoder())
    assert [s.reason for s in report.skipped] == ["missing_manifest_meta"]


# -- manifests ---------------------------------------------------------------------------------


def write_manifest(path, doc_id, **kw):
    row = dict(
        doc_id=doc_id, source_url="https://p.example.test/x", fetched_at="2026-01-02T00:00:00Z",
        town="mattapoisett", status="fetched", size_bytes=10,
    )  # fmt: skip
    row.update(kw)
    (path / f"{doc_id}.json").write_text(json.dumps(row))


def test_manifest_dir_gives_doc_meta_and_coverage_rows(tmp_path):
    write_manifest(tmp_path, "a" * 64)
    write_manifest(tmp_path, "b" * 64, status="gap", gap_reason="paid", size_bytes=0, town="rye")
    (tmp_path / "junk.json").write_text("{not json")
    (tmp_path / "bad.json").write_text(json.dumps({"doc_id": "x"}))
    rows = sj.read_manifest_dir(tmp_path)
    assert {r.doc_id for r in rows} == {"a" * 64, "b" * 64}
    assert all(isinstance(r, Manifest) for r in rows)
    meta = sj.doc_meta_from_manifests(rows)
    assert meta == {"a" * 64: DocMeta("mattapoisett", "https://p.example.test/x")}
    assert sj.read_manifest_dir(tmp_path / "missing") == []


def test_reclassified_mortgage_to_deed_removes_its_orphan_loan(tmp_path):
    from silver_helpers import NOW

    from backend.ingest import publish as pub

    d = "7" * 64
    meta = {d: DocMeta("mattapoisett", None)}
    store = sj.InMemoryStore()
    sj.run_silver(doc_records(d, MORTGAGE), meta, store, CountingGeocoder())
    assert [r["loan_id"] for r in store.read_loans()] == [f"{d}-0"]

    as_deed = MORTGAGE.replace("MORTGAGE\n", "QUITCLAIM DEED\n", 1)
    sj.run_silver(doc_records(d, as_deed), meta, store, CountingGeocoder())
    assert store.read_instruments()[0]["doc_type"] == "deed"
    assert store.read_loans() == []

    other = "8" * 64
    sj.run_silver(
        doc_records(other, MORTGAGE.replace("MATT-1-1", "MATT-3-3")),
        {**meta, other: DocMeta("mattapoisett", None)},
        store,
        CountingGeocoder(),
    )
    source = pub.ArrowSilverSource(
        views.table_from_rows(store.read_loans(), views.LOAN_COLUMNS),
        views.table_from_rows(store.read_instruments(), views.INSTRUMENT_COLUMNS),
    )
    import tempfile

    with tempfile.TemporaryDirectory() as out:
        res = pub.publish(source, pub.LocalObjectStore(out), now=NOW)
    assert res.row_count == 1
