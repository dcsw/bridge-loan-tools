"""WU6 DoD 4: geocoder against RECORDED Census responses (no network)."""

from __future__ import annotations

import json
import urllib.error

import pytest
from silver_helpers import census

from backend.ingest import geocode as g

ADDRESS = "19296 Troy Shoals, Mattapoisett, MA"


def _recorded(name, address=ADDRESS):
    return g.RecordedTransport({address: census(name)["response"]})


@pytest.mark.parametrize("name", ["matched", "low_match", "no_match"])
def test_recorded_responses_classify_as_the_fixture_expects(name):
    result = g.CensusGeocoder(_recorded(name)).geocode(ADDRESS)
    assert result.status == census(name)["expected_geocode_status"]


def test_matched_returns_lon_lat_from_coordinates():
    result = g.CensusGeocoder(_recorded("matched")).geocode(ADDRESS)
    assert (result.status, result.lat, result.lon) == ("matched", 41.6532, -70.8161)


def test_low_match_keeps_coordinates():
    result = g.CensusGeocoder(_recorded("low_match")).geocode(ADDRESS)
    assert (result.status, result.lat, result.lon) == ("low_match", 41.6601, -70.8302)


def test_no_match_has_null_geometry():
    result = g.CensusGeocoder(_recorded("no_match")).geocode(ADDRESS)
    assert (result.status, result.lat, result.lon) == ("no_match", None, None)


def test_blank_or_missing_address_is_no_match_without_calling_transport():
    class Boom:
        def get(self, address):
            raise AssertionError("must not be called")

    geo = g.CensusGeocoder(Boom())
    assert geo.geocode(None).status == "no_match"
    assert geo.geocode("   ").status == "no_match"


def test_suite_suffix_does_not_downgrade_an_exact_street_match():
    payload = census("matched")["response"]
    addr = "19296 Troy Shoals Suite 12, Mattapoisett, MA"
    result = g.CensusGeocoder(g.RecordedTransport({addr: payload})).geocode(addr)
    assert result.status == "matched"


def test_multiple_candidates_are_low_match():
    payload = census("matched")["response"]
    payload["result"]["addressMatches"].append(payload["result"]["addressMatches"][0])
    result = g.CensusGeocoder(g.RecordedTransport({ADDRESS: payload})).geocode(ADDRESS)
    assert result.status == "low_match"


@pytest.mark.parametrize("payload", [{}, {"result": {}}, {"result": {"addressMatches": [{}]}}])
def test_malformed_payloads_raise_geocode_error(payload):
    with pytest.raises(g.GeocodeError):
        g.CensusGeocoder(g.RecordedTransport({ADDRESS: payload})).geocode(ADDRESS)


def test_recorded_transport_refuses_unrecorded_addresses():
    with pytest.raises(g.GeocodeError, match="no recorded response"):
        g.RecordedTransport({}).get("1 Nowhere St")


# -- real HTTP transport (opener injected: never reaches the network) ----------------------


def test_urllib_transport_builds_the_census_url_and_parses_json():
    seen = []

    def opener(url, timeout):
        seen.append((url, timeout))
        return json.dumps(census("matched")["response"]).encode()

    payload = g.UrllibTransport(opener=opener, timeout=3.0).get(ADDRESS)
    assert payload["result"]["addressMatches"]
    url, timeout = seen[0]
    assert url.startswith("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?")
    assert "benchmark=Public_AR_Current" in url and "format=json" in url
    assert "address=19296+Troy+Shoals%2C+Mattapoisett%2C+MA" in url
    assert timeout == 3.0


@pytest.mark.parametrize("exc", [urllib.error.URLError("down"), TimeoutError(), ValueError("json")])
def test_urllib_transport_wraps_failures(exc):
    def opener(url, timeout):
        if isinstance(exc, ValueError):
            return b"not json"
        raise exc

    with pytest.raises(g.GeocodeError):
        g.UrllibTransport(opener=opener).get(ADDRESS)


def test_real_transport_is_unreachable_in_tests():
    """The autouse socket guard in conftest turns any real HTTP attempt into a hard failure."""
    with pytest.raises(g.GeocodeError):
        g.UrllibTransport().get(ADDRESS)


# -- cache + pending queue (the Spark job has no internet; a host step fills the cache) ----


def test_cache_roundtrip_and_hit_skips_inner(tmp_path):
    cache = g.JsonlGeocodeCache(tmp_path / "cache.jsonl")
    assert cache.get(ADDRESS) is None
    cache.put(ADDRESS, g.GeocodeResult("matched", 41.6, -70.8))
    again = g.JsonlGeocodeCache(tmp_path / "cache.jsonl")
    assert again.get(ADDRESS) == g.GeocodeResult("matched", 41.6, -70.8)

    class Boom:
        def geocode(self, address):
            raise AssertionError("cache hit must not call the inner geocoder")

    geo = g.CachedGeocoder(again, inner=Boom())
    assert geo.geocode(ADDRESS).status == "matched"


def test_cache_miss_calls_inner_and_stores(tmp_path):
    cache = g.JsonlGeocodeCache(tmp_path / "c.jsonl")
    geo = g.CachedGeocoder(cache, inner=g.CensusGeocoder(_recorded("matched")))
    assert geo.geocode(ADDRESS).status == "matched"
    assert cache.get(ADDRESS) is not None


def test_offline_miss_is_no_match_and_queued_but_not_cached(tmp_path):
    cache = g.JsonlGeocodeCache(tmp_path / "c.jsonl")
    geo = g.CachedGeocoder(cache, inner=None)
    assert geo.geocode(ADDRESS) == g.GeocodeResult("no_match", None, None)
    assert geo.pending == {ADDRESS}
    assert cache.get(ADDRESS) is None


def test_inner_failure_degrades_to_no_match_without_poisoning_cache(tmp_path):
    cache = g.JsonlGeocodeCache(tmp_path / "c.jsonl")
    geo = g.CachedGeocoder(cache, inner=g.CensusGeocoder(g.RecordedTransport({})))
    assert geo.geocode(ADDRESS).status == "no_match"
    assert cache.get(ADDRESS) is None and geo.pending == {ADDRESS}


def test_none_address_is_no_match_and_not_queued(tmp_path):
    geo = g.CachedGeocoder(g.JsonlGeocodeCache(tmp_path / "c.jsonl"), inner=None)
    assert geo.geocode(None).status == "no_match" and geo.pending == set()


def test_cache_tolerates_corrupt_lines(tmp_path):
    path = tmp_path / "c.jsonl"
    path.write_text('not json\n{"address": "a", "status": "no_match"}\n')
    assert g.JsonlGeocodeCache(path).get("a") == g.GeocodeResult("no_match", None, None)


def test_fill_cache_cli_resolves_pending_requests(tmp_path):
    req = tmp_path / "requests.txt"
    req.write_text(f"{ADDRESS}\n\n")
    cache_path = tmp_path / "cache.jsonl"
    rc = g.main(
        ["--requests", str(req), "--cache", str(cache_path)], transport=_recorded("low_match")
    )
    assert rc == 0
    assert g.JsonlGeocodeCache(cache_path).get(ADDRESS).status == "low_match"


def test_fill_cache_cli_reports_failures_nonzero(tmp_path):
    req = tmp_path / "requests.txt"
    req.write_text(f"{ADDRESS}\n")
    rc = g.main(
        ["--requests", str(req), "--cache", str(tmp_path / "c.jsonl")],
        transport=g.RecordedTransport({}),
    )
    assert rc == 1


@pytest.mark.parametrize(
    "line",
    [
        '{"address": "a", "status": "weird"}',
        '{"address": "a", "status": "matched", "lat": 99, "lon": 0}',
        '{"address": "a", "status": "matched", "lat": 0, "lon": 181}',
        '{"address": "a", "status": "matched"}',
        '{"address": "a", "status": "no_match", "lat": 1.0, "lon": 1.0}',
        '{"address": 5, "status": "no_match"}',
    ],
)
def test_cache_rejects_malformed_or_wild_entries_and_counts_them(tmp_path, line):
    path = tmp_path / "c.jsonl"
    path.write_text(line + '\n{"address": "ok", "status": "no_match"}\n')
    cache = g.JsonlGeocodeCache(path)
    assert cache.get("a") is None and cache.get("ok") is not None
    assert cache.rejected == 1
