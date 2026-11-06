"""End-to-end gateway behaviour: real KrakenD + edge in front of the real api image.

Needs Docker; run with RUN_STACK=1 (project ``gwtest``, loopback port 18080).
"""

from __future__ import annotations

import json

import pytest
from gateway_helpers import (
    http,
    load_krakend,
    load_openapi,
    response_schema,
    sign_jwt,
    signature_variants,
    validate,
)

pytestmark = pytest.mark.stack

SPEC = load_openapi()


def bearer(gw, sub="key-alpha", **kw):
    return {"Authorization": f"Bearer {sign_jwt(gw.jwt_key, sub, **kw)}"}


def jbody(resp):
    return json.loads(resp.body)


def assert_contract(path, status, resp):
    """The response body validates against the schema openapi.json documents for it."""
    ctype = resp.header("content-type").split(";")[0]
    schema = response_schema(SPEC, path, "get", status, ctype)
    assert schema is not None, f"{path} {status} {ctype} is not documented in openapi.json"
    if ctype == "application/geo+json" and schema == {"type": "string"}:
        # openapi.json (WU7) documents the GeoJSON list as a bare string; validate the body
        # against the FeatureCollection component the API actually returns.
        schema = {"$ref": "#/components/schemas/FeatureCollection"}
    if ctype == "application/pdf":
        return
    assert validate(SPEC, schema, jbody(resp)) == []


def direct_api(gw, path, headers=None):
    """GET straight at the api container (from inside the private network)."""
    hdrs = json.dumps(headers or {})
    code = (
        "import json,urllib.request,urllib.error\n"
        f"req=urllib.request.Request('http://127.0.0.1:8000{path}',headers={hdrs})\n"
        "try:\n r=urllib.request.urlopen(req,timeout=5)\n"
        "except urllib.error.HTTPError as e:\n r=e\n"
        "print(json.dumps({'status':r.status,'ctype':r.headers.get('content-type'),"
        "'body':r.read().decode()}))"
    )
    return json.loads(gw.api_exec(code))


# ---- 1. authentication ---------------------------------------------------------------------


def test_no_jwt_gives_401_json_with_code(harness):
    resp = http(f"{harness.base}/loans")
    assert resp.status == 401
    assert resp.header("content-type").startswith("application/json")
    assert jbody(resp)["code"] == "unauthorized"


@pytest.mark.parametrize(
    "label,token_kwargs",
    [
        ("expired", {"exp_in": -60}),
        ("wrong issuer", {"iss": "someone-else"}),
        ("wrong audience", {"aud": "other-api"}),
        ("alg none", {"alg": "none"}),
        ("alg HS512 header", {"alg": "HS512"}),
    ],
)
def test_invalid_jwt_variants_are_rejected(harness, label, token_kwargs):
    resp = http(f"{harness.base}/coverage", headers=bearer(harness, **token_kwargs))
    assert resp.status == 401, label
    assert jbody(resp)["code"] == "unauthorized"


def test_wrong_signing_key_is_rejected(harness):
    token = sign_jwt("x" * 64, "key-alpha")
    resp = http(f"{harness.base}/coverage", headers={"Authorization": f"Bearer {token}"})
    assert resp.status == 401 and jbody(resp)["code"] == "unauthorized"


def test_garbage_authorization_is_rejected(harness):
    resp = http(f"{harness.base}/coverage", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status == 401 and jbody(resp)["code"] == "unauthorized"


def test_valid_jwt_reaches_backend_and_response_matches_contract(harness):
    resp = http(f"{harness.base}/coverage", headers=bearer(harness))
    assert resp.status == 200
    assert_contract("/coverage", 200, resp)


def test_health_endpoints_need_no_jwt(harness):
    live = http(f"{harness.base}/health/live")
    assert live.status == 200
    assert_contract("/health/live", 200, live)
    ready = http(f"{harness.base}/health/ready")
    assert ready.status == 200
    assert_contract("/health/ready", 200, ready)
    assert http(f"{harness.base}/__health").status == 200


# ---- 2. backend trust: shared secret + forwarded key id ---------------------------------------


def test_direct_to_backend_without_secret_is_rejected(harness):
    direct = direct_api(harness, "/loans")
    assert direct["status"] == 401
    assert json.loads(direct["body"])["code"] == "gateway_auth_required"
    with_secret_only = direct_api(harness, "/loans", {"X-Gateway-Secret": harness.gateway_secret})
    assert with_secret_only["status"] == 401
    assert json.loads(with_secret_only["body"])["code"] == "key_id_required"


def test_api_is_not_published_on_the_host(harness):
    port = harness.compose("port", "api", "8000", check=False)
    # `docker compose port` prints ":0" when the port is not published
    assert port.stdout.strip() in ("", ":0"), "api must not publish a host port"
    ps = harness.compose("ps", "--format", "json").stdout
    for line in ps.splitlines():
        svc = json.loads(line)
        if svc["Service"] == "api":
            assert not svc.get("Publishers") or all(
                not p.get("PublishedPort") for p in svc["Publishers"]
            )


def test_gateway_injects_secret_and_key_id_from_claim_not_from_client(harness):
    loans = http(f"{harness.base}/loans?limit=1", headers=bearer(harness, "key-real"))
    loan_id = jbody(loans)["features"][0]["properties"]["loan_id"]
    resp = http(
        f"{harness.base}/loans/{loan_id}",
        headers={
            **bearer(harness, "key-real"),
            "X-Key-Id": "spoofed-key",
            "X-Gateway-Secret": "spoofed-secret",
        },
    )
    assert resp.status == 200
    assert_contract("/loans/{loan_id}", 200, resp)
    audit = harness.api_exec("print(open('/var/lib/bridge-loan/audit/audit.log').read())")
    entries = [json.loads(line) for line in audit.splitlines() if line.strip()]
    mine = [e for e in entries if e["loan_id"] == loan_id]
    assert mine and {e["key_id"] for e in mine} == {"key-real"}


# ---- 5/6. passthrough + contract ------------------------------------------------------------


def test_list_and_detail_validate_against_openapi(harness):
    listing = http(f"{harness.base}/loans?limit=3", headers=bearer(harness))
    assert listing.status == 200
    assert listing.header("content-type").startswith("application/geo+json")
    assert_contract("/loans", 200, listing)


@pytest.mark.parametrize(
    "path,openapi_path,status",
    [
        ("/loans?bbox=1,2", "/loans", 400),
        ("/loans?limit=999999", "/loans", 400),
        ("/loans/does-not-exist", "/loans/{loan_id}", 404),
        ("/loans/does-not-exist/source", "/loans/{loan_id}/source", 404),
    ],
)
def test_problem_json_passes_through_untouched(harness, path, openapi_path, status):
    headers = bearer(harness)
    via_gateway = http(f"{harness.base}{path}", headers=headers)
    direct = direct_api(
        harness,
        path,
        {"X-Gateway-Secret": harness.gateway_secret, "X-Key-Id": "key-alpha"},
    )
    assert via_gateway.status == direct["status"] == status
    assert via_gateway.header("content-type") == direct["ctype"] == "application/problem+json"
    assert via_gateway.body.decode() == direct["body"]
    assert_contract(openapi_path, status, via_gateway)


def test_unknown_cursor_410_style_errors_keep_backend_code(harness):
    resp = http(f"{harness.base}/loans?cursor=garbage", headers=bearer(harness))
    assert resp.status in (400, 410)
    assert jbody(resp)["code"]
    assert resp.header("content-type") == "application/problem+json"


def test_unlisted_query_params_are_not_forwarded(harness):
    # the backend would 400 on a bad `limit`; an unlisted name never reaches it
    ok = http(f"{harness.base}/loans?evil=1&limit=1", headers=bearer(harness))
    assert ok.status == 200


# ---- gateway-generated errors -----------------------------------------------------------------


def test_unknown_route_is_json_404(harness):
    resp = http(f"{harness.base}/nope", headers=bearer(harness))
    assert resp.status == 404
    assert resp.header("content-type").startswith("application/json")
    assert jbody(resp)["code"] == "not_found"


def test_write_methods_are_rejected_with_json_code(harness):
    resp = http(f"{harness.base}/loans", method="POST", headers=bearer(harness), data=b"{}")
    assert resp.status in (404, 405)
    assert jbody(resp)["code"] in ("not_found", "method_not_allowed")


def test_request_size_limit_returns_413_json(harness):
    resp = http(
        f"{harness.base}/loans", method="POST", headers=bearer(harness), data=b"x" * 200_000
    )
    assert resp.status == 413
    assert jbody(resp)["code"] == "payload_too_large"


# ---- 3. CORS ----------------------------------------------------------------------------------


def _allowed_origin():
    return load_krakend()["extra_config"]["security/cors"]["allow_origins"][0]


def _preflight(harness, origin):
    return http(
        f"{harness.base}/loans",
        method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )


def test_cors_allows_listed_origin(harness):
    origin = _allowed_origin()
    resp = _preflight(harness, origin)
    assert resp.header("access-control-allow-origin") == origin
    actual = http(f"{harness.base}/health/live", headers={"Origin": origin})
    assert actual.header("access-control-allow-origin") == origin


def test_cors_rejects_unlisted_origin(harness):
    resp = _preflight(harness, "https://evil.example")
    assert resp.header("access-control-allow-origin") is None
    actual = http(
        f"{harness.base}/coverage",
        headers={**bearer(harness), "Origin": "https://evil.example"},
    )
    assert actual.header("access-control-allow-origin") is None
    assert actual.status == 403
    assert jbody(actual)["code"] == "origin_not_allowed"


def test_cors_never_answers_wildcard(harness):
    resp = _preflight(harness, "null")
    assert resp.header("access-control-allow-origin") is None


# ---- 4. rate limiting ---------------------------------------------------------------------------


def _burst(url, headers, n):
    return [http(url, headers=headers) for _ in range(n)]


def test_rate_limit_429_has_json_body_and_retry_after(harness):
    headers = bearer(harness, "rl-detail")
    out = _burst(f"{harness.base}/loans/does-not-exist", headers, 40)
    limited = [r for r in out if r.status == 429]
    assert limited, "expected the stricter /loans/{id} limit to trip within 40 rapid requests"
    r = limited[0]
    assert r.header("content-type").startswith("application/json")
    body = jbody(r)
    assert body["code"] == "rate_limited"
    assert isinstance(body["retry_after_seconds"], int) and body["retry_after_seconds"] >= 1
    assert r.header("retry-after") == str(body["retry_after_seconds"])
    assert {r.status for r in out} <= {404, 429}


def test_noncanonical_signature_spellings_cannot_multiply_rate_limit_buckets(harness):
    """KrakenD accepts 4 spellings of one signature; the edge must collapse them to one bucket."""
    token = sign_jwt(harness.jwt_key, "bucket-probe")
    variants = signature_variants(token)
    assert len(set(variants)) == 4
    statuses = {v: [] for v in variants}
    for _ in range(12):
        for v in variants:
            r = http(f"{harness.base}/loans/x", headers={"Authorization": f"Bearer {v}"})
            statuses[v].append(r.status)
    assert all(
        s == 401 for s in statuses[variants[1]] + statuses[variants[2]] + statuses[variants[3]]
    )
    canonical = statuses[variants[0]]
    assert 404 in canonical and 429 in canonical
    # burst 4 plus a refill or two; four buckets would have allowed ~16
    assert canonical.count(404) <= 8, canonical


def test_list_endpoint_limit_is_more_permissive_than_detail(harness):
    detail = _burst(f"{harness.base}/loans/x", bearer(harness, "cmp-detail"), 12)
    listing = _burst(f"{harness.base}/loans?limit=1", bearer(harness, "cmp-list"), 12)
    assert sum(r.status == 429 for r in detail) > sum(r.status == 429 for r in listing)


def test_rate_limits_are_per_key(harness):
    noisy = bearer(harness, "noisy")
    for _ in range(100):
        if http(f"{harness.base}/loans/x", headers=noisy).status == 429:
            break
    else:
        pytest.fail("noisy key never rate limited")
    # right after the trip: the noisy key is still limited, a different key is not
    assert http(f"{harness.base}/loans/x", headers=noisy).status == 429
    quiet = http(f"{harness.base}/loans/x", headers=bearer(harness, "quiet"))
    assert quiet.status == 404


# ---- 9. headers -------------------------------------------------------------------------------


def test_security_headers_present_and_fingerprints_absent(harness):
    resp = http(f"{harness.base}/health/live")
    assert resp.header("x-content-type-options") == "nosniff"
    assert resp.header("x-frame-options") == "DENY"
    assert resp.header("referrer-policy")
    assert "default-src 'none'" in resp.header("content-security-policy")
    assert resp.header("x-krakend") is None and resp.header("x-krakend-completed") is None
    assert resp.header("server") is None


def test_security_headers_also_on_gateway_errors(harness):
    resp = http(f"{harness.base}/loans")
    assert resp.status == 401
    assert resp.header("x-content-type-options") == "nosniff"
