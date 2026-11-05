"""Fixtures for the WU5a extractor tests.

Tests marked ``docker`` build and run extractor containers; they are deselected (with a
visible notice) unless ``RUN_DOCKER=1``. ``EXTRACTORS`` (comma list) narrows which images.
"""

from __future__ import annotations

import os

import pytest

ALL = ["pymupdf", "docling", "unstructured"]


def selected_extractors() -> list[str]:
    raw = os.environ.get("EXTRACTORS")
    return [e for e in ALL if not raw or e in raw.split(",")]


def pytest_configure(config):
    config.addinivalue_line("markers", "docker: builds/runs extractor containers (RUN_DOCKER=1)")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_DOCKER") == "1":
        return
    deselected = [i for i in items if i.get_closest_marker("docker")]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = [i for i in items if not i.get_closest_marker("docker")]
        print(f"\n[docker] {len(deselected)} docker test(s) deselected; set RUN_DOCKER=1 to run")


@pytest.fixture(params=selected_extractors())
def extractor(request):
    return request.param
