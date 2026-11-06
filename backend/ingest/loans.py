"""Silver loans: derived from mortgage instruments, recomputed per parcel (design 3.2).

* ``loan_id`` = ``instrument_id`` of the mortgage. Only mortgages become loans.
* Signals come from ``backend.common.signals``; whatever the data cannot support is reported in
  ``signals_unevaluable`` (a null ``parcel_key`` makes every parcel-dependent signal unevaluable).
  ``deeds_ingested`` / ``discharges_ingested`` are true when the mortgage's *town* has at least one
  deed / discharge in silver: without any, absence of a discharge proves nothing.
* The classifier (``backend.common.classify``) is always invoked; v0 is the all-true placeholder.
* :func:`loans_scope` selects which loans to rebuild after an instruments MERGE: every mortgage on
  the parcel of every changed instrument, plus every mortgage of a town whose deed/discharge
  coverage flags just changed.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from typing import Any

from backend.common.classify import Classification, classify_loan
from backend.common.models import Instrument, Loan
from backend.common.signals import ClassifierConfig, SignalContext, compute_signals, load_config
from backend.ingest.geocode import Geocoder
from backend.ingest.unify import UnifiedInstrument

Classify = Callable[[Any, ClassifierConfig], Classification]
TownFlags = dict[str, tuple[bool, bool]]  # town -> (deeds_ingested, discharges_ingested)


def town_flags(instruments: Sequence[Instrument]) -> TownFlags:
    flags: dict[str, list[bool]] = {}
    for ins in instruments:
        entry = flags.setdefault(ins.town, [False, False])
        if ins.doc_type == "deed":
            entry[0] = True
        elif ins.doc_type == "discharge":
            entry[1] = True
    return {town: (e[0], e[1]) for town, e in flags.items()}


def _parcel(ins: Instrument) -> tuple[str, str] | None:
    return None if ins.parcel_key is None else (ins.town, ins.parcel_key)


def derive_loans(
    records: Sequence[UnifiedInstrument],
    *,
    geocoder: Geocoder,
    cfg: ClassifierConfig | None = None,
    classify: Classify = classify_loan,
    scope: Collection[str] | None = None,
) -> list[Loan]:
    """Build loans for the mortgages in ``scope`` (None = all) from the full instrument set."""
    cfg = cfg or load_config()
    flags = town_flags([r.instrument for r in records])
    by_parcel: dict[tuple[str, str], list[UnifiedInstrument]] = {}
    for r in records:
        parcel = _parcel(r.instrument)
        if parcel is not None:
            by_parcel.setdefault(parcel, []).append(r)

    loans: list[Loan] = []
    for rec in sorted(records, key=lambda r: r.instrument.instrument_id):
        m = rec.instrument
        if m.doc_type != "mortgage" or (scope is not None and m.instrument_id not in scope):
            continue
        parcel = _parcel(m)
        context = by_parcel.get(parcel, []) if parcel is not None else []
        deeds, discharges = flags.get(m.town, (False, False))
        ctx = SignalContext(
            parcel_instruments=[c.instrument for c in context],
            deeds_ingested=deeds,
            discharges_ingested=discharges,
            text=rec.text,
        )
        result = compute_signals(m, ctx, cfg)
        geo = geocoder.geocode(m.address_raw)
        loan = Loan(
            loan_id=m.instrument_id,
            lat=geo.lat,
            lon=geo.lon,
            geocode_status=geo.status,
            classifier_version=cfg.classifier_version,
            signals=result.fired,
            signals_unevaluable=result.unevaluable,
            last_ingested_at=max(c.ingested_at for c in [rec, *context]),
        )
        verdict = classify(loan, cfg)
        loans.append(
            loan.model_copy(
                update={
                    "is_bridge_loan": verdict.is_bridge_loan,
                    "bridge_score": verdict.bridge_score,
                    "classifier_version": verdict.classifier_version,
                }
            )
        )
    return loans


def loans_scope(
    before: Sequence[Instrument], after: Sequence[Instrument], changed_ids: Collection[str]
) -> set[str]:
    """Loan ids to recompute after the MERGE that turned ``before`` into ``after``."""
    changed = set(changed_ids)
    parcels = {_parcel(i) for i in [*before, *after] if i.instrument_id in changed} - {None}
    flags_before, flags_after = town_flags(before), town_flags(after)
    towns = {t for t in {*flags_before, *flags_after} if flags_before.get(t) != flags_after.get(t)}
    return {
        i.instrument_id
        for i in after
        if i.doc_type == "mortgage"
        and (i.instrument_id in changed or _parcel(i) in parcels or i.town in towns)
    }
