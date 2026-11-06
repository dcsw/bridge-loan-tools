"""Static checks on gateway/krakend.json (no Docker needed)."""

from __future__ import annotations

import json
import re

import pytest
from gateway_helpers import KRAKEND_JSON, load_krakend, load_openapi

SECRET_PLACEHOLDERS = {"X-Gateway-Secret": "${GATEWAY_SECRET}"}
HEALTH = {"/health/live", "/health/ready"}


def _endpoints():
    return {e["endpoint"]: e for e in load_krakend()["endpoints"]}


def _ratelimit(endpoint):
    return endpoint["extra_config"]["qos/ratelimit/router"]


def test_global_settings():
    cfg = load_krakend()
    assert cfg["version"] == 4
    assert cfg["timeout"] == "10s"
    assert cfg["port"] == 8080
    assert cfg["output_encoding"] == "no-op"
    # the gateway never needs to listen on a non-container interface of its own accord
    assert cfg["read_header_timeout"] and cfg["read_timeout"] and cfg["idle_timeout"]


def test_endpoints_match_openapi_paths_exactly():
    spec_paths = {re.sub(r"\{(\w+)\}", r"{\1}", p) for p in load_openapi()["paths"]}
    gateway = set(_endpoints()) - {"/__health"}
    assert gateway == spec_paths


def test_every_endpoint_is_get_only_and_no_op_passthrough():
    for path, ep in _endpoints().items():
        assert ep["method"] == "GET", path
        assert ep["output_encoding"] == "no-op", path
        for backend in ep["backend"]:
            assert backend["encoding"] == "no-op", path


def test_query_strings_whitelist_equals_openapi_parameters():
    spec = load_openapi()
    endpoints = _endpoints()
    for path, item in spec["paths"].items():
        wanted = sorted(p["name"] for p in item["get"].get("parameters", []) if p["in"] == "query")
        assert sorted(endpoints[path].get("input_query_strings", [])) == wanted, path


def test_path_params_map_to_backend_patterns():
    endpoints = _endpoints()
    assert endpoints["/loans/{loan_id}"]["backend"][0]["url_pattern"] == "/loans/{loan_id}"
    assert (
        endpoints["/loans/{loan_id}/source"]["backend"][0]["url_pattern"]
        == "/loans/{loan_id}/source"
    )


def test_only_whitelisted_headers_reach_the_backend():
    for path, ep in _endpoints().items():
        if path == "/__health":
            continue
        forwarded = {h.lower() for h in ep.get("input_headers", [])}
        # x-key-id is listed only so KrakenD lets the claim-derived value through to the backend
        # (propagate_claims overwrites any client value; the edge also drops client X-Key-Id)
        assert forwarded <= {"accept", "accept-language", "x-key-id"}, (path, forwarded)
        assert "x-gateway-secret" not in forwarded
        assert ("x-key-id" in forwarded) == ("auth/validator" in ep["extra_config"]), path


@pytest.mark.parametrize(
    "path", ["/coverage", "/loans", "/loans/{loan_id}", "/loans/{loan_id}/source"]
)
def test_data_endpoints_require_hs256_jwt_and_forward_key_id(path):
    ep = _endpoints()[path]
    auth = ep["extra_config"]["auth/validator"]
    assert auth["alg"] == "HS256"
    assert auth["propagate_claims"] == [["sub", "X-Key-Id"]]
    assert auth["jwk_local_path"].startswith("/")
    assert auth["issuer"] and auth["audience"]
    assert auth["disable_jwk_security"] is True  # symmetric (oct) JWK
    assert auth.get("cache") is not True or "cache_duration" in auth


@pytest.mark.parametrize("path", sorted(HEALTH))
def test_health_endpoints_are_unauthenticated(path):
    assert "auth/validator" not in _endpoints()[path].get("extra_config", {})


def test_backend_gets_shared_secret_injected_via_placeholder_not_literal():
    for path, ep in _endpoints().items():
        if path == "/__health":
            continue
        mod = ep["backend"][0]["extra_config"]["modifier/martian"]["header.Modifier"]
        assert {mod["name"]: mod["value"]} == SECRET_PLACEHOLDERS, path
        assert mod["scope"] == ["request"]


def test_backend_host_is_private_api_service():
    for path, ep in _endpoints().items():
        if path == "/__health":
            continue
        assert ep["backend"][0]["host"] == ["http://api:8000"], path


def test_cors_allow_list_is_explicit_and_no_wildcard():
    cors = load_krakend()["extra_config"]["security/cors"]
    origins = cors["allow_origins"]
    assert origins and all(o != "*" and "*" not in o for o in origins)
    assert all(re.fullmatch(r"https?://[A-Za-z0-9.-]+(:\d+)?", o) for o in origins)
    assert set(cors["allow_methods"]) <= {"GET", "HEAD", "OPTIONS"}
    assert "*" not in cors.get("allow_headers", [])
    assert cors.get("allow_credentials", False) is False
    assert "Authorization" in cors["allow_headers"]


def test_security_headers_configured():
    sec = load_krakend()["extra_config"]["security/http"]
    assert sec["content_type_nosniff"] is True
    assert sec["frame_deny"] is True
    assert sec["referrer_policy"]
    assert "default-src 'none'" in sec["content_security_policy"]


def test_rate_limits_are_per_key_and_detail_is_stricter():
    eps = _endpoints()
    for path in ("/coverage", "/loans", "/loans/{loan_id}", "/loans/{loan_id}/source"):
        rl = _ratelimit(eps[path])
        assert rl["strategy"] == "header" and rl["key"] == "Authorization", path
    for path in HEALTH:
        rl = _ratelimit(eps[path])
        assert rl["strategy"] == "header" and rl["key"] == "X-Forwarded-For", path
    listing = _ratelimit(eps["/loans"])["client_max_rate"]
    for path in ("/loans/{loan_id}", "/loans/{loan_id}/source"):
        assert _ratelimit(eps[path])["client_max_rate"] < listing, path


def test_all_rates_refill_within_one_second_for_the_fixed_retry_after():
    """edge.py advertises Retry-After: 1; that is only honest while every bucket refills >= 1/s."""
    for path, ep in _endpoints().items():
        if path == "/__health":
            continue
        assert _ratelimit(ep)["client_max_rate"] >= 1, path


def test_no_secret_material_in_committed_config():
    text = KRAKEND_JSON.read_text()
    assert not re.search(r"[0-9a-f]{40,}", text), "long hex string (looks like a key)"
    assert not re.search(r"eyJ[A-Za-z0-9_-]{10,}\.", text), "looks like a JWT"
    assert '"k"' not in text


def test_config_is_valid_json_with_schema_reference():
    assert json.loads(KRAKEND_JSON.read_text())["$schema"].startswith("https://www.krakend.io/")
