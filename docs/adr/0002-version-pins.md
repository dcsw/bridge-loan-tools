# ADR-0002: Version pins

Status: partial (WU1 + WU3 recorded; Spark/Iceberg-Spark, KrakenD, Node pinned by later units)

## Pinned (WU1)
- Python 3.12, managed with uv (workspace, root `uv.lock`)
- ruff 0.17.3 (pre-commit and lockfile)
- gitleaks v8.30.1 (pre-commit and CI)

## Pinned (WU3, local core stack)
All images are digest-pinned (`scripts/check_image_digests.py` enforces this).

| Component | Pin |
|---|---|
| Lakekeeper (Iceberg REST catalog) | `quay.io/lakekeeper/catalog:v0.14.2@sha256:9ad8de17...4b85` |
| PostgreSQL | `postgres:17-alpine@sha256:266d3c25...7e3` (17.11) |
| MinIO server | `cgr.dev/chainguard/minio:latest@sha256:cee9a8e3...886b` = RELEASE.2026-10-23T20-06-51Z |
| busybox (static, for MinIO healthcheck only) | `busybox:1.37-musl@sha256:f6a95d61...8478` |
| Bootstrap job base | `python:3.12-slim@sha256:4840d514...3a54` + boto3 1.43.117, requests 2.34.2 |
| Host test deps (root dev group) | pyiceberg 0.12.0, DuckDB 2.0.0, boto3 1.43.117, pyarrow 26.0.1 |
| Docker Compose | v5.6.0 (tested); see "Compose `include`" below |

MinIO note: the `minio/minio` Docker Hub and `quay.io/minio/minio` repositories no longer
serve pullable release tags, so the Chainguard build of the same upstream release is used,
pinned by digest (tag `latest` is only a label; the digest is authoritative). The upstream
image is distroless, so `deploy/docker/minio/Dockerfile` copies in a static busybox solely to
provide a real in-container healthcheck.

### Compose `include` and `required: false`
`include: - path: ... required: false` is NOT honored by Compose v5.6.0: with a missing file
`docker compose config` fails with "no such file or directory" (verified; the key is silently
ignored). The plan's stated fallback is therefore used: `compose.yaml` keeps the
`required: false` entries (harmless, forward compatible) and WU3 ships stub fragments
(`services: {}`) for `compose.{fetcher,extractors,ingest,api,gateway,web}.yaml`. Each owning
unit replaces its stub with the real fragment.

### Lakekeeper credential mode
Warehouse `bridgeloan` is created with `sts-enabled: true` against MinIO. Verified against the
running stack (`X-Iceberg-Access-Delegation` header on create/load table):
- `vended-credentials`: works. Lakekeeper returns short-lived STS credentials
  (access key, secret, session token) plus `s3.path-style-access=true`.
- `remote-signing`: also available (`s3.signer=S3V4RestSigner`, signer URI derived from the
  request Host, so no `LAKEKEEPER__BASE_URI` is set).
- Decision: vended credentials are the default for in-network clients (Spark, API).
  Host-side tests and scripts use the MinIO root credentials with an explicit client-side
  endpoint override instead.

### Endpoint override (important)
Lakekeeper returns the warehouse endpoint `http://minio:9000/` in the table config, and
pyiceberg gives server config precedence over client config, so a plain `s3.endpoint`
catalog property is ignored on the host. `scripts/maintenance/expire_snapshots.py:with_endpoint`
rebuilds the table FileIO with the reachable endpoint (path-style). DuckDB reads use an S3
secret with `ENDPOINT '127.0.0.1:9000'`, `URL_STYLE 'path'`.

### DuckDB
DuckDB 2.0.0 with the `iceberg` extension supports REST catalog attach. Verified:
`ATTACH 'bridgeloan' AS lk (TYPE ICEBERG, ENDPOINT 'http://127.0.0.1:8181/catalog',
AUTHORIZATION_TYPE 'none', ACCESS_DELEGATION_MODE 'none')` then `SELECT` from a table written
by pyiceberg. `ACCESS_DELEGATION_MODE 'none'` is required on the host because vended
credentials would carry the unresolvable internal `minio:9000` endpoint.

## To be pinned
Spark, Iceberg-Spark runtime, KrakenD, Node (later units).

## Pinned (local dev tools, `make tools`)
Installed by `scripts/install_tools.sh` into the gitignored `./.tools/bin`, each download verified
against the checksums file of the same release (integrity only, no signature check). linux/amd64.

| Tool | Version |
|---|---|
| gitleaks | 8.30.1 |
| OpenTofu (`tofu`) | 1.13.8 |
| Trivy | 0.76.2 |

Notes: the WU1 hook test still skips locally because pre-commit builds the gitleaks hook with Go
(not installed). Direct `gitleaks detect` runs clean on history via `.gitleaksignore` (one entry:
the deliberate fake key committed in 553e956, now assembled at runtime in the test).

## Decision: Postgres, pg_lake and DuckDB (2026-11-05, user delegated the call)
- Postgres here is Lakekeeper's catalog backend; it holds catalog metadata, not table data.
- **pg_lake** (Snowflake-Labs, latest release v3.6.0) is Postgres + a `pgduck_server` process that runs
  DuckDB underneath, and it publishes no prebuilt image (its Docker setup builds from source via Task).
  It therefore does not remove DuckDB, it embeds it.
- **Decision:** keep DuckDB as the read engine for the API (serverless-friendly, reads the published
  snapshot with no catalog or database, per design §3.3) and for local Iceberg reads. Keep stock
  `postgres:17-alpine` for Lakekeeper. Treat pg_lake as an optional, later local service that gives a
  Postgres SQL interface over the same Iceberg tables; it is not on the Phase 1 critical path and
  needs a source build to adopt. Revisit when a prebuilt image exists or if you want Postgres SQL
  access to the semantic view.

## Addendum (WU5b, 2026-11-05): Spark / Iceberg / Java / Python pairing
Append-only; added by the Bronze unit. The ingest image (`deploy/docker/ingest/Dockerfile`) is the only place
Spark runs (`local[*]`).

| Component | Pin |
|---|---|
| Base image | `eclipse-temurin:17-jre-noble@sha256:8ee89ca3...ff1d` (Java 17, Ubuntu 24.04) |
| Python | 3.12 (Ubuntu noble `python3`, in `/opt/venv`; matches `requires-python`) |
| PySpark / Spark | `pyspark==4.0.5` (bundles Spark 4.0.5, Scala 2.13), hash-pinned in `deploy/docker/ingest/requirements.txt` (exported from `uv.lock`, group `spark`) |
| Iceberg Spark runtime | `iceberg-spark-runtime-4.0_2.13-1.12.0.jar`, sha256 `3f50e93e...8306` |
| Iceberg AWS bundle | `iceberg-aws-bundle-1.12.0.jar`, sha256 `dd7e49ff...d957` |
| Catalog | Lakekeeper v0.14.2 REST, `X-Iceberg-Access-Delegation: vended-credentials` |

Jars are downloaded at image build time, verified with `sha256sum -c`, and placed in pyspark's `jars/`
directory; the job never uses `--packages`/`spark.jars.packages`, so runtime needs no Maven access.
Spark 4.0 supports Java 17 and Python 3.12; the `-4.0_2.13` runtime artifact is the Iceberg build for
Spark 4.0/Scala 2.13.

Verified by `tests/ingest/test_ingest_live.py::test_spark_iceberg_smoke_readable_by_pyiceberg_and_duckdb`
(`RUN_STACK=1`): Spark creates a format-version 2 table through the REST catalog, appends in two commits,
reads it back; then pyiceberg 0.12.0 (2 snapshots, rows equal) and DuckDB 2.0.0 (`ATTACH ... TYPE ICEBERG`,
rows equal incl. NULL) read the same Spark-written table. Bronze tables (MERGE INTO, copy-on-write) are
read back through DuckDB in the same suite.

Operational notes found while verifying:
- The runtime uid (10001) must have a passwd entry or Hadoop `UserGroupInformation` fails with
  "invalid null input: name" (and Ivy sees `user.home=?`); the image adds a `ingest` user.
- The `/tmp` tmpfs needs `exec`: zstd-jni extracts and maps a native `.so` there. It stays `nosuid`,
  and the container runs non-root, `cap_drop: ALL`, read-only root FS.
- Network: the job sits only on the `ingest` network (`internal: true`); `minio` and `lakekeeper` are
  additionally attached to it. Credentials are vended by Lakekeeper, so the container holds no secrets.
