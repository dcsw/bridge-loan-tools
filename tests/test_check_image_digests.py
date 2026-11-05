"""Tests for scripts/check_image_digests.py (digest-pin enforcement)."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_image_digests", ROOT / "scripts" / "check_image_digests.py"
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

DIGEST = "sha256:" + "a" * 64


def test_unpinned_dockerfile_fails(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\nRUN true\n")
    violations = mod.find_violations(tmp_path)
    assert len(violations) == 1
    assert "python:3.12-slim" in violations[0]
    assert mod.main([str(tmp_path)]) == 1


def test_pinned_dockerfile_passes(tmp_path):
    (tmp_path / "Dockerfile").write_text(f"FROM python:3.12-slim@{DIGEST}\n")
    assert mod.find_violations(tmp_path) == []
    assert mod.main([str(tmp_path)]) == 0


def test_unpinned_compose_image_fails(tmp_path):
    (tmp_path / "compose.yaml").write_text("services:\n  db:\n    image: postgres:16\n")
    violations = mod.find_violations(tmp_path)
    assert len(violations) == 1
    assert "postgres:16" in violations[0]


def test_pinned_compose_image_passes(tmp_path):
    (tmp_path / "compose.api.yaml").write_text(
        f"services:\n  db:\n    image: 'postgres:16@{DIGEST}'\n"
    )
    assert mod.find_violations(tmp_path) == []


def test_short_or_malformed_digest_fails(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM python@sha256:abc\n")
    assert len(mod.find_violations(tmp_path)) == 1


@pytest.mark.parametrize(
    "text",
    [
        "FROM scratch\n",
        f"FROM python:3.12@{DIGEST} AS build\nFROM build AS final\n",
        f"FROM --platform=linux/amd64 python:3.12@{DIGEST}\n",
    ],
)
def test_scratch_and_stage_references_allowed(tmp_path, text):
    (tmp_path / "Dockerfile").write_text(text)
    assert mod.find_violations(tmp_path) == []


def test_stage_alias_flagged_when_base_unpinned(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM python:3.12 AS build\nFROM build\n")
    assert len(mod.find_violations(tmp_path)) == 1


def test_scans_nested_and_named_dockerfiles(tmp_path):
    nested = tmp_path / "deploy" / "x"
    nested.mkdir(parents=True)
    (nested / "api.Dockerfile").write_text("FROM alpine\n")
    (nested / "Dockerfile.web").write_text("FROM node:22\n")
    assert len(mod.find_violations(tmp_path)) == 2


def test_skips_vendored_dirs(tmp_path):
    vendored = tmp_path / "frontend" / "node_modules" / "p"
    vendored.mkdir(parents=True)
    (vendored / "Dockerfile").write_text("FROM alpine\n")
    assert mod.find_violations(tmp_path) == []


def test_empty_repo_passes(tmp_path):
    assert mod.main([str(tmp_path)]) == 0


def test_repo_itself_is_clean():
    assert mod.find_violations(ROOT) == []
