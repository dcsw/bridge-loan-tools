"""Smoke test: the suite collects and runs."""

import importlib


def test_smoke():
    assert 1 + 1 == 2


def test_backend_packages_importable():
    for name in ("common", "api", "ingest", "fetcher"):
        assert importlib.import_module(f"backend.{name}") is not None
