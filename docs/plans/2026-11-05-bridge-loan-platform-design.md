# Bridge Loan Data Platform: Design (v2)

Date: 2026-11-05 · Status: DRAFT v2, Design Review Gate round 2 pending
Changes from v1: addresses round-1 blockers from PM, Architect, Designer, Security, CTO.

## 1. Users and use cases
**Persona:** a bridge-loan researcher (today working manually from `info/Free Sources...md`) who decides which properties or private lenders to shortlist for verification.

| # | WHO | WANTS | SO THAT | WHEN |
|---|-----|-------|---------|------|
| U1 | Researcher | a ranked, mappable list of likely bridge loans in a town | they can shortlist properties/lenders to verify instead of reading every instrument by hand | starting research on a town |
| U2 | Researcher | to see which signals fired (and which could not be evaluated) for a loan, with the source document | they can verify the inference and trust or reject it | reviewing a candidate |
| U3 | Researcher | filters by town, score, lender type, date range | they can narrow to relevant candidates | after the map loads |
| U4 | Researcher | to see fetch gaps (paid/blocked/failed documents) per town | absence of a pin is not mistaken for absence of a loan | judging coverage |
| U5 | Researcher | to re-run ingestion for new filings without duplicates | data stays current | periodic refresh |

Out of scope: correcting/overriding extractions in the UI (v2+), borrower outreach tooling, public hosting.

## 2. Scope and phasing (decided with user)
- **Thin slice first.** Phase 1 delivers ONE town end to end: Mattapoisett MA (masslandrecords, free index+images), all three extractors, map, Docker-local. Rye NH and Madison CT follow as separate phases once the Phase-1 accuracy gate passes. Full 3-town scope remains the goal.
- **Cloud posture: private by default.** Only the API and storage are deployed to clouds; ingest (Spark, extractors, scraper) is local-only. Cloud modules require auth and do not hold real PII until the user opts in.
- Stated decisions: Obscura = headless-browser fetcher behind an adapter interface (Playwright fallback); PySpark kept (local mode, Docker); Mangum handler for AWS only, container-per-cloud with thin adapters elsewhere; ingest deeds + discharges as well as mortgages; US Census geocoder; BEADS tracking only (no GitHub issue); borrower names omitted from list payload, returned only by detail endpoint.

## 3. Architecture
```
Fetcher (Obscura | Playwright adapter) -> raw PDFs, content-addressed by sha256 (MinIO)
  + manifest table (doc_id=sha256, source_url, town, doc_type, fetched_at, status, gap_reason)
-> Spark (local[*]) 3 isolated extractor jobs: Docling | PyMuPDF | Unstructured
-> BRONZE (Iceberg v2, 3 tables, one per extractor)  [quarantine table for unparseable docs]
-> SILVER  instruments (mortgage/deed/discharge) + loans (derived, with signals/score)
-> SEMANTIC VIEW v1 = silver loans (DuckDB view; also materialized snapshot, see 3.3)
FastAPI <- KrakenD <- React/TS/d3
```

### 3.1 Components and images
- **ingest image** (Spark, Docling, PyMuPDF, Unstructured, pyiceberg): local only.
- **api image** (fastapi, duckdb, mangum only): small; extensions (`iceberg`, `httpfs`) baked in at build time; no runtime INSTALL; DuckDB opened read-only with `enable_external_access` restricted and `lock_configuration`.
- **fetcher image**: Obscura on an isolated network (3.5).
- Catalog: Lakekeeper (Iceberg REST) with MinIO warehouse, path-style S3, versions pinned; format-version 2, copy-on-write for silver (avoids delete-file gaps in DuckDB).
- Python deps split `backend/api`, `backend/ingest`, `backend/common` (shared pydantic models).

### 3.2 Data contracts
- **doc_id** = sha256 of PDF bytes. Immutable raw store; all bronze keyed on doc_id + extractor.
- **Bronze (per extractor):** doc_id, extractor, extractor_version, page_no, text, tables_json, extracted_at, status, error.
- **instruments (silver):** instrument_id = doc_id + ordinal (a document may hold several instruments), doc_type ∈ {mortgage, deed, discharge}, parcel_key (town + map/lot, nullable), address_raw, address_norm, grantor/borrower, grantee/lender, lender_type ∈ {bank, credit_union, private_llc, individual, other, unknown}, principal, recorded_date, maturity_date, term_months, per-field `{value, source_extractor, confidence, disagreement}`; recorded_ref (book/page), source_url.
- **Unification rules:** per field, normalize across extractors, accept if ≥2 agree (confidence 0.9), else take highest-precedence extractor by field (precedence table in code, tested), confidence 0.5, flag `disagreement=true`; all-null stays null (never imputed). Extractor failure is isolated: silver proceeds with remaining extractors.
- **Idempotency:** writes are Iceberg MERGE on (instrument_id); re-running a doc_id replaces, never duplicates.
- **loans (silver, derived):** loan_id = instrument_id of the mortgage; lat, lon, geocode_status ∈ {matched, low_match, no_match}, bridge_score 0-100, signals[] (enum below), signals_unevaluable[], last_ingested_at.
- **Signals (enum + weights, v1):** `short_term` (term ≤ 24 mo) 30; `private_lender` (lender_type ∈ private_llc, individual) 25; `second_mortgage_after_purchase` (mortgage recorded within 90 days after a deed to same parcel, or second lien on parcel) 20; `no_discharge` (no later discharge referencing the mortgage) 15; `keyword` ("bridge", "interim", "swing" in text) 10. Score = sum of fired weights; a signal that cannot be evaluated (missing data) is listed in `signals_unevaluable` and contributes 0. score is an unverified heuristic, shown as such.
- **Geocoding:** Census batch geocoder (10k/batch limit); no_match rows stay in the API with null geometry, excluded from the map and counted in a "N not mapped" indicator.

### 3.3 Semantic view and API read path
- Semantic view is versioned (`semantic_loans_v1`), equal to silver loans for now.
- A publish step materializes the view to a Parquet snapshot + `manifest.json` pointer in the object store. **Deployed API reads the snapshot** (no REST catalog needed in the cloud); local compose API may read Iceberg directly via the catalog. Connection and attach cached at module scope.
- Budgets (acceptance): cold start ≤ 3 s, warm `/loans` p95 ≤ 300 ms at 5k rows.

### 3.4 API (OpenAPI is the source of truth; TS types generated via openapi-typescript)
- `GET /loans` → GeoJSON FeatureCollection. Params: `bbox=minLon,minLat,maxLon,maxLat` (validated, ordered, within bounds), `town` ∈ enum, `min_score` 0-100, `lender_type` enum, `recorded_from`/`recorded_to` ISO dates, `limit` (default 500, max 2000), `cursor`. Response includes `next_cursor`, `total`, `unmapped_count`, `generated_at`. Feature properties (list): loan_id, address_norm, lender (name), lender_type, principal, recorded_date, maturity_date, term_months, bridge_score, signals[], geocode_status. **No borrower name in list.**
- `GET /loans/{loan_id}` → full detail: borrower (authed), per-field confidence/source/disagreement, signals + unevaluable, source document link and book/page.
- `GET /coverage` → per town: documents fetched, gaps by reason (paid, blocked, failed), last_ingested_at.
- `GET /health/live`, `GET /health/ready` (snapshot readable).
- Errors: RFC 7807 problem+json: 400 invalid params, 401/403, 404 unknown id, 413/422, 429, 503 snapshot unavailable (with Retry-After). All queries parameterized; pydantic validation on every param. Response size capped below 5 MB.
- KrakenD: timeout 10 s, passes problem+json through, rate limit per key, CORS allow-list (no wildcard).

### 3.5 Security
- **AuthN/Z:** KrakenD validates an API key (v1, single user; JWT/OIDC later). Backends accept only gateway traffic: local = private compose network; cloud = private ingress/IAM invoker only plus shared-secret header check; deny by default. Detail endpoint (borrower names) requires auth; list works with same key but never returns names.
- **Fetcher SSRF:** https only, domain allow-list for the portals, resolve and block private/link-local/loopback IPs, re-validate every redirect, Obscura on an isolated network with egress only to allow-listed hosts and no route to MinIO/catalog; IMDS blocked. Portal credentials/paid sessions (CT) stored only in a secret manager/gitignored env, never in images. ToS review is a gate before any live scrape; manual PDF drop-folder is the fallback.
- **Untrusted PDFs:** each extractor runs in an unprivileged, read-only, no-network container with CPU/mem/time/page limits, non-root, seccomp default; credentials scoped to write own bronze table only; pre-validation (magic bytes, size, page cap). Parser versions pinned (PyMuPDF is AGPL: license reviewed in an ADR).
- **Secrets/state:** no secrets in compose or repo; `.env` gitignored with `.env.example`; generated dev creds; OpenTofu uses sensitive variables, encrypted remote state per cloud (local state gitignored); least-privilege IAM; pre-commit secret scan.
- **PII:** borrower names classed as personal data; list payload omits them; detail requires auth; buckets encrypted at rest; PII redacted in logs; audit log of detail-endpoint access; retention = until user deletes; raw PDFs, bronze, silver gitignored; fixtures contain no real PII (synthetic). Census geocoder receives addresses (documented).
- **Frontend:** render strings via React escaping/textContent only, never `dangerouslySetInnerHTML`; CSP; no inline script.
- **Supply chain:** lockfiles (uv, package-lock), digest-pinned images, Dependabot/Renovate, pip-audit/npm audit/trivy in CI, provider versions pinned with committed lock file. Compose ports bound to 127.0.0.1; MinIO/Spark UIs not exposed.

### 3.6 Frontend (React + TS + d3)
- d3-geo map of the town on public TopoJSON, d3-zoom with +/- buttons and keyboard pan; pins clustered at low zoom.
- Pins are focusable buttons with ARIA labels; click/tap/Enter opens a popover/side panel (hover is an enhancement only); Esc closes. Pin shape+color encode score band, plus numeric badge and text in the card (never color alone); legend with thresholds; "unverified heuristic" disclaimer. WCAG AA contrast; respects prefers-reduced-motion.
- Card shows: address, lender, principal, term, recorded/maturity dates, score, fired signals and unevaluable signals, per-field confidence/disagreement, link to source document. Borrower name only after explicit click (detail call).
- States: loading (aria-busy), empty (clear-filters), error (retry, live region), partial ("N not mapped"), coverage banner with gaps and last-ingested time.
- Filters (town, score, lender type, dates) kept in URL query string. A sortable table view alongside the map is the accessible alternative; mobile/touch supported for the popover path.
- Dev: MSW mocks + committed sample GeoJSON so the frontend does not wait for backend phases.

## 4. Infrastructure
- **Local:** `docker compose` profiles (`core`: minio, lakekeeper, api, krakend, web; `ingest`: spark, extractors, fetcher). Ports on 127.0.0.1.
- **API serverless:** AWS Lambda via Mangum + Lambda RIE locally; other clouds run the same FastAPI container on the native runtime (Cloud Run, Azure Container Apps/Functions custom handler, DO Functions/App Platform, OCI Container Instances/Functions) through a thin adapter per cloud.
- **OpenTofu:** modules `docker` (local deploy via kreuzwerker provider), `aws`, `gcp`, `azure`, `digitalocean`, `oci`. Common interface — inputs: `name, region, image_ref, env, bucket_name, snapshot_prefix, api_key_secret_ref`; outputs: `api_url, bucket_name`. KrakenD runs as a container in each cloud (or the provider's gateway with the same config) in front of a private backend. Phase 1 builds `docker` + `aws`; the other clouds are later phases.
- **Verification without cloud accounts:** `tofu` pinned in a tools container; per module `tofu fmt -check`, `validate`, `tflint`, `checkov`, `tofu test` with mock providers, and a shared contract test that every module exposes the common variables/outputs. Real `apply` only for the docker module (CI) and AWS by user opt-in.

## 5. Testing and acceptance
**Layers:** unit (pytest: unification, scoring, normalizers, geocode parsing; Vitest: transforms), contract (OpenAPI/GeoJSON schema, KrakenD config), integration (compose profile on fixtures: fake portal → MinIO → bronze → silver → API → JSON contract), e2e (Playwright: tab to pin, Enter opens card, Esc closes, filters in URL), IaC checks above. Coverage gate ≥ 80% on backend/common and backend/api.
**Fixtures:** recorded HTML/HAR or local fake-portal for the fetcher; synthetic/public-domain sample PDFs per doc type (mortgage, deed, discharge, scanned/low-quality, multi-loan); golden JSON per extractor; recorded Census responses. No real borrower PII.
**Edge-case tests (explicit):** scanned PDF with OCR fallback, multi-instrument documents, missing/ambiguous dates and principal, address variants, geocoder no/low match, duplicate sha256, ingest re-run idempotency, extractor failure isolation, scraper CAPTCHA/timeout/rate-limit/partial failure, empty and malformed bbox, oversize limit.
**Ground truth:** hand-label ≥ 30 Mattapoisett documents (created by the user/reviewer, labeling tool: simple CSV template) for evaluating each extractor and the unified output.

**Phase exit criteria (pass/fail):**
| Phase | Exit |
|-------|------|
| 1 Local stack + thin vertical slice | `compose up` healthy; one fixture doc flows fetch→bronze→silver→`/loans`→a map pin; integration test green |
| 2 Mattapoisett ingestion | ≥ 100 docs ingested; field accuracy on labeled set: principal, maturity, lender ≥ 90% (unified); extractor comparison report; coverage report with gaps |
| 3 Classifier placeholder | every processed document yields a classified loan record; `classifier_version=placeholder-all-true`; no manual review gate (see section 9) |
| 4 API+gateway+security | auth enforced, SSRF/egress tests pass, p95/cold-start budgets met, contract tests green |
| 5 Frontend polish | e2e + a11y checks (axe) pass; hover/keyboard/touch parity |
| 6 Serverless | Lambda RIE handler passes same contract tests |
| 7 IaC | `docker` module applies locally; `aws` module passes validate/tflint/checkov/test |
| 8+ | Rye, Madison, and remaining clouds, each with the same exit gates |
**Stop/pivot criteria:** if a portal's ToS forbids automation or CAPTCHAs block Obscura/Playwright, switch to manual PDF drop-folder for that town; if unified accuracy < 80% after tuning, stop and review extractor choice; CT paid images need a per-run budget cap and skip-and-flag behavior. Decision owner: the user.
**Product metrics:** time to shortlist one property vs manual workflow (target ≥ 5× faster); coverage % of mortgages in a defined date window ingested per town reported per run; ingest throughput reported (docs/hour).

## 6. Repo layout
`backend/{api,ingest,common,fetcher}`, `notebooks/` (jupytext percent `.py` + ipynb; import from `backend/common`; runnable on fixtures: 01 fetch, 02 bronze, 03 silver, 04 geocode, 05 explore view), `frontend/`, `gateway/krakend.json`, `deploy/docker/`, `infra/{modules,envs}`, `tests/{fixtures,...}`, `docs/{plans,adr}`.

## 7. Risks and dependencies
Obscura maturity (spike in Phase 1; Playwright adapter fallback); portal ToS/legal (gate before live scrape); Spark image size (local only); Docling/Unstructured model download size and licenses; DuckDB-Iceberg ↔ catalog version compatibility (pinned, tested); Census batch limits and rural match rate; legal/privacy of aggregated public-record PII (user accepts local-first). ADR per major decision; tool versions pinned (Python, Spark, Iceberg, DuckDB, tofu).

## 8. Round-2 addendum (non-blocking items accepted; Design Review Gate: all 5 APPROVED)
**MVP line:** Phases 1-3 plus a minimal map and table are the MVP; Phases 4-7 are hardening/deployment. The precision@20 go/no-go is superseded by section 9 (placeholder classifier, no review). Accuracy 80-90% band: tune and retry within a bounded budget; <80% stop. Coverage and "N not mapped" rate are reported per town; thresholds set after Phase 2 baseline. Rye/Madison get a viability check (access, CT image cost) before Phase 8.
**Measurement:** accuracy is measured as cross-extractor agreement rate (no labels required; see section 9); an optional labeled set can be added later. "5x faster" needs a timed manual baseline; cold-start/p95 measured with pinned hardware, snapshot size and N runs.
**Scoring:** weights live in versioned config, not code. Split `second_mortgage_after_purchase` into `purchase_then_mortgage_90d` and `second_lien`. `no_discharge` and any parcel-key-dependent signal are unevaluable when required data is absent (e.g. discharges not yet ingested for the window, parcel_key null). Loans recomputed per parcel/town after every instruments MERGE. Multi-instrument documents aligned across extractors by page range / recorded_ref. Branch-coverage gate 95% on unification and scoring.
**Snapshot/DuckDB:** publish = write versioned Parquet then atomically flip `manifest.json`; API re-reads manifest on TTL and on 503; partition/sort by town and bbox. Two snapshots: PII-free list and borrower detail (readable only by the detail path's role; detail is local-only unless the user opts in, enforced by an explicit tofu opt-in variable). DuckDB config: external access limited to the snapshot prefix, autoinstall/autoload off, lock_configuration after secret creation; test proves S3 read works under the lock and arbitrary file/URL reads fail. Publish is a manual, explicit command. Iceberg snapshot expiry/compaction for local tables.
**API/UI:** cursor sorted by (bridge_score desc, loan_id) encoding filter hash and snapshot id (stale cursor → 410); `total`/`unmapped_count` honor filters; `score_band` in API (0-39, 40-69, 70-100, color-blind-safe palette); problem+json gets machine `code` and per-field `errors[]`; frontend handles 429/503 Retry-After. Map loads per town up to the cap (no viewport fetching). Unmapped rows listed in the table (default sort score desc, selection synced with map); "N not mapped" links to the filtered table. Cluster: Enter zooms and focuses first child; label "12 loans, highest score 85". MSW mocks/types generated from OpenAPI examples with drift check in CI.
**Security:** SPA holds API key via local dev proxy only (v1 local, single-user); OIDC/JWT required before any non-local exposure. Source PDFs served only via authenticated route or short-TTL presigned URL with safe Content-Type/CSP; extractor models baked into images; gateway shared secret from secret manager, constant-time compare, rotated; stricter rate limit on `/loans/{id}`; audit log records key id and loan_id only (no names), with retention period; buckets block public access, KMS-encrypted.
**IaC caveat:** non-docker, non-aws modules are labeled "unverified against a real account" in the README (mock-provider tests only).
**Phase 1** exit uses fixtures only; the ToS review gates any live scrape.


## 9. User decisions after gate approval (2026-11-05)
- **Process all documents.** Ingest and process every fetched document; there is no top-N review step and no manual review gate. Drop the precision@20 gate and the hand-labeled ground-truth requirement.
- **Placeholder classifier.** The definition of a "true bridge loan" is not yet known. `classify_loan()` is a pluggable function behind a versioned config; the v0 implementation (`placeholder-all-true`) classifies EVERY loan record as a bridge loan with `bridge_score = 100` and `is_bridge_loan = true`. Signals are still computed and stored (the weights/signals in section 3.2 remain as inactive-for-classification metadata) so a real classifier can replace the placeholder without re-ingesting. UI/API show `classifier_version` and the "unverified placeholder" disclaimer.
- **Accuracy metric without labels:** report per-field cross-extractor agreement rate and null rate per town; the Phase 2 ≥90% accuracy exit is replaced by "agreement/null-rate report produced"; thresholds are set after the first baseline.
- **BEADS** initialized in this repo (`bd init`); epic tracks the work.

## 10. Plan-review clarifications (2026-11-05)
- **Superseded text:** any "Phase-1 accuracy gate", "hand-label ≥ 30 docs", "≥ 90% on labeled set" or "precision@20" wording in §5/§8 is superseded by §9.
- **Gateway auth:** KrakenD Community Edition has no API-key validator. v1 auth is a static per-key-id HS256 JWT validated by KrakenD (kid forwarded as key id for audit); the backend trusts only the gateway shared-secret header. Wherever "API key" appears, read "gateway JWT". OIDC/JWT-issuer needed before any non-local exposure.
- **Extractor invocation:** extractor containers run as one-shot jobs with mounted input/output dirs and no network; no docker socket anywhere. Fetcher hands off via a drop directory to a non-isolated raw-ingest step.
