"""WU6 DoD 3 and 5: loans derived per parcel, late discharge, unevaluable signals, classifier."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from silver_helpers import SHA, census, make_instrument

from backend.common.classify import Classification
from backend.ingest import geocode as g
from backend.ingest import loans as ln
from backend.ingest.unify import UnifiedInstrument

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FixedGeocoder:
    def __init__(self, result=None):
        self.result = result or g.GeocodeResult("matched", 41.65, -70.81)
        self.calls = []

    def geocode(self, address):
        self.calls.append(address)
        return self.result


def rec(inst, text="", ingested=T0):
    return UnifiedInstrument(inst, None, text, ingested)


def doc(n):
    return f"{n:064x}"


def mortgage(n=1, **kw):
    return make_instrument(0, doc_id=doc(n), recorded_book="1", recorded_page=str(n), **kw)


def deed(n=2, **kw):
    return make_instrument(
        0, doc_id=doc(n), doc_type="deed", recorded_date=date(2023, 12, 20), term_months=None,
        principal=None, lender_type=None, **kw
    )  # fmt: skip


def discharge(n=3, refers="1/1", **kw):
    return make_instrument(
        0, doc_id=doc(n), doc_type="discharge", recorded_date=date(2025, 1, 1), term_months=None,
        principal=None, lender_type=None, refers_to_ref=refers, **kw
    )  # fmt: skip


def derive(records, geocoder=None, **kw):
    return {
        x.loan_id: x for x in ln.derive_loans(records, geocoder=geocoder or FixedGeocoder(), **kw)
    }


def test_only_mortgages_become_loans_with_loan_id_equal_instrument_id():
    m, d = mortgage(), deed()
    out = derive([rec(m), rec(d)])
    assert list(out) == [m.instrument_id]


def test_purchase_then_mortgage_fires_for_deed_within_window_on_same_parcel():
    out = derive([rec(mortgage()), rec(deed())])
    loan = out[doc(1) + "-0"]
    assert "purchase_then_mortgage_90d" in loan.signals
    assert "purchase_then_mortgage_90d" not in loan.signals_unevaluable


def test_deed_on_other_parcel_does_not_fire():
    out = derive([rec(mortgage()), rec(deed(parcel_key="MATT-9-9"))])
    assert "purchase_then_mortgage_90d" not in out[doc(1) + "-0"].signals


def test_second_lien_fires_when_earlier_mortgage_on_parcel():
    first = mortgage(2, recorded_date=date(2023, 1, 1))
    second = mortgage(1)
    out = derive([rec(first), rec(second)])
    assert "second_lien" in out[second.instrument_id].signals
    assert "second_lien" not in out[first.instrument_id].signals


def test_no_discharge_is_unevaluable_until_discharges_are_ingested_for_the_town():
    loan = derive([rec(mortgage())])[doc(1) + "-0"]
    assert "no_discharge" in loan.signals_unevaluable and "no_discharge" not in loan.signals


def test_late_discharge_flips_no_discharge():
    other = discharge(5, refers="9/9", parcel_key="MATT-7-7")  # town now has discharges
    before = [rec(mortgage()), rec(other)]
    first = derive(before)[doc(1) + "-0"]
    assert "no_discharge" in first.signals  # still outstanding

    after = [*before, rec(discharge(3, refers="1/1"))]
    second = derive(after)[doc(1) + "-0"]
    assert "no_discharge" not in second.signals
    assert "no_discharge" not in second.signals_unevaluable


def test_discharge_referring_to_a_different_mortgage_does_not_clear_no_discharge():
    out = derive([rec(mortgage()), rec(discharge(3, refers="1/77"))])
    assert "no_discharge" in out[doc(1) + "-0"].signals


def test_null_parcel_key_makes_parcel_dependent_signals_unevaluable():
    m = mortgage(parcel_key=None)
    out = derive([rec(m), rec(deed(parcel_key=None)), rec(discharge(parcel_key=None))])[
        m.instrument_id
    ]
    assert {"purchase_then_mortgage_90d", "second_lien", "no_discharge"} <= set(
        out.signals_unevaluable
    )
    assert not {"purchase_then_mortgage_90d", "second_lien", "no_discharge"} & set(out.signals)
    assert "short_term" in out.signals  # parcel-independent signals still evaluate


def test_unknown_lender_and_term_are_unevaluable_not_false():
    m = mortgage(lender_type="unknown", term_months=None)
    out = derive([rec(m)])[m.instrument_id]
    assert {"private_lender", "short_term"} <= set(out.signals_unevaluable)


def test_keyword_signal_reads_instrument_text():
    out = derive([rec(mortgage(), text="This is a BRIDGE loan note")])[doc(1) + "-0"]
    assert "keyword" in out.signals
    out = derive([rec(mortgage(), text="plain")])[doc(1) + "-0"]
    assert "keyword" not in out.signals


def test_placeholder_classifier_marks_every_loan_a_bridge_loan_with_version():
    recs = [
        rec(mortgage(1, term_months=360, lender_type="bank")),
        rec(mortgage(2, parcel_key=None)),
    ]
    for loan in derive(recs).values():
        assert loan.is_bridge_loan is True
        assert loan.bridge_score == 100
        assert loan.classifier_version == "placeholder-all-true"


def test_classifier_is_actually_invoked_and_its_verdict_stored():
    seen = []

    def spy(loan, cfg):
        seen.append(loan.loan_id)
        return Classification(is_bridge_loan=False, bridge_score=7, classifier_version="spy-v1")

    out = derive([rec(mortgage())], classify=spy)[doc(1) + "-0"]
    assert seen == [doc(1) + "-0"]
    assert (out.is_bridge_loan, out.bridge_score, out.classifier_version) == (False, 7, "spy-v1")


def test_geocode_results_flow_to_loan_and_no_match_keeps_the_row_without_geometry():
    matched = derive([rec(mortgage())], FixedGeocoder(g.GeocodeResult("matched", 41.6, -70.8)))
    loan = matched[doc(1) + "-0"]
    assert (loan.geocode_status, loan.lat, loan.lon) == ("matched", 41.6, -70.8)

    low = derive([rec(mortgage())], FixedGeocoder(g.GeocodeResult("low_match", 41.6, -70.8)))
    assert low[doc(1) + "-0"].geocode_status == "low_match"

    none = derive([rec(mortgage())], FixedGeocoder(g.NO_MATCH))[doc(1) + "-0"]
    assert (none.geocode_status, none.lat, none.lon) == ("no_match", None, None)


def test_geocoder_uses_address_raw_and_recorded_census_responses_end_to_end():
    address = census("matched")["request"]["address"]
    m = mortgage(address_raw=address)
    for name, status in [
        ("matched", "matched"),
        ("low_match", "low_match"),
        ("no_match", "no_match"),
    ]:
        geo = g.CensusGeocoder(g.RecordedTransport({address: census(name)["response"]}))
        assert derive([rec(m)], geo)[m.instrument_id].geocode_status == status


def test_last_ingested_at_is_latest_across_the_parcel():
    later = T0 + timedelta(days=3)
    out = derive([rec(mortgage(), ingested=T0), rec(discharge(3), ingested=later)])
    assert out[doc(1) + "-0"].last_ingested_at == later


def test_scope_limits_which_loans_are_rebuilt_and_geocoded():
    geo = FixedGeocoder()
    recs = [rec(mortgage(1)), rec(mortgage(2, parcel_key="MATT-2-2"))]
    out = derive(recs, geo, scope={doc(2) + "-0"})
    assert list(out) == [doc(2) + "-0"] and len(geo.calls) == 1


# -- which loans to recompute after a MERGE --------------------------------------------------


def test_scope_is_the_parcel_of_every_changed_instrument():
    a, b = mortgage(1), mortgage(2, parcel_key="MATT-2-2")
    before = [a, b]
    d = discharge(3, refers="1/1")
    scope = ln.loans_scope(before, [*before, d], {d.instrument_id})
    assert scope == {a.instrument_id, b.instrument_id}  # first discharge in town widens to town


def test_scope_stays_on_parcel_when_town_flags_do_not_change():
    a, b = mortgage(1), mortgage(2, parcel_key="MATT-2-2")
    other = discharge(5, refers="9/9", parcel_key="MATT-7-7")
    before = [a, b, other]
    d = discharge(3, refers="1/1")
    scope = ln.loans_scope(before, [*before, d], {d.instrument_id})
    assert scope == {a.instrument_id}


def test_scope_includes_changed_mortgages_with_null_parcel():
    a = mortgage(1, parcel_key=None)
    b = mortgage(2, parcel_key=None)
    scope = ln.loans_scope([b], [a, b], {a.instrument_id})
    assert scope == {a.instrument_id}


def test_scope_follows_a_parcel_key_that_changed():
    old = mortgage(1, parcel_key="MATT-1-1")
    new = mortgage(1, parcel_key="MATT-5-5")
    other_old_parcel = mortgage(2, parcel_key="MATT-1-1")
    scope = ln.loans_scope([old, other_old_parcel], [new, other_old_parcel], {new.instrument_id})
    assert scope == {new.instrument_id, other_old_parcel.instrument_id}


def test_default_hash_of_unused_sha_helper():
    assert SHA == "a" * 64
