"""Normalizers for money, dates, lender names and addresses."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}


def parse_money(raw: object) -> Decimal | None:
    """Parse currency text/numbers into Decimal; None when missing or unparseable."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int | float):
        return Decimal(str(raw))
    text = str(raw).strip()
    if not text:
        return None
    negative = (text.startswith("(") and text.endswith(")")) or text.startswith("-")
    cleaned = re.sub(r"(?i)usd|dollars?", "", text)
    cleaned = re.sub(r"[\s$,()\-]", "", cleaned)
    if not re.fullmatch(r"\d+(\.\d+)?|\.\d+", cleaned):
        return None
    value = Decimal(cleaned)
    return -value if negative else value


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _year(s: str) -> int:
    y = int(s)
    if len(s) == 2:
        return 2000 + y if y <= 50 else 1900 + y
    return y


def parse_date(raw: object) -> date | None:
    """Parse a date; US month-first for numeric forms (day-first only if month>12).

    Returns None when the value is missing, has no day (e.g. "March 2021"), or is
    impossible. Two-digit years pivot at 50 (<=50 -> 20xx).
    """
    if isinstance(raw, date):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})", text)
    if m:
        a, b, y = int(m[1]), int(m[2]), _year(m[3])
        if a > 12:  # cannot be a month: treat as day-first
            return _safe_date(y, b, a)
        return _safe_date(y, a, b)
    lowered = text.lower().replace(",", " ")
    m = re.fullmatch(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\d{4})", lowered)
    if m and m[1] in _MONTHS:
        return _safe_date(int(m[3]), _MONTHS[m[1]], int(m[2]))
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\s+(\d{4})", lowered)
    if m and m[2] in _MONTHS:
        return _safe_date(int(m[3]), _MONTHS[m[2]], int(m[1]))
    return None


_ENTITY_SUFFIXES = {"L L C": "LLC", "N A": "NA"}


def normalize_lender_name(raw: str | None) -> str | None:
    """Uppercase, strip punctuation/articles, canonicalize entity suffixes."""
    if not raw:
        return None
    text = raw.upper().replace("&", " AND ").replace("'", "")
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for spaced, short in _ENTITY_SUFFIXES.items():
        text = re.sub(rf"\b{spaced}\b", short, text)
    text = re.sub(r"^THE ", "", text)
    return text or None


def classify_lender_type(normalized: str | None) -> str:
    """Heuristic lender type from a normalized lender name."""
    if not normalized:
        return "unknown"
    words = set(normalized.split())
    if "CREDIT UNION" in normalized:
        return "credit_union"
    if words & {"BANK", "NA", "SAVINGS", "TRUST"} or "COOPERATIVE" in words:
        return "bank"
    if words & {"LLC", "INC", "CORP", "LP", "LLP", "FUND", "CAPITAL", "LENDING", "HOLDINGS"}:
        return "private_llc"
    if words & {"COMMONWEALTH", "TOWN", "CITY", "STATE", "UNITED", "MORTGAGE", "MERS"}:
        return "other"
    return "individual"


_STREET = {
    "STREET": "ST",
    "ROAD": "RD",
    "AVENUE": "AVE",
    "DRIVE": "DR",
    "LANE": "LN",
    "COURT": "CT",
    "BOULEVARD": "BLVD",
    "PLACE": "PL",
    "TERRACE": "TER",
    "CIRCLE": "CIR",
    "HIGHWAY": "HWY",
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
    "APARTMENT": "APT",
}


def normalize_address(raw: str | None) -> str | None:
    """Uppercase, strip punctuation, abbreviate street types/directions, '#' -> UNIT."""
    if not raw:
        return None
    text = raw.upper().replace("#", " UNIT ")
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    tokens = [_STREET.get(t, t) for t in text.split()]
    return " ".join(tokens) or None
