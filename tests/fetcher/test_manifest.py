"""Mapping fetch outcomes to manifest rows and the drop-dir hand-off."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from backend.common.models import Manifest
from backend.fetcher.adapter import FetchResult, Outcome, RetryPolicy
from backend.fetcher.manifest import build_row, fetch_to_drop, gap_reason_for

URL = "https://portal.example.test/a.pdf"
NOW = datetime(2026, 11, 5, tzinfo=UTC)
PDF = b"%PDF-1.4\nsynthetic\n%%EOF"


@pytest.mark.parametrize(
    ("outcome", "status", "reason"),
    [
        (Outcome.CAPTCHA, 200, "blocked"),
        (Outcome.RATE_LIMITED, 429, "blocked"),
        (Outcome.BLOCKED_URL, None, "blocked"),
        (Outcome.HTTP_ERROR, 403, "blocked"),
        (Outcome.HTTP_ERROR, 401, "blocked"),
        (Outcome.PAID, 402, "paid"),
        (Outcome.TIMEOUT, None, "failed"),
        (Outcome.HTTP_ERROR, 500, "failed"),
        (Outcome.HTTP_ERROR, 404, "failed"),
        (Outcome.NOT_PDF, 200, "failed"),
    ],
)
def test_gap_reason_mapping(outcome, status, reason):
    assert gap_reason_for(FetchResult(url=URL, outcome=outcome, status=status)) == reason


def test_ok_result_has_no_gap_reason():
    with pytest.raises(ValueError):
        gap_reason_for(FetchResult(url=URL, outcome=Outcome.OK, body=PDF))


def test_build_row_for_fetched_pdf_uses_content_sha256():
    row, body = build_row(
        FetchResult(url=URL, outcome=Outcome.OK, body=PDF), town="mattapoisett", fetched_at=NOW
    )
    assert body == PDF
    assert row.doc_id == hashlib.sha256(PDF).hexdigest()
    assert (row.status, row.gap_reason, row.size_bytes) == ("fetched", None, len(PDF))
    assert row.source_url == URL and row.town == "mattapoisett"


def test_build_row_for_gap_uses_url_hash_and_zero_size():
    row, body = build_row(
        FetchResult(url=URL, outcome=Outcome.CAPTCHA, status=200),
        town="mattapoisett",
        fetched_at=NOW,
    )
    assert body is None
    assert row.doc_id == hashlib.sha256(URL.encode()).hexdigest()
    assert (row.status, row.gap_reason, row.size_bytes) == ("gap", "blocked", 0)


class Scripted:
    def __init__(self, by_url):
        self.by_url = by_url

    def fetch(self, url):
        return self.by_url[url]


def test_fetch_to_drop_writes_pdf_and_row_json_and_gap_rows(tmp_path):
    ok_url, cap_url, slow_url = (f"https://portal.example.test/{n}.pdf" for n in "abc")
    fetcher = Scripted(
        {
            ok_url: FetchResult(url=ok_url, outcome=Outcome.OK, body=PDF),
            cap_url: FetchResult(url=cap_url, outcome=Outcome.CAPTCHA, status=200),
            slow_url: FetchResult(url=slow_url, outcome=Outcome.TIMEOUT),
        }
    )
    rows = fetch_to_drop(
        fetcher,
        [ok_url, cap_url, slow_url],
        town="mattapoisett",
        drop_dir=tmp_path,
        policy=RetryPolicy(max_attempts=1),
        sleep=lambda s: None,
        now=lambda: NOW,
    )
    assert [r.status for r in rows] == ["fetched", "gap", "gap"]
    assert [r.gap_reason for r in rows] == [None, "blocked", "failed"]
    sha = hashlib.sha256(PDF).hexdigest()
    assert (tmp_path / f"{sha}.pdf").read_bytes() == PDF
    on_disk = Manifest.model_validate(json.loads((tmp_path / f"{sha}.json").read_text()))
    assert on_disk == rows[0]
    names = sorted(p.name for p in tmp_path.iterdir())
    assert len(names) == 4  # one pdf + three row jsons
    assert not [n for n in names if n.endswith(".tmp")]


def test_fetch_to_drop_survives_an_unexpected_fetcher_exception(tmp_path):
    class Boom:
        def fetch(self, url):
            raise RuntimeError("browser crashed")

    rows = fetch_to_drop(
        Boom(),
        [URL],
        town="mattapoisett",
        drop_dir=tmp_path,
        policy=RetryPolicy(max_attempts=1),
        sleep=lambda s: None,
        now=lambda: NOW,
    )
    assert (rows[0].status, rows[0].gap_reason) == ("gap", "failed")


def test_fetch_to_drop_creates_missing_drop_dir(tmp_path):
    target = tmp_path / "nested" / "drop"
    fetch_to_drop(
        Scripted({URL: FetchResult(url=URL, outcome=Outcome.PAID, status=402)}),
        [URL],
        town="t",
        drop_dir=target,
        policy=RetryPolicy(max_attempts=1),
        sleep=lambda s: None,
        now=lambda: NOW,
    )
    assert len(list(target.glob("*.json"))) == 1
