"""GET /loans: filters, limit, cursor pagination, totals, GeoJSON shape, validation."""

from __future__ import annotations

import base64
import json

import pyarrow.parquet as pq
import pytest

from backend.common.snapshot import LIST_FILE


def _expected(snap_dir):
    rows = pq.read_table(snap_dir / LIST_FILE).to_pylist()
    rows.sort(key=lambda r: (-r["bridge_score"], r["loan_id"]))
    return rows


@pytest.fixture
def ctx(small_snapshot, make_client):
    snap, _ = small_snapshot
    return make_client(snap), _expected(snap), snap


def test_returns_geojson_feature_collection(ctx):
    client, expected, _ = ctx
    r = client.get("/loans")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/geo+json")
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert body["total"] == len(expected) == 40
    assert body["next_cursor"] is None
    assert body["generated_at"].startswith("2026-01-01")
    first = body["features"][0]
    assert first["type"] == "Feature"
    assert first["properties"]["loan_id"] == expected[0]["loan_id"]
    assert first["properties"]["bridge_score"] == expected[0]["bridge_score"]


def test_feature_properties_exact_and_no_borrower(ctx):
    client, expected, _ = ctx
    body = client.get("/loans").json()
    keys = {
        "loan_id",
        "address_norm",
        "lender",
        "lender_type",
        "principal",
        "recorded_date",
        "maturity_date",
        "term_months",
        "bridge_score",
        "score_band",
        "is_bridge_loan",
        "classifier_version",
        "signals",
        "geocode_status",
    }
    for feat in body["features"]:
        assert set(feat["properties"]) == keys
    text = json.dumps(body).lower()
    assert "borrower" not in text and "grantor" not in text


def test_geometry_point_lon_lat_and_null_for_unmapped(ctx):
    client, expected, _ = ctx
    by_id = {r["loan_id"]: r for r in expected}
    feats = client.get("/loans").json()["features"]
    seen_null = seen_point = False
    for f in feats:
        row = by_id[f["properties"]["loan_id"]]
        if row["geocode_status"] == "no_match":
            assert f["geometry"] is None
            seen_null = True
        else:
            assert f["geometry"] == {"type": "Point", "coordinates": [row["lon"], row["lat"]]}
            seen_point = True
    assert seen_null and seen_point


def test_score_band_values(ctx):
    client, _, _ = ctx
    for f in client.get("/loans").json()["features"]:
        s = f["properties"]["score_band"], f["properties"]["bridge_score"]
        band, score = s
        assert band == ("low" if score < 40 else "mid" if score < 70 else "high")


def test_sorted_by_score_desc_then_loan_id(ctx):
    client, expected, _ = ctx
    ids = [f["properties"]["loan_id"] for f in client.get("/loans").json()["features"]]
    assert ids == [r["loan_id"] for r in expected]


def test_min_score_filter(ctx):
    client, expected, _ = ctx
    body = client.get("/loans", params={"min_score": 70}).json()
    want = [r for r in expected if r["bridge_score"] >= 70]
    assert body["total"] == len(want)
    assert [f["properties"]["loan_id"] for f in body["features"]] == [r["loan_id"] for r in want]


def test_town_filter(ctx):
    client, expected, _ = ctx
    assert client.get("/loans", params={"town": "mattapoisett"}).json()["total"] == 40
    assert client.get("/loans", params={"town": "rye"}).json()["total"] == 0


def test_lender_type_filter(ctx):
    client, expected, _ = ctx
    lt = expected[0]["lender_type"]
    want = [r for r in expected if r["lender_type"] == lt]
    body = client.get("/loans", params={"lender_type": lt}).json()
    assert body["total"] == len(want)


def test_recorded_date_range_filter(ctx):
    client, expected, _ = ctx
    dates = sorted(r["recorded_date"] for r in expected)
    lo, hi = dates[10], dates[25]
    want = [r for r in expected if lo <= r["recorded_date"] <= hi]
    body = client.get(
        "/loans", params={"recorded_from": lo.isoformat(), "recorded_to": hi.isoformat()}
    ).json()
    assert body["total"] == len(want)


def test_bbox_filter_excludes_unmapped_and_outside(ctx):
    client, expected, _ = ctx
    lons = sorted(r["lon"] for r in expected if r["lon"] is not None)
    lo, hi = lons[5], lons[20]
    want = [r for r in expected if r["lon"] is not None and lo <= r["lon"] <= hi]
    bbox = f"{lo},41.0,{hi},42.0"
    body = client.get("/loans", params={"bbox": bbox}).json()
    assert body["total"] == len(want)
    assert all(f["geometry"] is not None for f in body["features"])


def test_unmapped_count_honors_filters(ctx):
    client, expected, _ = ctx
    unmapped = [r for r in expected if r["geocode_status"] == "no_match"]
    assert unmapped, "fixture must contain unmapped rows"
    body = client.get("/loans").json()
    assert body["unmapped_count"] == len(unmapped)
    want = [r for r in unmapped if r["bridge_score"] >= 50]
    body = client.get("/loans", params={"min_score": 50}).json()
    assert body["unmapped_count"] == len(want)
    # bbox is a spatial filter that unmapped rows cannot satisfy: the indicator ignores it
    body = client.get("/loans", params={"bbox": "-70.9,41.6,-70.8,41.7"}).json()
    assert body["unmapped_count"] == len(unmapped)


def test_default_and_explicit_limit(ctx):
    client, expected, _ = ctx
    assert len(client.get("/loans", params={"limit": 7}).json()["features"]) == 7
    assert len(client.get("/loans").json()["features"]) == 40


def test_cursor_pagination_walks_all_rows_without_duplicates(ctx):
    client, expected, _ = ctx
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 9, "min_score": 10}
        if cursor:
            params["cursor"] = cursor
        body = client.get("/loans", params=params).json()
        seen += [f["properties"]["loan_id"] for f in body["features"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    want = [r["loan_id"] for r in expected if r["bridge_score"] >= 10]
    assert seen == want
    assert pages == -(-len(want) // 9)


def test_cursor_with_changed_filters_is_rejected(ctx):
    client, _, _ = ctx
    cursor = client.get("/loans", params={"limit": 5}).json()["next_cursor"]
    r = client.get("/loans", params={"limit": 5, "min_score": 50, "cursor": cursor})
    assert r.status_code == 400
    assert r.json()["code"] == "cursor_filter_mismatch"


def test_stale_cursor_after_new_snapshot_is_410(ctx):
    client, _, snap = ctx
    cursor = client.get("/loans", params={"limit": 5}).json()["next_cursor"]
    manifest = json.loads((snap / "manifest.json").read_text())
    manifest["snapshot_id"] = "ffffffffffffffff"
    (snap / "manifest.json").write_text(json.dumps(manifest))
    client.app.state.store.invalidate()
    r = client.get("/loans", params={"limit": 5, "cursor": cursor})
    assert r.status_code == 410
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "stale_cursor"


@pytest.mark.parametrize("cursor", ["!!!", "bm90LWpzb24", base64.urlsafe_b64encode(b"{}").decode()])
def test_malformed_cursor_is_400(ctx, cursor):
    client, _, _ = ctx
    r = client.get("/loans", params={"cursor": cursor})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_cursor"


@pytest.mark.parametrize(
    "params,field",
    [
        ({"bbox": "1,2,3"}, "bbox"),
        ({"bbox": "a,b,c,d"}, "bbox"),
        ({"bbox": "-70.7,41.7,-70.9,41.6"}, "bbox"),  # inverted lon
        ({"bbox": "-70.9,41.7,-70.7,41.6"}, "bbox"),  # inverted lat
        ({"bbox": "-181,0,0,1"}, "bbox"),
        ({"bbox": "0,-91,1,0"}, "bbox"),
        ({"bbox": "0,0,1,91"}, "bbox"),
        ({"bbox": "nan,0,1,1"}, "bbox"),
        ({"town": "atlantis"}, "town"),
        ({"lender_type": "shark"}, "lender_type"),
        ({"min_score": -1}, "min_score"),
        ({"min_score": 101}, "min_score"),
        ({"limit": 2001}, "limit"),
        ({"limit": 0}, "limit"),
        ({"recorded_from": "yesterday"}, "recorded_from"),
        ({"recorded_to": "2024-13-45"}, "recorded_to"),
    ],
)
def test_invalid_params_are_400_problem_with_field_errors(ctx, params, field):
    client, _, _ = ctx
    r = client.get("/loans", params=params)
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "invalid_parameters"
    assert body["status"] == 400
    assert any(e["field"] == field for e in body["errors"])


def test_inverted_date_range_is_400(ctx):
    client, _, _ = ctx
    r = client.get("/loans", params={"recorded_from": "2024-02-01", "recorded_to": "2024-01-01"})
    assert r.status_code == 400
    assert r.json()["code"] == "invalid_parameters"


def test_sql_injection_attempts_are_inert_or_rejected(ctx):
    client, _, _ = ctx
    r = client.get("/loans", params={"town": "x'; DROP TABLE loans;--"})
    assert r.status_code == 400
    assert client.get("/loans").json()["total"] == 40


def test_cursor_with_hostile_values_is_inert(ctx):
    client, _, _ = ctx
    good = client.get("/loans", params={"limit": 5}).json()["next_cursor"]
    payload = json.loads(base64.urlsafe_b64decode(good + "=" * (-len(good) % 4)))
    payload["k"] = [50, "x' OR '1'='1"]
    forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    r = client.get("/loans", params={"limit": 5, "cursor": forged})
    assert r.status_code == 200
    assert r.json()["total"] == 40


def test_5k_snapshot_limit_2000_stays_under_5mb(big_snapshot_master, make_client):
    snap, rows = big_snapshot_master
    client = make_client(snap)
    r = client.get("/loans", params={"limit": 2000})
    assert r.status_code == 200
    assert len(r.content) < 5_000_000
    body = r.json()
    assert len(body["features"]) == 2000
    assert body["total"] == 5000
    assert body["next_cursor"] is not None


def test_response_cap_enforced_with_413(small_snapshot, make_client):
    snap, _ = small_snapshot
    client = make_client(snap, response_cap_bytes=1000)
    r = client.get("/loans")
    assert r.status_code == 413
    assert r.json()["code"] == "response_too_large"
