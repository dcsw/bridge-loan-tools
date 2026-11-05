"""Signals: computed, stored, unevaluable when data is absent."""

from datetime import date

import pytest

from backend.common.models import Instrument
from backend.common.signals import SIGNAL_NAMES, SignalContext, compute_signals, load_config

SHA = "b" * 64
CFG = load_config()


def inst(n, doc_type="mortgage", **kw):
    d = f"{n:064x}"
    base = {
        "instrument_id": f"{d}-0",
        "doc_id": d,
        "ordinal": 0,
        "doc_type": doc_type,
        "town": "mattapoisett",
        "parcel_key": "MATT-1-2",
    }
    base.update(kw)
    return Instrument(**base)


def full_ctx(**kw):
    base = {"deeds_ingested": True, "discharges_ingested": True, "text": "plain text"}
    base.update(kw)
    return SignalContext(**base)


def test_config_loaded():
    assert CFG.classifier_version == "placeholder-all-true"
    assert set(CFG.weights) == set(SIGNAL_NAMES)
    assert CFG.params.short_term_max_months == 24


def test_all_unevaluable_when_nothing_known():
    m = inst(1, parcel_key=None)
    r = compute_signals(m, SignalContext(), CFG)
    assert r.fired == []
    assert set(r.unevaluable) == set(SIGNAL_NAMES)
    assert r.score == 0


def test_short_term_from_term_and_dates_and_boundaries():
    assert "short_term" in compute_signals(inst(1, term_months=24), full_ctx(), CFG).fired
    assert "short_term" not in compute_signals(inst(1, term_months=25), full_ctx(), CFG).fired
    m = inst(1, recorded_date=date(2024, 1, 1), maturity_date=date(2024, 12, 1))
    assert "short_term" in compute_signals(m, full_ctx(), CFG).fired
    m = inst(1, recorded_date=date(2024, 1, 1))
    assert "short_term" in compute_signals(m, full_ctx(), CFG).unevaluable


@pytest.mark.parametrize(
    ("lt", "fired", "unev"),
    [
        ("private_llc", True, False),
        ("individual", True, False),
        ("bank", False, False),
        ("credit_union", False, False),
        ("other", False, False),
        ("unknown", False, True),
        (None, False, True),
    ],
)
def test_private_lender(lt, fired, unev):
    r = compute_signals(inst(1, lender_type=lt), full_ctx(), CFG)
    assert ("private_lender" in r.fired) is fired
    assert ("private_lender" in r.unevaluable) is unev


def test_purchase_then_mortgage_90d():
    m = inst(1, recorded_date=date(2024, 6, 1))
    deed_in = inst(2, "deed", recorded_date=date(2024, 3, 15))
    deed_old = inst(3, "deed", recorded_date=date(2023, 1, 1))
    deed_after = inst(4, "deed", recorded_date=date(2024, 7, 1))
    deed_other = inst(5, "deed", recorded_date=date(2024, 5, 1), parcel_key="OTHER")
    deed_nodate = inst(6, "deed")
    ok = compute_signals(m, full_ctx(parcel_instruments=[deed_in]), CFG)
    assert "purchase_then_mortgage_90d" in ok.fired
    for d in (deed_old, deed_after, deed_other, deed_nodate):
        r = compute_signals(m, full_ctx(parcel_instruments=[d]), CFG)
        assert "purchase_then_mortgage_90d" not in r.fired
        assert "purchase_then_mortgage_90d" not in r.unevaluable
    edge = inst(7, "deed", recorded_date=date(2024, 3, 3))  # exactly 90 days
    assert (
        "purchase_then_mortgage_90d"
        in compute_signals(m, full_ctx(parcel_instruments=[edge]), CFG).fired
    )


def test_purchase_unevaluable_cases():
    m = inst(1, recorded_date=date(2024, 6, 1))
    assert (
        "purchase_then_mortgage_90d"
        in compute_signals(m, full_ctx(deeds_ingested=False), CFG).unevaluable
    )
    assert (
        "purchase_then_mortgage_90d"
        in compute_signals(
            inst(1, parcel_key=None, recorded_date=date(2024, 6, 1)), full_ctx(), CFG
        ).unevaluable
    )
    assert "purchase_then_mortgage_90d" in compute_signals(inst(1), full_ctx(), CFG).unevaluable


def test_second_lien():
    m = inst(1, recorded_date=date(2024, 6, 1))
    earlier = inst(2, recorded_date=date(2023, 6, 1))
    later = inst(3, recorded_date=date(2025, 6, 1))
    other = inst(4, recorded_date=date(2023, 6, 1), parcel_key="OTHER")
    deed = inst(5, "deed", recorded_date=date(2023, 6, 1))
    assert "second_lien" in compute_signals(m, full_ctx(parcel_instruments=[earlier]), CFG).fired
    for x in (later, other, deed, m):
        assert "second_lien" not in compute_signals(m, full_ctx(parcel_instruments=[x]), CFG).fired
    assert "second_lien" in compute_signals(inst(1, parcel_key=None), full_ctx(), CFG).unevaluable
    assert "second_lien" in compute_signals(inst(1), full_ctx(), CFG).unevaluable
    undated = inst(6)
    assert (
        "second_lien" not in compute_signals(m, full_ctx(parcel_instruments=[undated]), CFG).fired
    )


def test_no_discharge():
    m = inst(1, recorded_book="10", recorded_page="20")
    dis = inst(2, "discharge", refers_to_ref="10/20")
    dis_other = inst(3, "discharge", refers_to_ref="99/99")
    assert "no_discharge" in compute_signals(m, full_ctx(parcel_instruments=[dis_other]), CFG).fired
    assert "no_discharge" not in compute_signals(m, full_ctx(parcel_instruments=[dis]), CFG).fired
    assert (
        "no_discharge" not in compute_signals(m, full_ctx(parcel_instruments=[]), CFG).unevaluable
    )
    assert (
        "no_discharge" in compute_signals(m, full_ctx(discharges_ingested=False), CFG).unevaluable
    )
    # parcel_key null: parcel context unavailable, so unevaluable (never fires from absent data)
    r = compute_signals(
        inst(1, parcel_key=None, recorded_book="1", recorded_page="2"),
        full_ctx(parcel_instruments=[]),
        CFG,
    )
    assert "no_discharge" in r.unevaluable
    assert "no_discharge" not in r.fired
    # a discharge on a different parcel does not count
    other = inst(4, "discharge", refers_to_ref="10/20", parcel_key="OTHER")
    assert "no_discharge" in compute_signals(m, full_ctx(parcel_instruments=[other]), CFG).fired
    # without a recorded ref nothing can be matched: unevaluable
    assert "no_discharge" in compute_signals(inst(1), full_ctx(), CFG).unevaluable


def test_keyword():
    for text in ("a BRIDGE loan", "interim financing", "Swing note"):
        assert "keyword" in compute_signals(inst(1), full_ctx(text=text), CFG).fired
    assert "keyword" not in compute_signals(inst(1), full_ctx(text="overbridge road"), CFG).fired
    assert "keyword" in compute_signals(inst(1), full_ctx(text=None), CFG).unevaluable


def test_score_is_sum_of_fired_weights():
    m = inst(1, term_months=12, lender_type="private_llc")
    r = compute_signals(m, full_ctx(text="bridge"), CFG)
    assert set(r.fired) >= {"short_term", "private_lender", "keyword"}
    assert r.score == sum(CFG.weights[s] for s in r.fired)
    assert r.score <= 100


def test_load_config_rejects_missing_weight(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "classifier_version: v\nclassifier_impl: placeholder_all_true\nsignals_version: v1\n"
        "weights: {short_term: 1}\n"
        "params: {short_term_max_months: 24, purchase_window_days: 90, keywords: [bridge]}\n"
    )
    with pytest.raises(ValueError, match="missing signal weights"):
        load_config(p)
