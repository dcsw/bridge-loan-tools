"""Obscura adapter: documented stub (see docs/adr/0003-obscura-spike.md).

Obscura (Rust headless browser, CDP-compatible) was researched but not run: no pinned,
verified binary is wired into the fetcher image. This stub keeps the adapter slot in the
``Fetcher`` interface and fails loudly instead of pretending to fetch.
"""

from __future__ import annotations

from backend.fetcher.adapter import Fetcher, FetchResult
from backend.fetcher.allowlist import AllowList


class ObscuraNotAvailableError(RuntimeError):
    """Obscura is not integrated yet."""


_MESSAGE = (
    "Obscura adapter is a stub (ADR-0003, docs/adr/0003-obscura-spike.md); use PlaywrightFetcher"
)


class ObscuraFetcher(Fetcher):
    def __init__(self, allowlist: AllowList) -> None:
        self.allowlist = allowlist

    def start(self) -> None:
        raise ObscuraNotAvailableError(_MESSAGE)

    def fetch(self, url: str) -> FetchResult:
        raise ObscuraNotAvailableError(_MESSAGE)
