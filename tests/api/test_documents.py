"""Source-PDF route: authenticated streaming with safe headers; SigV4 signer."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from backend.api.config import RawStoreConfig
from backend.api.documents import (
    ObjectNotFound,
    ObjectStream,
    S3ObjectFetcher,
    sigv4_headers,
)
from backend.common.snapshot import DETAIL_FILE

PDF = b"%PDF-1.7\n" + b"x" * 5000


class FakeFetcher:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects
        self.opened: list[str] = []
        self.closed = 0

    def open(self, key: str) -> ObjectStream:
        self.opened.append(key)
        if key not in self.objects:
            raise ObjectNotFound(key)
        data = self.objects[key]

        def gen():
            for i in range(0, len(data), 1024):
                yield data[i : i + 1024]

        stream = ObjectStream(chunks=gen(), content_length=len(data), close=self._close)
        return stream

    def _close(self):
        self.closed += 1


@pytest.fixture
def ctx(small_snapshot, make_client):
    snap, _ = small_snapshot
    detail = pq.read_table(snap / DETAIL_FILE).to_pylist()
    first = detail[0]
    fetcher = FakeFetcher({f"{first['doc_id']}.pdf": PDF})
    client = make_client(snap, app_kwargs={"fetcher": fetcher})
    return client, fetcher, first


def test_streams_pdf_with_safe_headers(ctx):
    client, fetcher, first = ctx
    r = client.get(f"/loans/{first['loan_id']}/source")
    assert r.status_code == 200
    assert r.content == PDF
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == f'attachment; filename="{first["doc_id"]}.pdf"'
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in r.headers["cache-control"]
    assert r.headers["content-length"] == str(len(PDF))
    assert fetcher.opened == [f"{first['doc_id']}.pdf"]
    assert fetcher.closed == 1


def test_content_type_never_comes_from_the_object(ctx, small_snapshot):
    client, fetcher, first = ctx
    r = client.get(f"/loans/{first['loan_id']}/source")
    assert r.headers["content-type"] == "application/pdf"


def test_non_pdf_object_is_rejected(small_snapshot, make_client):
    snap, _ = small_snapshot
    first = pq.read_table(snap / DETAIL_FILE).to_pylist()[0]
    fetcher = FakeFetcher({f"{first['doc_id']}.pdf": b"<html><script>alert(1)</script>"})
    client = make_client(snap, app_kwargs={"fetcher": fetcher})
    r = client.get(f"/loans/{first['loan_id']}/source")
    assert r.status_code == 502
    assert r.json()["code"] == "invalid_document"
    assert fetcher.closed == 1


def test_missing_object_is_404(small_snapshot, make_client):
    snap, _ = small_snapshot
    first = pq.read_table(snap / DETAIL_FILE).to_pylist()[0]
    client = make_client(snap, app_kwargs={"fetcher": FakeFetcher({})})
    r = client.get(f"/loans/{first['loan_id']}/source")
    assert r.status_code == 404
    assert r.json()["code"] == "document_not_found"


def test_unknown_loan_is_404(ctx):
    client, fetcher, _ = ctx
    r = client.get("/loans/unknown/source")
    assert r.status_code == 404
    assert r.json()["code"] == "loan_not_found"
    assert fetcher.opened == []


def test_no_raw_store_configured_is_503(small_snapshot, make_client):
    snap, _ = small_snapshot
    first = pq.read_table(snap / DETAIL_FILE).to_pylist()[0]
    client = make_client(snap)
    r = client.get(f"/loans/{first['loan_id']}/source")
    assert r.status_code == 503
    assert r.json()["code"] == "documents_unavailable"


def test_source_access_is_audited(ctx):
    client, _, first = ctx
    client.get(f"/loans/{first['loan_id']}/source")
    lines = Path(client.app.state.settings.audit_log_path).read_text().splitlines()
    entry = json.loads(lines[-1])
    assert entry["action"] == "source" and entry["loan_id"] == first["loan_id"]
    assert set(entry) == {"ts", "key_id", "loan_id", "action"}


def test_unsafe_doc_id_never_reaches_the_object_store(small_snapshot, make_client, monkeypatch):
    snap, _ = small_snapshot
    first = pq.read_table(snap / DETAIL_FILE).to_pylist()[0]
    fetcher = FakeFetcher({})
    client = make_client(snap, app_kwargs={"fetcher": fetcher})
    from backend.api import queries

    real = queries.get_detail

    def evil(state, loan_id):
        d = real(state, loan_id)
        return d.model_copy(
            update={"source": d.source.model_copy(update={"doc_id": "../../etc/passwd"})}
        )

    monkeypatch.setattr("backend.api.main.queries.get_detail", evil)
    r = client.get(f"/loans/{first['loan_id']}/source")
    assert r.status_code == 404
    assert fetcher.opened == []


# --- SigV4 signer ---------------------------------------------------------------------------


def test_sigv4_matches_aws_documented_example():
    """AWS S3 SigV4 'GET Object' example (Range header), documented signature."""
    headers = sigv4_headers(
        method="GET",
        host="examplebucket.s3.amazonaws.com",
        path="/test.txt",
        region="us-east-1",
        key_id="AKIAIOSFODNN7EXAMPLE",
        secret="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        now=datetime(2013, 5, 24, 0, 0, 0, tzinfo=UTC),
        extra_headers={"range": "bytes=0-9"},
    )
    assert headers["Authorization"].endswith(
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in headers["Authorization"]


def test_sigv4_session_token_is_signed():
    headers = sigv4_headers(
        method="GET",
        host="h:9000",
        path="/b/k",
        region="us-east-1",
        key_id="k",
        secret="s",
        now=datetime(2026, 1, 1, tzinfo=UTC),
        session_token="tok",
    )
    assert headers["x-amz-security-token"] == "tok"
    assert "x-amz-security-token" in headers["Authorization"]


def test_fetcher_rejects_bad_keys():
    f = S3ObjectFetcher(
        RawStoreConfig(endpoint="http://127.0.0.1:1", bucket="raw", key_id="k", secret="s")
    )
    for key in ("../x", "/abs", "a//b", "a\\b", ""):
        with pytest.raises(ValueError):
            f.open(key)


def test_raw_store_config_from_env():
    from backend.api.config import Settings

    s = Settings.from_env(
        {
            "GATEWAY_SECRET": "g" * 32,
            "SNAPSHOT_BASE": "/tmp/s",
            "RAW_S3_ENDPOINT": "http://minio:9000",
            "RAW_S3_BUCKET": "raw",
            "RAW_S3_KEY_ID": "k",
            "RAW_S3_SECRET": "s",
        }
    )
    assert s.raw_store.endpoint == "http://minio:9000"
    assert s.raw_store.bucket == "raw"
