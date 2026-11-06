# ADR-0005: Gateway (KrakenD CE), authentication, and gateway-generated errors

Status: accepted (WU8). KrakenD Community Edition **3.0.3** (config `version: 4`), pinned by digest
in `deploy/docker/compose.gateway.yaml`.

## Context
The design asks the gateway for: JWT/key auth, per-key rate limiting, CORS allow-list, a 10 s
timeout, request size limit, security headers, and problem+json passthrough from the API. Two
facts from the plan review shape the solution (design section 10): KrakenD CE has no API-key
validator, and its router-level rate limiter rejects before the proxy layer, so response
modifiers may not apply to gateway-generated 429s. The API (WU7, ADR-0006) trusts only the
`X-Gateway-Secret` header plus a forwarded `X-Key-Id`.

## Spike: `Retry-After` header and JSON body on gateway-generated 429s
Time-boxed, run against the real KrakenD 3.0.3 container (a throwaway backend, a rate of 1 req/s).
Raw results:

| Option tried | Result |
|---|---|
| Router-level `qos/ratelimit/router` (`client_max_rate`), default | 429, **`Content-Length: 0`, no `Retry-After`**, no body. |
| ... plus `router.return_error_msg: true` | Same: the router limiter aborts with a bare 429; the flag only affects errors that travel through the proxy pipe. |
| ... plus `modifier/martian` `header.Modifier` with `scope: ["response"]`, at endpoint and backend level | `Retry-After: 1` appears on the **200** responses and **never on the 429**. Confirms martian runs inside the proxy pipe, after the router limiter. |
| Proxy-level `qos/ratelimit/proxy` on the backend | Limit exhaustion surfaces as an internal error: **500, empty body** (log: `rate limit exceded`), not 429. Not usable as a 429 source. |
| CE Lua (`modifier/lua-endpoint`, available in the CE image) | Runs, but only on requests that reach the endpoint handler. `custom_error(body, 429, "application/json")` sets the status and content type but the **body is still empty**, and Lua has no hook into the router limiter that decides to reject (and no state to implement its own limiter safely). Note: in 3.0 the Lua global is `ctx.load()`, not `request.load()`. |
| Custom Go plugin | Not attempted: needs a custom KrakenD build with a matching Go toolchain/plugin ABI, i.e. we would stop running the stock pinned image. Rejected for Phase 1 maintenance cost. |

Other KrakenD-generated errors behave the same way: JWT failure is a 401 with an empty body, an
unknown route is gin's plain-text `404 page not found`, a backend timeout is a bare 500.

**Decision (the named fallback): a thin header+body-adding edge in front of KrakenD.**
`deploy/docker/gateway/edge.py` (standard library only, `python:3.12-slim` digest-pinned,
non-root, read-only, all capabilities dropped) is the only published service. It forwards
everything to KrakenD and:

* gives every gateway-generated error (status >= 400 with an empty or `text/plain` body) a JSON
  body `{"code","message","status"}`; a 429 additionally carries `retry_after_seconds` and a
  **`Retry-After` header**, which this spike therefore proves works;
* leaves every other response byte-for-byte alone (problem+json from the API, PDFs, data), so
  the API's own 503 + `Retry-After` and problem bodies pass through untouched (tested against the
  real API and compared with a direct call);
* enforces the request size limit (64 KiB, 413 `payload_too_large`; chunked request bodies get 411);
* rejects an `Origin` that is not in the CORS allow-list with 403 `origin_not_allowed` (the list is
  read from `gateway/krakend.json`, the single source of truth; a wildcard makes the edge refuse
  to start). KrakenD's CORS middleware alone only omits the `Access-Control-*` headers, which a
  non-browser client ignores;
* removes `Server` / `X-Krakend*` fingerprint headers, sets `X-Forwarded-For` to the real peer,
  and drops client-supplied `X-Gateway-Secret`, `X-Key-Id` and `X-Forwarded-For`.

### Strict request handling (hardening after two adversarial reviews)
The edge and KrakenD must never disagree about where a request ends, who sent it, or which rate
limit bucket it belongs to. Review findings that shaped the rules: Python's tolerant header parser
stops silently at a malformed line (dropping a following `Content-Length`, so the body was parsed as
a second, smuggled request); a dict copy of duplicate headers keeps the last value while the edge
had decided on the first; KrakenD 3.0.3 accepts non-canonical trailing base64url bits, so one JWT
had four Authorization strings and four buckets; per-read socket timeouts allow slowloris. The edge
therefore has **its own HTTP/1.1 parser** (`parse_head`, no `http.server` request parsing). Every
violation gets a JSON 4xx with a `code` and a closed connection, and never reaches KrakenD.

**Parsing.** The header block is read until `CRLF CRLF`, at most 8 KiB and 100 header lines (else
431). Rejected with 400: bare LF or bare CR, obs-fold continuation lines, NUL/control characters,
any header line that is not `token ":" OWS value` (so `Name : v` and `Bad Header: v` fail instead of
truncating parsing), a request line that is not `METHOD SP target SP HTTP/1.1` (HTTP/0.9 is 400,
other versions 505), a missing `Host`, and `Expect` (417, before any `100 Continue`). Methods other
than GET/HEAD/POST/PUT/DELETE/PATCH/OPTIONS get 405.

**Framing.** More than one `Host`, `Origin`, `Authorization`, `Content-Type`, `Content-Length`,
`Transfer-Encoding` or `Expect` (and CORS preflight headers) is 400, even when the values are equal.
`Content-Length` must match `[0-9]{1,18}` (ASCII only); with `Transfer-Encoding` it is 400,
`Transfer-Encoding` alone is 411, too large 413. The body is read to exactly the validated length
and the forwarded `Content-Length` is rebuilt from it; pipelined bytes stay in the edge's buffer and
are parsed as their own request, so the upstream sees exactly the requests the client intended.

**Forwarding is allow-list based.** Only `Accept`, `Accept-Language`, `Content-Type`, `Origin` and the
two CORS preflight headers are copied (duplicates of the list-valued ones are joined). The edge
builds `Authorization` (canonical), `Host` (always the fixed upstream), `X-Forwarded-For` (the real
peer) and `Content-Length` itself. Everything else (Cookie, Forwarded, X-Forwarded-Host,
`X-Key-Id`, `X-Gateway-Secret`, Accept-Encoding, ...) is dropped.

**Authorization is canonical or absent.** Exactly `Bearer <token>` (case-sensitive scheme, one
space, at most 2048 bytes else 431) where the token is three non-empty unpadded base64url segments,
each of which re-encodes to itself (no spare trailing bits), with a 43-character (32-byte, HS256)
signature. Anything else is dropped so KrakenD answers 401 and never allocates a bucket for it. Result:
a valid token has exactly one spelling and therefore one rate-limit bucket (the 3 non-canonical
spellings that KrakenD would accept are tested live to get 401). Tokens that are canonical but not
validly signed can still reach KrakenD, so a per-peer token bucket at the edge (`PEER_RATE`
200/s, burst 400, table bounded to 1024 peers with oldest-first eviction) caps how fast any
one address can create buckets; it answers 429 JSON when exceeded.

**Routes.** The edge forwards only `/coverage`, `/loans`, `/loans/<id>`, `/loans/<id>/source`,
`/health/live`, `/health/ready` and `/__health` (`<id>` is `[A-Za-z0-9_.:-]{1,128}`); every other
spelling (`/loans/`, `/loans//x`, `;x=1`, `%252e`, case changes, trailing slash) is 404 from the edge, so
no alternate path can reach a limiter through a different key. Targets containing `..`, `.` segments,
encoded `%2e %2f %5c %00`, backslashes, controls, non-ASCII or an absolute-URI/`//` form are 400. The
check uses the raw request line.

**Slow clients.** Time limits are totals, not per read: `HEADER_TIMEOUT` (10 s from the first byte to
the end of the header block), `BODY_TIMEOUT` (15 s for the whole body), `CLIENT_TIMEOUT` (15 s idle
keep-alive and the cap for any single read/write); a miss is 408 and a close. Concurrency is bounded
globally (`MAX_CONNECTIONS` 64, 503 JSON) and per peer address (`PEER_MAX_CONNECTIONS` 16, 429 JSON),
so a few dripping connections cannot take every slot. Behind Docker's NAT every host client shares one
peer address, which is why the per-peer figures are generous.

**Noise.** Broken pipes, resets and timeouts are swallowed; real exceptions are still reported.

`Retry-After` is the fixed `RETRY_AFTER_SECONDS=1`. That is honest because every configured
bucket refills at least 1 token per second (a test asserts `client_max_rate >= 1` for every
endpoint). If a slower limit is ever configured, the edge must learn it.

The proxy-level limiter named in the plan as the other fallback was rejected above (500, not 429).

## Authentication
* KrakenD `auth/validator`, **HS256**, one symmetric JWK (`kid: gw-1`) built at container start from
  `JWT_SIGNING_KEY` (`make env` generates 256 bits of hex; `gateway/entrypoint.sh` renders the JWK and
  `krakend.json` into a tmpfs, so no secret is committed, baked into an image, or visible in
  `docker compose config` beyond the variable reference). Issuer `bridge-loan-gateway`, audience
  `bridge-loan-api`, `exp` required.
* Per-key identity is the **`sub` claim**, minted by `make gateway-token KEY_ID=<id>`
  (`gateway/mint_token.py`); `propagate_claims` turns it into `X-Key-Id` for the audit log. A client
  `X-Key-Id` is overwritten (and dropped by the edge before that). One signing key means
  revocation of a single key id is not possible in Phase 1 (rotate the key, or set short `exp`);
  an OIDC/JWT issuer is required before any non-local exposure (design section 10).
* KrakenD injects `X-Gateway-Secret` toward the API with a martian `header.Modifier`; the value is
  a `${GATEWAY_SECRET}` placeholder in the committed file, substituted from `.env` at start (the
  entrypoint refuses to start if any placeholder is left). The API still compares in constant time.
* `/health/live` and `/health/ready` are exposed unauthenticated (they carry no PII; the OpenAPI
  document declares no security for them). KrakenD's `/__health` is the container healthcheck.
* Rate limit buckets: data endpoints are keyed on the `Authorization` header (the token is the key
  identity; garbage tokens only create unused buckets and fail auth), health on the edge-set
  `X-Forwarded-For`. `/loans` and `/coverage`: 10 req/s, burst 20. `/loans/{id}` and
  `/loans/{id}/source` (borrower PII, large PDFs): 2 req/s, burst 4.

## Other settings
Timeout 10 s (`timeout`, verified live: a backend that sleeps is cut off at ~10 s), explicit
`read_header/read/idle` timeouts, GET-only endpoints with `no-op` encoding (status, headers and
body pass straight through), query-string and header allow-lists (query names equal the OpenAPI
parameters; a test fails on drift), CORS allow-list of the two local Vite origins with no wildcard and
no credentials, security headers (`nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, a
`default-src 'none'` CSP; the API's stricter CSP on the PDF route is passed through).
`USAGE_DISABLE=1` turns off KrakenD's anonymous usage reporting.

Only the edge publishes a port: `127.0.0.1:${GATEWAY_PORT:-8080}`; `krakend` and `api` are on the
private `core` network only (checked on the rendered `docker compose config` and on the running
stack).

## Consequences
* A second small container to maintain, but ~200 lines of standard library, fully unit-tested.
* Gateway errors are plain JSON with a `code` (not problem+json); the OpenAPI `Problem` schema
  describes the API's errors only. Frontend retry UX reads `retry_after_seconds` / `Retry-After`.
* Moving to a cloud-native gateway later means re-proving the 429 body/header requirement there.
* Findings for other units: `openapi.json` documents the `/loans` 200 `application/geo+json` body as a
  bare `{"type":"string"}` rather than the `FeatureCollection` schema (WU7); the gateway contract test
  validates against the `FeatureCollection` component instead.
