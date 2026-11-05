"""Audit log of detail-endpoint access: key id + loan id only, never names.

Retention: entries older than ``retention_days`` are dropped by ``purge_expired`` (run at
startup and at most once per 24 h while writing). Unparseable lines are kept, never destroyed.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

PURGE_INTERVAL = timedelta(hours=24)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AuditLog:
    def __init__(
        self,
        path: Path | str,
        retention_days: int,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if retention_days < 1:
            raise ValueError("audit retention must be at least 1 day")
        self._path = Path(path)
        self._retention = timedelta(days=retention_days)
        self._clock = clock
        self._lock = threading.Lock()
        self._last_purge: datetime | None = None

    def record(self, key_id: str, loan_id: str, action: str) -> None:
        now = self._clock()
        entry = {"ts": now.isoformat(), "key_id": key_id, "loan_id": loan_id, "action": action}
        with self._lock:
            if self._last_purge is None or now - self._last_purge >= PURGE_INTERVAL:
                self._purge(now)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
                fh.flush()
                os.fsync(fh.fileno())

    def purge_expired(self) -> int:
        with self._lock:
            return self._purge(self._clock())

    def _purge(self, now: datetime) -> int:
        self._last_purge = now
        if not self._path.exists():
            return 0
        cutoff = now - self._retention
        kept, removed = [], 0
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            if self._expired(line, cutoff):
                removed += 1
            else:
                kept.append(line)
        if removed or not kept:
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("".join(line + "\n" for line in kept))
            os.replace(tmp, self._path)
        return removed

    @staticmethod
    def _expired(line: str, cutoff: datetime) -> bool:
        try:
            ts = datetime.fromisoformat(json.loads(line)["ts"])
        except (ValueError, KeyError, TypeError):
            return False
        return ts < cutoff
