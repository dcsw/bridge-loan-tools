"""WU6 DoD 6-7: publish materializes from semantic_loans_v1, versioned key, atomic manifest flip,
output validates and loads in the WU7 API (in-process, local-file snapshot, no gateway)."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from silver_helpers import EXTRACTORS, NOW, golden_records, meta_for, record

from backend.api.config import Settings
from backend.api.queries import get_coverage, get_detail, list_loans
from backend.api.schemas import LoanFilters
from backend.api.snapshot import SnapshotStore
from backend.common.models import Manifest
from backend.common.snapshot import validate_snapshot
from backend.ingest import publish as pub
from backend.ingest import silver_job as sj
from backend.ingest import views
from backend.ingest.geocode import GeocodeResult
from backend.ingest.unify import DocMeta


class Geo:
    def geocode(self, address):
        return GeocodeResult("matched", 41.65, -70.81)


def build_source(extra_records=(), extra_meta=None):
    recs = []
    for n in ("mortgage", "deed", "discharge", "multi_instrument"):
        recs += golden_records(n)
    recs += list(extra_records)
    meta = meta_for(recs)
    meta.update(extra_meta or {})
    store = sj.InMemoryStore()
    sj.run_silver(recs, meta, store, Geo())
    return pub.ArrowSilverSource(
        views.table_from_rows(store.read_loans(), views.LOAN_COLUMNS),
        views.table_from_rows(store.read_instruments(), views.INSTRUMENT_COLUMNS),
    )


RYE_TEXT = (
    "MORTGAGE\nGrantor: Rye Borrower\nGrantee: Harbor Credit Union\n"
    "Property: 7 Shore Rd, Rye, NH\nParcel: RYE-1-1\nRecorded: May 5, 2024  Book 9 Page 9\n"
    "Principal: $50,000.00\nTerm: 6 months\n"
)
RYE_DOC = "9" * 64


def two_town_source():
    recs = [record(e, RYE_TEXT, doc_id=RYE_DOC) for e in EXTRACTORS]
    return build_source(recs, {RYE_DOC: DocMeta("rye", None)})


def manifest_row(doc_id, town="mattapoisett", status="fetched", gap=None):
    return Manifest(
        doc_id=doc_id, source_url="https://p.example.test/x", fetched_at=NOW, town=town,
        status=status, gap_reason=gap, size_bytes=0 if gap else 5,
    )  # fmt: skip


def api_store(base: Path) -> SnapshotStore:
    settings = Settings(
        gateway_secret="s" * 32, snapshot_base=str(base), audit_log_path=str(base / "audit.log")
    )
    return SnapshotStore(settings)


# -- stores ------------------------------------------------------------------------------------


def test_local_store_put_get_is_atomic_and_creates_parents(tmp_path):
    s = pub.LocalObjectStore(tmp_path)
    s.put("a/b/c.bin", b"one")
    s.put("a/b/c.bin", b"two")
    assert s.get("a/b/c.bin") == b"two" and s.get("nope") is None
    assert [p.name for p in (tmp_path / "a" / "b").iterdir()] == ["c.bin"]  # no tmp leftovers


def test_local_store_rejects_escaping_keys(tmp_path):
    for key in ("../x", "/abs", "a/../../x"):
        with pytest.raises(ValueError, match="unsafe"):
            pub.LocalObjectStore(tmp_path).put(key, b"x")


class FakeS3:
    def __init__(self):
        self.puts, self.objects = [], {}

    def put_object(self, **kw):
        self.puts.append(kw["Key"])
        self.objects[kw["Key"]] = kw["Body"]

    def get_object(self, **kw):
        if kw["Key"] not in self.objects:
            raise self.exceptions.NoSuchKey()
        return {"Body": io.BytesIO(self.objects[kw["Key"]])}

    class exceptions:
        class NoSuchKey(Exception):
            pass


def test_s3_store_puts_data_before_manifest_and_honors_prefix():
    client = FakeS3()
    store = pub.S3ObjectStore(client, "snapshots", prefix="phase1")
    result = pub.publish(build_source(), store, now=NOW)
    assert client.puts[-1] == "phase1/manifest.json"
    assert all(k.startswith("phase1/versions/") for k in client.puts[:-1])
    assert store.get("manifest.json") is not None and store.get("missing") is None
    assert json.loads(client.objects["phase1/manifest.json"])["snapshot_id"] == result.snapshot_id


# -- publish -----------------------------------------------------------------------------------


def test_publish_writes_versioned_key_and_validates(tmp_path):
    res = pub.publish(build_source(), pub.LocalObjectStore(tmp_path), now=NOW)
    version_dir = tmp_path / "versions" / res.snapshot_id
    assert (version_dir / "loans_list.parquet").exists()
    assert (version_dir / "loans_detail.parquet").exists()
    assert validate_snapshot(version_dir) == res.row_count == 2
    root = json.loads((tmp_path / "manifest.json").read_text())
    assert root["snapshot_id"] == res.snapshot_id
    assert root["files"]["list"]["path"] == f"versions/{res.snapshot_id}/loans_list.parquet"
    assert root["schema_version"] == 1 and root["generated_at"] == "2026-11-05T12:00:00Z"
    assert root["classifier_version"] == "placeholder-all-true"
    assert root["town"] == "mattapoisett"


def test_list_is_pii_free_and_sorted_by_town_and_detail_has_borrower_and_fields(tmp_path):
    res = pub.publish(two_town_source(), pub.LocalObjectStore(tmp_path), now=NOW)
    vdir = tmp_path / "versions" / res.snapshot_id
    listing = pq.read_table(vdir / "loans_list.parquet")
    towns = listing.column("town").to_pylist()
    assert towns == sorted(towns) and set(towns) == {"mattapoisett", "rye"}
    assert not {"borrower", "grantor", "fields_json", "source_text"} & set(listing.column_names)
    text = (vdir / "loans_list.parquet").read_bytes()
    assert b"Diane Webb" not in text and b"Rye Borrower" not in text
    detail = pq.read_table(vdir / "loans_detail.parquet")
    assert "borrower" in detail.column_names and "fields_json" in detail.column_names
    assert "Rye Borrower" in detail.column("borrower").to_pylist()
    assert json.loads(detail.column("fields_json")[0].as_py())["grantor"]["confidence"] == 0.9
    assert json.loads(root_manifest(tmp_path))["town"] == "mattapoisett,rye"


def root_manifest(base):
    return (base / "manifest.json").read_text()


def test_publish_materializes_from_the_semantic_view(tmp_path, monkeypatch):
    """Redefining the view changes what is published: publish reads from it, nothing else."""
    original = views.view_sql()
    monkeypatch.setattr(
        views, "view_sql", lambda: original.replace("l.bridge_score,", "42 AS bridge_score,", 1)
    )
    res = pub.publish(build_source(), pub.LocalObjectStore(tmp_path), now=NOW)
    listing = pq.read_table(tmp_path / "versions" / res.snapshot_id / "loans_list.parquet")
    assert set(listing.column("bridge_score").to_pylist()) == {42}


def test_publish_with_no_loans_is_refused(tmp_path):
    empty = pub.ArrowSilverSource(
        views.loans_table([]), views.table_from_rows([], views.INSTRUMENT_COLUMNS)
    )
    with pytest.raises(ValueError, match="no loans"):
        pub.publish(empty, pub.LocalObjectStore(tmp_path), now=NOW)
    assert not (tmp_path / "manifest.json").exists()


class CrashingStore(pub.LocalObjectStore):
    def __init__(self, root, fail_on):
        super().__init__(root)
        self.fail_on = fail_on

    def put(self, key, data):
        if self.fail_on(key):
            raise RuntimeError("simulated crash")
        super().put(key, data)


def test_crash_before_manifest_flip_leaves_previous_snapshot_valid_and_loadable(tmp_path):
    first = pub.publish(build_source(), pub.LocalObjectStore(tmp_path), now=NOW)
    before = root_manifest(tmp_path)
    later = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    crashing = CrashingStore(tmp_path, lambda key: key == "manifest.json")
    with pytest.raises(RuntimeError, match="simulated crash"):
        pub.publish(two_town_source(), crashing, now=later)
    assert root_manifest(tmp_path) == before  # pointer untouched
    versions = sorted(p.name for p in (tmp_path / "versions").iterdir())
    assert len(versions) == 2 and first.snapshot_id in versions  # new data written, unreferenced
    state = api_store(tmp_path).current()
    assert state.snapshot_id == first.snapshot_id and state.manifest.row_count == 2


def test_crash_while_writing_data_leaves_previous_manifest_untouched(tmp_path):
    pub.publish(build_source(), pub.LocalObjectStore(tmp_path), now=NOW)
    before = root_manifest(tmp_path)
    crashing = CrashingStore(tmp_path, lambda key: key.endswith("loans_detail.parquet"))
    with pytest.raises(RuntimeError):
        pub.publish(two_town_source(), crashing, now=datetime(2026, 11, 6, tzinfo=UTC))
    assert root_manifest(tmp_path) == before
    assert api_store(tmp_path).current().manifest.row_count == 2


def test_manifest_is_written_last(tmp_path):
    order = []

    class Recording(pub.LocalObjectStore):
        def put(self, key, data):
            order.append(key)
            super().put(key, data)

    pub.publish(build_source(), Recording(tmp_path), now=NOW)
    assert order[-1] == "manifest.json" and "manifest.json" not in order[:-1]
    assert len(order) == 4  # list, detail, per-version manifest, root pointer


# -- coverage ----------------------------------------------------------------------------------


def test_coverage_counts_fetched_and_gaps_per_town_from_manifest_rows(tmp_path):
    rows = [
        manifest_row("a" * 64),
        manifest_row("b" * 64),
        manifest_row("c" * 64, status="gap", gap="paid"),
        manifest_row("d" * 64, status="gap", gap="blocked"),
        manifest_row("e" * 64, status="gap", gap="blocked"),
        manifest_row("f" * 64, town="rye"),
        manifest_row("0" * 64, town="madison", status="gap", gap="failed"),
    ]
    pub.publish(two_town_source(), pub.LocalObjectStore(tmp_path), manifests=rows, now=NOW)
    cov = json.loads(root_manifest(tmp_path))["coverage"]
    assert cov["mattapoisett"]["documents_fetched"] == 2
    assert cov["mattapoisett"]["gaps"] == {"paid": 1, "blocked": 2, "failed": 0}
    assert cov["rye"] == {
        "documents_fetched": 1,
        "gaps": {"paid": 0, "blocked": 0, "failed": 0},
        "last_ingested_at": "1970-01-01T00:00:00Z",
    }
    assert cov["madison"]["documents_fetched"] == 0 and cov["madison"]["last_ingested_at"] is None
    assert cov["madison"]["gaps"]["failed"] == 1


def test_no_manifest_rows_means_no_coverage_key(tmp_path):
    pub.publish(build_source(), pub.LocalObjectStore(tmp_path), manifests=None, now=NOW)
    assert "coverage" not in json.loads(root_manifest(tmp_path))
    pub.publish(build_source(), pub.LocalObjectStore(tmp_path), manifests=[], now=NOW)
    assert "coverage" not in json.loads(root_manifest(tmp_path))


def test_s3_manifest_reader_pages_and_skips_bad_rows():
    good = manifest_row("a" * 64).model_dump_json()
    objects = {"manifest/a.json": good, "manifest/bad.json": "{nope", "other/x.json": good}

    class Paginator:
        def paginate(self, **kw):
            assert kw == {"Bucket": "raw", "Prefix": "manifest/"}
            yield {"Contents": [{"Key": k} for k in objects if k.startswith("manifest/")]}
            yield {}

    class Client:
        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return Paginator()

        def get_object(self, Bucket, Key):
            return {"Body": io.BytesIO(objects[Key].encode())}

    rows = pub.read_manifests_s3(Client(), "raw")
    assert [r.doc_id for r in rows] == ["a" * 64]


# -- the WU7 API loads the published output ----------------------------------------------------


def test_api_loads_published_snapshot_with_fields_and_coverage(tmp_path):
    rows = [manifest_row("a" * 64), manifest_row("b" * 64, status="gap", gap="paid")]
    res = pub.publish(build_source(), pub.LocalObjectStore(tmp_path), manifests=rows, now=NOW)
    state = api_store(tmp_path).current()
    assert state.snapshot_id == res.snapshot_id

    collection = list_loans(state, LoanFilters(), 100, None)
    assert collection.total == 2 and collection.unmapped_count == 0
    feature = collection.features[0]
    assert feature.geometry is not None
    assert feature.properties.is_bridge_loan and feature.properties.bridge_score == 100
    assert feature.properties.classifier_version == "placeholder-all-true"
    assert "borrower" not in feature.properties.model_dump()

    loan_id = next(f.id for f in collection.features if f.id.endswith("-1"))
    detail = get_detail(state, loan_id)
    assert detail is not None
    assert detail.borrower == "Diane Webb"
    assert detail.fields["principal"].value == "1060000.00"
    assert detail.fields["grantee"].source_extractor == "unstructured"
    assert detail.fields["grantor"].confidence == 0.9 and not detail.fields["grantor"].disagreement
    assert detail.source.doc_id == loan_id.rsplit("-", 1)[0]
    assert detail.source.source_url.endswith(".pdf")
    assert detail.last_ingested_at is not None
    assert "short_term" in detail.signals

    coverage = get_coverage(state)
    (town,) = coverage.towns
    assert town.town == "mattapoisett" and town.documents_fetched == 1
    assert town.gaps is not None and (town.gaps.paid, town.gaps.blocked) == (1, 0)
    assert town.last_ingested_at == "1970-01-01T00:00:00Z"


def test_api_picks_up_a_second_publish_after_manifest_reload(tmp_path):
    pub.publish(build_source(), pub.LocalObjectStore(tmp_path), now=NOW)
    api = api_store(tmp_path)
    first = api.current().snapshot_id
    second = pub.publish(
        two_town_source(), pub.LocalObjectStore(tmp_path), now=datetime(2026, 11, 7, tzinfo=UTC)
    )
    api.invalidate()
    assert api.current().snapshot_id == second.snapshot_id != first
    assert api.current().manifest.row_count == 3


# -- CLI wiring --------------------------------------------------------------------------------


def test_main_publishes_to_a_local_target(tmp_path, monkeypatch):
    monkeypatch.setattr(pub, "IcebergSilverSource", lambda *a, **k: build_source())
    mdir = tmp_path / "manifests"
    mdir.mkdir()
    (mdir / "a.json").write_text(manifest_row("a" * 64).model_dump_json())
    out = tmp_path / "out"
    rc = pub.main(["--local-dir", str(out), "--manifests-dir", str(mdir)])
    assert rc == 0
    assert (
        json.loads((out / "manifest.json").read_text())["coverage"]["mattapoisett"][
            "documents_fetched"
        ]
        == 1
    )


def test_main_requires_exactly_one_target(capsys):
    with pytest.raises(SystemExit):
        pub.main([])
    with pytest.raises(SystemExit):
        pub.main(["--local-dir", "x", "--bucket", "snapshots"])


def test_main_s3_target_uses_prefix_and_raw_bucket_coverage(monkeypatch):
    import boto3

    client = FakeS3()
    monkeypatch.setattr(boto3, "client", lambda *a, **k: client)
    monkeypatch.setattr(pub, "IcebergSilverSource", lambda *a, **k: build_source())
    monkeypatch.setattr(
        pub, "read_manifests_s3", lambda c, bucket: [manifest_row("a" * 64)] if c is client else []
    )
    assert pub.main(["--bucket", "snapshots", "--prefix", "current"]) == 0
    assert client.puts[-1] == "current/manifest.json"
    assert "coverage" in json.loads(client.objects["current/manifest.json"])


def test_export_manifests_writes_one_json_per_doc(tmp_path, monkeypatch):
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: object())
    rows = [manifest_row("a" * 64), manifest_row("b" * 64, status="gap", gap="paid")]
    monkeypatch.setattr(pub, "read_manifests_s3", lambda c, bucket: rows)
    dest = tmp_path / "m"
    assert pub.main(["--export-manifests", str(dest)]) == 0
    assert sorted(p.name for p in dest.iterdir()) == [f"{'a' * 64}.json", f"{'b' * 64}.json"]
    assert {r.doc_id for r in sj.read_manifest_dir(dest)} == {"a" * 64, "b" * 64}


def test_coverage_normalizes_manifest_town_case_to_one_entry(tmp_path):
    rows = [
        manifest_row("a" * 64, town="Rye"),
        manifest_row("b" * 64, town=" RYE ", status="gap", gap="paid"),
        manifest_row("c" * 64, town="Mattapoisett"),
    ]
    pub.publish(two_town_source(), pub.LocalObjectStore(tmp_path), manifests=rows, now=NOW)
    cov = json.loads(root_manifest(tmp_path))["coverage"]
    assert sorted(cov) == ["mattapoisett", "rye"]
    assert cov["rye"]["documents_fetched"] == 1
    assert cov["rye"]["gaps"]["paid"] == 1
    assert cov["rye"]["last_ingested_at"] == "1970-01-01T00:00:00Z"
