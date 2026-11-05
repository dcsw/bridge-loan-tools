"""Structured-logging helpers with a borrower-name redaction filter."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

REDACTED = "[REDACTED]"
# key=value, key: "value" and JSON "key": "value" for PII-bearing keys.
_KV = re.compile(
    r"""(?x)
    (?P<key>["']?(?i:borrower|grantor|grantee)["']?\s*[:=]\s*)
    (?:"[^"]*"|'[^']*'|[A-Za-z][A-Za-z.'\-]*(?:\s+[A-Z][A-Za-z.'\-]*)*)
    """
)


class RedactionFilter(logging.Filter):
    """Redacts registered names (case-insensitive) and borrower/grantor key values."""

    def __init__(self, names: Iterable[str] = ()) -> None:
        super().__init__()
        self._names: set[str] = set()
        for n in names:
            self.register(n)

    def register(self, name: str) -> None:
        name = name.strip()
        if name:
            self._names.add(name)

    def _scrub(self, text: str) -> str:
        for name in sorted(self._names, key=len, reverse=True):
            text = re.sub(re.escape(name), REDACTED, text, flags=re.IGNORECASE)
        return _KV.sub(lambda m: f"{m['key']}{REDACTED}", text)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = self._scrub(message)
        record.args = None
        if record.exc_info:
            # Render and scrub the traceback; Formatter reuses a preset exc_text.
            record.exc_text = self._scrub(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = self._scrub(record.exc_text)
        return True


def get_logger(name: str, names: Iterable[str] = ()) -> logging.Logger:
    """Return a logger whose handlers redact PII; installs one filter, reused on re-calls."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())
    existing = next(
        (f for h in logger.handlers for f in h.filters if isinstance(f, RedactionFilter)), None
    )
    if existing is None:
        existing = RedactionFilter()
        for h in logger.handlers:
            h.addFilter(existing)
    for n in names:
        existing.register(n)
    return logger
