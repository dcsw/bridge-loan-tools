"""Bridge-loan signals: computed and stored, unevaluable when data is absent."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from backend.common.models import Instrument

SIGNAL_NAMES: tuple[str, ...] = (
    "purchase_then_mortgage_90d",
    "second_lien",
    "short_term",
    "private_lender",
    "no_discharge",
    "keyword",
)
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "classifier.yaml"


class SignalParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    short_term_max_months: int = Field(ge=1)
    purchase_window_days: int = Field(ge=1)
    keywords: list[str] = Field(min_length=1)


class ClassifierConfig(BaseModel):
    """Versioned classifier + signal-weight configuration (config/classifier.yaml)."""

    model_config = ConfigDict(extra="forbid")
    classifier_version: str
    classifier_impl: str
    signals_version: str
    weights: dict[str, int]
    params: SignalParams


def load_config(path: Path | str | None = None) -> ClassifierConfig:
    with open(path or DEFAULT_CONFIG_PATH, encoding="utf-8") as fh:
        cfg = ClassifierConfig.model_validate(yaml.safe_load(fh))
    missing = set(SIGNAL_NAMES) - set(cfg.weights)
    if missing:
        raise ValueError(f"missing signal weights: {sorted(missing)}")
    return cfg


class SignalContext(BaseModel):
    """What is known around a mortgage; absence means 'cannot evaluate', not 'false'."""

    model_config = ConfigDict(extra="forbid")
    parcel_instruments: Sequence[Instrument] = ()
    deeds_ingested: bool = False
    discharges_ingested: bool = False
    text: str | None = None


class SignalResult(BaseModel):
    fired: list[str] = Field(default_factory=list)
    unevaluable: list[str] = Field(default_factory=list)
    score: int = 0


def _same_parcel(mortgage: Instrument, other: Instrument) -> bool:
    return other.parcel_key == mortgage.parcel_key and other.instrument_id != mortgage.instrument_id


def _short_term(m: Instrument, _ctx: SignalContext, cfg: ClassifierConfig) -> bool | None:
    months = m.term_months
    if months is None and m.recorded_date and m.maturity_date:
        months = round((m.maturity_date - m.recorded_date).days / 30.4375)
    if months is None:
        return None
    return months <= cfg.params.short_term_max_months


def _private_lender(m: Instrument, _ctx: SignalContext, _cfg: ClassifierConfig) -> bool | None:
    if m.lender_type in (None, "unknown"):
        return None
    return m.lender_type in ("private_llc", "individual")


def _purchase_then_mortgage(
    m: Instrument, ctx: SignalContext, cfg: ClassifierConfig
) -> bool | None:
    if not ctx.deeds_ingested or m.parcel_key is None or m.recorded_date is None:
        return None
    window = timedelta(days=cfg.params.purchase_window_days)
    return any(
        d.doc_type == "deed"
        and d.recorded_date is not None
        and _same_parcel(m, d)
        and timedelta(0) <= m.recorded_date - d.recorded_date <= window
        for d in ctx.parcel_instruments
    )


def _second_lien(m: Instrument, ctx: SignalContext, _cfg: ClassifierConfig) -> bool | None:
    if m.parcel_key is None or m.recorded_date is None:
        return None
    return any(
        o.doc_type == "mortgage"
        and o.recorded_date is not None
        and _same_parcel(m, o)
        and o.recorded_date < m.recorded_date
        for o in ctx.parcel_instruments
    )


def _no_discharge(m: Instrument, ctx: SignalContext, _cfg: ClassifierConfig) -> bool | None:
    ref = m.recorded_ref
    if not ctx.discharges_ingested or ref is None or m.parcel_key is None:
        return None  # no parcel_key -> parcel context cannot be gathered
    return not any(
        d.doc_type == "discharge" and _same_parcel(m, d) and d.refers_to_ref == ref
        for d in ctx.parcel_instruments
    )


def _keyword(_m: Instrument, ctx: SignalContext, cfg: ClassifierConfig) -> bool | None:
    if ctx.text is None:
        return None
    pattern = r"\b(?:" + "|".join(re.escape(k) for k in cfg.params.keywords) + r")\b"
    return re.search(pattern, ctx.text, flags=re.IGNORECASE) is not None


_EVALUATORS = {
    "purchase_then_mortgage_90d": _purchase_then_mortgage,
    "second_lien": _second_lien,
    "short_term": _short_term,
    "private_lender": _private_lender,
    "no_discharge": _no_discharge,
    "keyword": _keyword,
}


def compute_signals(
    mortgage: Instrument, ctx: SignalContext, cfg: ClassifierConfig | None = None
) -> SignalResult:
    """Evaluate every signal. True -> fired, None -> unevaluable (contributes 0)."""
    cfg = cfg or load_config()
    result = SignalResult()
    for name in SIGNAL_NAMES:
        outcome = _EVALUATORS[name](mortgage, ctx, cfg)
        if outcome is None:
            result.unevaluable.append(name)
        elif outcome:
            result.fired.append(name)
    result.score = min(100, sum(cfg.weights[n] for n in result.fired))
    return result
