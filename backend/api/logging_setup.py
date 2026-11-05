"""API logging: one shared redaction filter on every handler we own, uvicorn's included."""

from __future__ import annotations

import logging
from typing import TextIO

from backend.common.logging import RedactionFilter

API_LOGGER = "bridge_loan.api"
_OWNED = (API_LOGGER, "uvicorn", "uvicorn.error", "uvicorn.access")

REDACTOR = RedactionFilter()


def configure_logging(stream: TextIO | None = None) -> logging.Logger:
    """(Re)install redacting handlers on the API and uvicorn loggers; idempotent."""
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(REDACTOR)
    for name in _OWNED:
        logger = logging.getLogger(name)
        for old in list(logger.handlers):
            if any(f is REDACTOR for f in old.filters):
                logger.removeHandler(old)
        logger.addHandler(handler)
        logger.propagate = False
        logger.setLevel(logging.INFO)
    return logging.getLogger(API_LOGGER)


def register_sensitive(name: str | None) -> None:
    """Teach the filter a name seen in data so any later log line is scrubbed."""
    if name:
        REDACTOR.register(name)
