"""WU5a DoD 1-3, 5 against the real extractor containers (needs Docker; RUN_DOCKER=1).

Containers always run via ``docker compose run`` so the service definition's
``network_mode: none`` / read-only FS / limits are what is under test.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.common.models import BronzeRecord
from backend.ingest.extractors.golden import compare_to_golden, missing_key_fields

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker" / "compose.extractors.yaml"
PDFS = ROOT / "tests" / "fixtures" / "pdfs"
GOLDEN = ROOT / "tests" / "fixtures" / "golden"
TRUTH = ROOT / "tests" / "fixtures" / "truth"
BASE = ["docker", "compose", "-p", "bridgeloan-extract", "-f", str(COMPOSE)]
FIXTURES = [
    "mortgage",
    "deed",
    "discharge",
    "multi_instrument",
    "scanned_image",
    "corrupt",
    "oversize",
]


def _env(in_dir: Path, out_dir: Path) -> dict[str, str]:
    return {
        **os.environ,
        "EXTRACT_IN_DIR": str(in_dir),
        "EXTRACT_OUT_DIR": str(out_dir),
        "EXTRACT_UID": str(os.getuid()),
        "EXTRACT_GID": str(os.getgid()),
    }


def _compose_run(
    extractor: str, in_dir: Path, out_dir: Path, *extra: str, timeout: int = 1800
) -> subprocess.CompletedProcess:
    cmd = [*BASE, "--profile", "extract", "run", "--rm", "-T"]
    cmd += [*extra, f"extract-{extractor}"]
    return subprocess.run(
        cmd, env=_env(in_dir, out_dir), capture_output=True, text=True, timeout=timeout
    )


@pytest.fixture(scope="module")
def built():
    """Build each selected image once per module (cache makes reruns cheap)."""
    from tests.extractors.conftest import selected_extractors

    for ext in selected_extractors():
        res = subprocess.run(
            [*BASE, "--profile", "extract", "build", f"extract-{ext}"],
            capture_output=True,
            text=True,
            timeout=3600,
        )
        assert res.returncode == 0, res.stderr[-3000:]


@pytest.fixture(scope="module")
def run_all(built, tmp_path_factory):
    """Run every extractor over all fixtures once; return {extractor: (proc, out_dir)}."""
    from tests.extractors.conftest import selected_extractors

    base = tmp_path_factory.mktemp("extract")
    in_dir = base / "in"
    in_dir.mkdir()
    for name in FIXTURES:
        shutil.copy(PDFS / f"{name}.pdf", in_dir / f"{name}.pdf")
    results = {}
    for ext in selected_extractors():
        out_dir = base / "out"
        (out_dir / ext).mkdir(parents=True)
        results[ext] = (_compose_run(ext, in_dir, out_dir), out_dir / ext)
    return results


def _records(out_dir: Path, name: str) -> list[dict]:
    doc_id = hashlib.sha256((PDFS / f"{name}.pdf").read_bytes()).hexdigest()
    return json.loads((out_dir / f"{doc_id}.json").read_text())


@pytest.mark.parametrize("name", FIXTURES)
def test_output_matches_golden_under_network_none(run_all, extractor, name):
    """DoD 1+2: runs with network_mode none + offline env, output within tolerance of golden."""
    proc, out_dir = run_all[extractor]
    assert proc.returncode == 0, proc.stderr[-3000:]
    actual = _records(out_dir, name)
    golden = json.loads((GOLDEN / extractor / f"{name}.json").read_text())
    assert compare_to_golden(actual, golden) == []
    for rec in actual:
        BronzeRecord.model_validate(rec)


@pytest.mark.parametrize("name", ["mortgage", "deed", "discharge", "multi_instrument"])
def test_key_fields_from_truth_present_in_text(run_all, extractor, name):
    _, out_dir = run_all[extractor]
    text = "\n".join(r["text"] for r in _records(out_dir, name))
    truth = json.loads((TRUTH / f"{name}.json").read_text())
    assert missing_key_fields(text, truth) == []


def test_rejected_inputs_fail_in_prevalidation_not_in_parser(run_all, extractor):
    _, out_dir = run_all[extractor]
    assert _records(out_dir, "corrupt")[0]["error"].startswith("prevalidate:truncated")
    assert _records(out_dir, "oversize")[0]["error"].startswith("prevalidate:too_large")


def test_scanned_fixture_ocr_rule(run_all, extractor):
    """DoD 5: image-only PDFs are either OCRed (extractor with OCR) or flagged needs_ocr."""
    _, out_dir = run_all[extractor]
    rec = _records(out_dir, "scanned_image")[0]
    if rec["status"] == "partial":
        assert rec["error"].startswith("needs_ocr")
    else:
        assert rec["status"] == "ok" and rec["text"].strip()


def _sh(extractor, tmp_path, code: str):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir(exist_ok=True)
    (out_dir / extractor).mkdir(parents=True, exist_ok=True)
    cmd = [*BASE, "--profile", "extract", "run", "--rm", "-T"]
    cmd += ["--entrypoint", "python", f"extract-{extractor}", "-c", code]
    return subprocess.run(
        cmd, env=_env(in_dir, out_dir), capture_output=True, text=True, timeout=600
    )


def test_cannot_write_outside_output_path(built, extractor, tmp_path):
    code = (
        "import sys\n"
        "bad = []\n"
        "for p in ['/etc/x', '/usr/x', '/app/x', '/in/x', '/x', '/root/x', '/proc/x']:\n"
        "    try:\n"
        "        open(p, 'w').close(); bad.append(p)\n"
        "    except OSError:\n"
        "        pass\n"
        "open('/out/ok', 'w').write('x')\n"
        "print('WRITABLE=' + ','.join(bad))\n"
    )
    res = _sh(extractor, tmp_path, code)
    assert res.returncode == 0, res.stderr
    assert "WRITABLE=\n" in res.stdout
    assert (tmp_path / "out" / extractor / "ok").exists()


def test_cannot_resolve_dns_or_connect(built, extractor, tmp_path):
    code = (
        "import socket\n"
        "try:\n"
        "    socket.getaddrinfo('example.com', 443); print('DNS=yes')\n"
        "except OSError:\n"
        "    print('DNS=no')\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 443), timeout=3); print('TCP=yes')\n"
        "except OSError:\n"
        "    print('TCP=no')\n"
        "import os; print('IFACES=' + ','.join(sorted(os.listdir('/sys/class/net'))))\n"
    )
    res = _sh(extractor, tmp_path, code)
    assert res.returncode == 0, res.stderr
    assert "DNS=no" in res.stdout and "TCP=no" in res.stdout
    assert "IFACES=lo\n" in res.stdout


def test_runs_non_root_with_offline_env(built, extractor, tmp_path):
    code = (
        "import os; print('UID=%d' % os.getuid()); print('OFFLINE=' + os.environ['HF_HUB_OFFLINE'])"
    )
    res = _sh(extractor, tmp_path, code)
    assert res.returncode == 0, res.stderr
    assert f"UID={os.getuid()}" in res.stdout and "UID=0" not in res.stdout
    assert "OFFLINE=1" in res.stdout


PAGE_CAP_CODE = (
    "from backend.ingest.extractors.{ext}.extract import extract\n"
    "res = extract(open('/in/multi_instrument.pdf','rb').read(), 2)\n"
    "print('PAGES=%d TRUNCATED=%s' % (len(res.pages), res.truncated))\n"
)


def test_extractor_itself_enforces_page_cap(built, extractor, tmp_path):
    """Bypasses prevalidate: calls the extractor directly on a 3-page PDF with cap 2."""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    shutil.copy(PDFS / "multi_instrument.pdf", in_dir / "multi_instrument.pdf")
    res = _sh(extractor, tmp_path, PAGE_CAP_CODE.format(ext=extractor))
    assert res.returncode == 0, res.stderr[-2000:]
    assert "PAGES=2 TRUNCATED=True" in res.stdout


def test_baked_models_match_sha256_manifest(built, extractor, tmp_path):
    """Models baked into the image are byte-identical to the committed manifest."""
    if extractor == "pymupdf":
        pytest.skip("pymupdf has no model files")
    root = "/models" if extractor == "docling" else "/opt/hf/hub"
    code = (
        "import hashlib, os, sys\n"
        f"base = '{root}'\n"
        "bad = []\n"
        "for line in open('/app/models.sha256').read().splitlines():\n"
        "    digest, rel = line.split('  ', 1)\n"
        "    h = hashlib.sha256(open(os.path.join(base, rel), 'rb').read()).hexdigest()\n"
        "    if h != digest: bad.append(rel)\n"
        "print('MISMATCH=' + ','.join(bad))\n"
    )
    res = _sh(extractor, tmp_path, code)
    assert res.returncode == 0, res.stderr[-2000:]
    assert "MISMATCH=\n" in res.stdout
