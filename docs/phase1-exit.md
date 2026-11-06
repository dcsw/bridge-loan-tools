# Phase 1 exit

Evidence for each exit criterion of `docs/plans/2026-11-05-phase1-implementation-plan.md` (WU11 DoD).
Run on 2026-11-06, WSL2 / Docker, `.env.e2e`, fixtures only (no live portal, no Census call).

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1a | Clean state → core stack healthy | PASS | `make e2e`: `clean()` runs `compose down -v` for the project and removes the `data/` hand-off dirs, failing if any survive, then `make stack-up`; `test_core_stack_is_healthy` asserts minio, postgres, lakekeeper, api, krakend, gateway are `healthy`. |
| 1b | fetcher → raw-ingest → extractors → bronze → silver → publish | PASS | `tests/integration/pipeline.py` runs the same make targets a developer uses (fetcher on the isolated `fetch` network against the fake portal, `fetcher-ingest`, `extract-all` for pymupdf/docling/unstructured, `ingest-run`, `silver-manifests`, `silver-run`, `publish`). Recorded-geocoder mode: the geocode cache is seeded with the recorded `matched` response, so no network call; the geocoder itself is not exercised here (its tests are in `tests/silver`), and the asserted coordinates are the seeded ones. The fake portal service is profile `e2e` only, not part of `core`. |
| 1c | API via KrakenD returns the fixture loan with `is_bridge_loan=true` | PASS | `test_api_returns_fixture_loan_as_bridge_loan`: 1 feature, `is_bridge_loan: true`, `classifier_version: placeholder-all-true`, geocode `matched`, coordinates `[-70.8161, 41.6532]`; `test_gateway_rejects_missing_token`: no JWT → 401. |
| 1d | Browser sees the pin and the table row | PASS | `test_ui_shows_pin_and_table_row`: Chromium (Python Playwright) against the real web image → nginx → gateway; 1 `button.pin` labelled with the fixture address, 1 table row with the lender, zero console errors. This is a live-stack check; the MSW-mock suite in `frontend/e2e` is separate. |
| 2 | Full rerun yields identical results | PASS | `test_full_rerun_is_identical`: second pipeline run over the same state; `/loans` and `/coverage` responses equal the first run. Scope: the API surface only (`/loans`, `/coverage`); bronze/silver/MinIO row counts are not compared. Excluded as run-time values: `generated_at`, `snapshot_id`, `published_at`, per-town `last_ingested_at`. |
| 3 | Security tests of WU3/4/5a/7/8 pass alongside the slice | PASS | Same session and stack, separate invocation: `make e2e-security` against the e2e stack: **665 passed, 1 skipped** (`tests/stack tests/fetcher tests/extractors tests/api tests/gateway`, with `RUN_STACK=1 RUN_DOCKER=1`). |
| 4 | Perf on the 5k-row snapshot, pinned conditions (informational) | RECORDED | `docs/perf/phase1.json` (`make perf`): 5000 rows, 50 runs + 5 warm-up per endpoint, in-process Starlette TestClient, i5-12450H (4 logical cores visible), 15.6 GiB, DuckDB 2.0.0. `/loans` default page p50 18 ms / p95 49 ms; detail p50 14 ms; cold first request ~105 ms. Not a gate. Measured on a laptop that was also running Docker, so treat as an order of magnitude. |
| 5 | All coverage gates met | PASS (Python) | `make e2e-coverage`: backend/common 99.7 % (≥80), classify + signals 100 % branch (≥95), backend/api 92.9 % (≥80), silver modules 91.6 % (≥80), `unify` 100 % branch (≥95). |
| 6 | This document ticks every criterion with evidence | PASS | This file. |
| 7 | BEADS children closed | PASS (after commit) | WU1-WU11 closed under epic `bridge-loan-tools-7hn` (`bd list`). |

Also: `make test` (unit, no Docker) 939 passed, 9 skipped; `make lint` clean; `make notebooks-test` 5 notebooks pass in the container.

## Not verified in this run

- **Frontend unit/e2e suites (Vitest, MSW Playwright, axe)** were not re-run: this machine has no Node. They were exercised when WU9 landed. Criterion 1d does not depend on Node.
- **Frontend coverage**: the plan defines no numeric frontend coverage gate; none was measured.
- One test is skipped in the security run (as in the suite's own configuration); it was not investigated.

## Defects found and fixed while building WU11

- Bronze quarantined every record in containers: extractors write `0600` files as the host user, unreadable to the ingest container's uid 10001. `make ingest-run` now makes the extractor output world-readable (synthetic data only; **Phase 2 must replace this with an ACL/group or host-uid run before real documents are extracted**, since it exposes extracted text to other local users) and creates the bind-mounted `data/manifests` and `data/silver-work` so Docker does not create them as root.
- `tests/stack/test_compose_static.py` pinned the compose include list and broke when WU10 added `compose.notebooks.yaml` (found by the security run, not by the WU10 checks).
- Live tests hard-coded `.env`; they now honour `BRIDGE_ENV_FILE`.

## Human checkpoint

Per the plan: after Phase 1 exit, decide on Phase 2. Phase 2 (live Mattapoisett ingest) is **subject to a ToS review** of the portal and an egress allow-list (ADR-0003); neither has been done.
