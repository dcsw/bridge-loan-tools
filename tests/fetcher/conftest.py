"""Fixtures for fetcher tests: a local fake portal and a test-mode allow-list."""

from __future__ import annotations

import pytest

from backend.fetcher.allowlist import AllowList
from tests.fakeportal.server import FakePortal


@pytest.fixture
def portal():
    with FakePortal() as p:
        yield p


@pytest.fixture
def portal_allowlist(monkeypatch, portal):
    """Allow-list for the fake portal (loopback, http) via the test-only override."""
    monkeypatch.setenv("FETCHER_TEST_MODE", "1")
    return AllowList([portal.host], allow_private_for_tests=True)
