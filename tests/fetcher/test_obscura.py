"""Obscura adapter is a documented stub (ADR-0003): it must fail loudly, never fake success."""

from __future__ import annotations

import pytest

from backend.fetcher.adapter import Fetcher
from backend.fetcher.allowlist import AllowList
from backend.fetcher.obscura import ObscuraFetcher, ObscuraNotAvailableError


def test_obscura_is_a_fetcher_but_refuses_to_run():
    f = ObscuraFetcher(AllowList(["portal.example.test"]))
    assert isinstance(f, Fetcher)
    with pytest.raises(ObscuraNotAvailableError, match="ADR-0003"):
        f.fetch("https://portal.example.test/a.pdf")


def test_obscura_context_manager_start_refuses():
    with (
        pytest.raises(ObscuraNotAvailableError),
        ObscuraFetcher(AllowList(["portal.example.test"])),
    ):
        pass
