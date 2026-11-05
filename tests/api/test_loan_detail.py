"""GET /loans/{id}, /coverage, /health/*; audit entries."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from backend.common.snapshot import DETAIL_FILE, LIST_FILE


def _audit(client) -> list[dict]:
    path = Path(client.app.state.settings.audit_log_path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.fixture
def ctx(small_snapshot, make_client):
    snap, _ = small_snapshot
    detail = {r["loan_id"]: r for r in pq.read_table(snap / DETAIL_FILE).to_pylist()}
    lst = {r["loan_id"]: r for r in pq.read_table(snap / LIST_FILE).to_pylist()}
    return make_client(snap), detail, lst


def test_detail_returns_borrower_fields_signals_and_source(ctx):
    client, detail, lst = ctx
    loan_id = next(k for k, v in lst.items() if v["signals_unevaluable"])
    r = client.get(f"/loans/{loan_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["loan_id"] == loan_id
    assert body["borrower"] == detail[loan_id]["borrower"]
    for key in ("is_bridge_loan", "classifier_version", "bridge_score", "score_band"):
        assert key in body
    assert body["signals"] == lst[loan_id]["signals"]
    assert body["signals_unevaluable"] == lst[loan_id]["signals_unevaluable"]
    assert body["fields"]["principal"] == {
        "value": "100000.00",
        "source_extractor": "docling",
        "confidence": 0.9,
        "disagreement": False,
    }
    assert body["fields"]["recorded_date"]["disagreement"] is True
    assert body["source"] == {
        "doc_id": detail[loan_id]["doc_id"],
        "recorded_book": detail[loan_id]["recorded_book"],
        "recorded_page": detail[loan_id]["recorded_page"],
        "source_url": detail[loan_id]["source_url"],
        "document_path": f"/loans/{loan_id}/source",
    }


def test_detail_without_fields_column_returns_empty_fields(snapshot_dir, make_client):
    client = make_client(snapshot_dir)
    loan_id = pq.read_table(snapshot_dir / LIST_FILE).to_pylist()[0]["loan_id"]
    r = client.get(f"/loans/{loan_id}")
    assert r.status_code == 200
    assert r.json()["fields"] == {}


def test_detail_unknown_id_is_404_problem(ctx):
    client, _, _ = ctx
    r = client.get("/loans/does-not-exist")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "loan_not_found"
    assert _audit(client) == []


@pytest.mark.parametrize("bad", ["a b", "x'--", "a" * 200, "%27OR1=1"])
def test_detail_malformed_id_is_400(ctx, bad):
    client, _, _ = ctx
    r = client.get(f"/loans/{bad}")
    assert r.status_code in (400, 404)
    if r.status_code == 400:
        assert r.json()["code"] == "invalid_parameters"


def test_detail_writes_audit_entry_with_key_id_and_loan_id_only(ctx):
    client, detail, lst = ctx
    loan_id = next(iter(lst))
    client.get(f"/loans/{loan_id}")
    entries = _audit(client)
    assert len(entries) == 1
    e = entries[0]
    assert set(e) == {"ts", "key_id", "loan_id", "action"}
    assert e["key_id"] == "key-alpha" and e["loan_id"] == loan_id and e["action"] == "detail"
    raw = Path(client.app.state.settings.audit_log_path).read_text()
    assert detail[loan_id]["borrower"] not in raw


def test_detail_fails_closed_when_audit_cannot_write(ctx):
    client, _, lst = ctx
    client.app.state.audit.record = _boom
    r = client.get(f"/loans/{next(iter(lst))}")
    assert r.status_code == 503
    assert r.json()["code"] == "audit_unavailable"
    assert "borrower" not in r.text


def _boom(*_a, **_k):
    raise OSError("disk full")


def test_list_does_not_write_audit(ctx):
    client, _, _ = ctx
    client.get("/loans")
    assert _audit(client) == []


def test_coverage_from_manifest(ctx):
    client, _, _ = ctx
    r = client.get("/coverage")
    assert r.status_code == 200
    assert r.json() == {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "towns": [
            {
                "town": "mattapoisett",
                "documents_fetched": 40,
                "gaps": {"paid": 2, "blocked": 1, "failed": 0},
                "last_ingested_at": "2026-01-01T00:00:00+00:00",
            }
        ],
    }


def test_coverage_fallback_when_manifest_has_no_coverage(snapshot_dir, make_client):
    client = make_client(snapshot_dir)
    body = client.get("/coverage").json()
    (town,) = body["towns"]
    assert town["town"] == "mattapoisett"
    assert town["documents_fetched"] == 1
    assert town["gaps"] is None  # unknown, not zero
    assert town["last_ingested_at"].startswith("2026-01-01")


def test_health_live_and_ready(ctx):
    client, _, _ = ctx
    assert client.get("/health/live").json() == {"status": "ok"}
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_endpoints_need_no_gateway_secret(small_snapshot, make_client):
    snap, _ = small_snapshot
    client = make_client(snap, auth=False)
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
