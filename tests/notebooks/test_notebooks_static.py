"""WU10 DoD 2-4: notebooks import from backend, ship stripped outputs, query semantic_loans_v1."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import jupytext
import pytest

ROOT = Path(__file__).resolve().parents[2]
NB_DIR = ROOT / "notebooks"
SOURCES = sorted(NB_DIR.glob("[0-9]*.py"))
EXPECTED = ["01_fetch", "02_bronze", "03_silver", "04_geocode", "05_explore"]


def _text(source: str | list[str]) -> str:
    return source if isinstance(source, str) else "".join(source)


def test_expected_notebooks_exist():
    assert [p.stem for p in SOURCES] == EXPECTED


@pytest.mark.parametrize("src", SOURCES, ids=lambda p: p.stem)
def test_ipynb_is_in_sync_and_stripped(src):
    ipynb = src.with_suffix(".ipynb")
    assert ipynb.exists(), f"run `make notebooks-sync` ({ipynb.name} missing)"
    nb = json.loads(ipynb.read_text())
    code = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert code
    for cell in code:
        assert cell["outputs"] == [], "outputs must be stripped"
        assert cell["execution_count"] is None
    expected = jupytext.reads(src.read_text(), fmt="py:percent")
    assert [_text(c["source"]) for c in nb["cells"]] == [
        _text(c["source"]) for c in expected["cells"]
    ]


@pytest.mark.parametrize("src", SOURCES, ids=lambda p: p.stem)
def test_notebooks_define_no_pipeline_logic(src):
    """Only thin demo adapters/sinks are allowed; anything else must be imported from backend/*."""
    tree = ast.parse(src.read_text())
    allowed = {"StdlibFetcher", "MemorySink"}
    defined = {n.name for n in tree.body if isinstance(n, ast.ClassDef | ast.FunctionDef)}
    assert defined <= allowed, f"unexpected local definitions: {defined - allowed}"
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Lambda)], "no lambdas"
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        assert len(cls.body) <= 6 and cls.end_lineno - cls.lineno <= 15, "adapter must stay thin"
        assert not [n for n in ast.walk(cls) if isinstance(n, ast.ClassDef) and n is not cls]
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
    assert any(m.startswith("backend.") for m in imports)


def test_explore_queries_semantic_loans_v1():
    text = (NB_DIR / "05_explore.py").read_text()
    assert "semantic_loans_v1" in text and "create_semantic_views" in text
