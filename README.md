# bridge-loan-tools

Identifies bridge-loan candidates from public mortgage records: fetch → extract → bronze → silver →
published snapshot → read API behind a gateway → map and table UI. Phase 1 is a local, fixture-driven
vertical slice (synthetic documents only; no live portal, no Census call).

Design: `docs/plans/2026-11-05-bridge-loan-platform-design.md`. Plan: `docs/plans/2026-11-05-phase1-implementation-plan.md`.
Phase 1 exit evidence: `docs/phase1-exit.md`. Decisions: `docs/adr/`.

## Prerequisites

Docker with Compose ≥ 2.20, [uv](https://docs.astral.sh/uv/), Python 3.12, GNU make. Node ≥ 24 only for
frontend development (`make web-install`); the e2e UI check uses Python Playwright (`make e2e` runs
`playwright install chromium` itself).

```bash
make sync            # install dev dependencies (uv sync --frozen --all-packages)
make test            # unit tests (no Docker)
make lint
```

## Run the whole slice

```bash
make e2e             # ~4 min: clean state -> stack -> pipeline -> API via gateway -> UI -> rerun is identical
make e2e-security    # WU3/4/5a/7/8 security suites against the e2e stack (also uses project `bridgeloan`; separate invocation)
make e2e-coverage    # all coverage gates
make perf            # informational perf on the 5k-row snapshot -> docs/perf/phase1.json
```

`make e2e` uses its own `.env.e2e` (gitignored, random local credentials), gateway port 18090 and web
port 13000, so your `.env` and anything on 8080/3000 are untouched. **It destroys the `bridgeloan` compose
project's volumes.**

## Run the pipeline by hand

```bash
make stack-up                          # MinIO, Postgres, Lakekeeper, API, KrakenD, gateway (127.0.0.1 only)
make fetcher-run URLS=... TOWN=...     # isolated fetch network -> data/drop (live hosts need FETCHER_PORTAL_HOSTS;
                                       # the fixture flow needs the e2e profile: see tests/integration/pipeline.py)
make fetcher-ingest                    # data/drop -> MinIO raw store + manifest rows
cp data/drop/ingested/*.pdf data/extract-in/ && make extract-all
make ingest-run                        # bronze (+ quarantine)
make silver-manifests && make silver-run   # unify -> instruments -> loans
make publish                           # MANUAL: semantic_loans_v1 -> versioned Parquet snapshot
make gateway-token KEY_ID=me           # JWT for the gateway
```

## Where the UI is

Everything binds to 127.0.0.1 only.

| How you run it | URL |
|---|---|
| Web image: `make web-up` (port set by `WEB_PORT`) | http://127.0.0.1:3000 |
| `make e2e` stack (`WEB_PORT=13000`; may be torn down after the run) | http://127.0.0.1:13000 |
| Dev server: `make web-dev` (needs Node; `BRIDGE_API_URL`, `BRIDGE_API_JWT`) | http://127.0.0.1:5173 |
| Offline mock build used by the Playwright/MSW suite | http://127.0.0.1:4173 |

The web image has nginx inject the gateway JWT server-side (`WEB_API_JWT`, minted with
`make gateway-token`); it is never in the browser bundle. The API itself is only reachable through the
gateway (port 8080 by default, 18090 in the e2e stack).

Notebooks (fixtures only, in a no-network container): `make notebooks-test`.
Every target is listed by `make help`.

## Layout

`backend/{common,fetcher,ingest,api}` Python packages · `gateway/` KrakenD + edge proxy · `frontend/` React/TS/d3 ·
`deploy/docker/` compose files and images · `make/` per-unit Makefile fragments · `notebooks/` · `tests/`.
