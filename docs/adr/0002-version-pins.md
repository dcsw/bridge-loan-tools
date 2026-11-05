# ADR-0002: Version pins

Status: stub (completed by WU3 and later units)

## Pinned now (WU1)
- Python 3.12, managed with uv (workspace, root `uv.lock`)
- ruff 0.17.3 (pre-commit and lockfile)
- gitleaks v8.30.1 (pre-commit and CI)

## To be pinned
Spark, Iceberg, Lakekeeper, DuckDB, Postgres, KrakenD, Node, Docker Compose (`include:` with
`required: false`), Lakekeeper credential mode.
