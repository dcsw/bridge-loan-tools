# ADR-0006: DuckDB lockdown for the read API

Status: accepted (WU7). DuckDB pinned to **2.0.0** (ADR-0002); `httpfs` pre-installed at image build.

## Context
The API answers queries by reading the published snapshot (PII-free list Parquet and a separate
borrower-detail Parquet, plus `manifest.json`) with DuckDB. A SQL-capable engine inside a service
that handles untrusted request parameters must not be able to read other files, other buckets,
arbitrary URLs, or be reconfigured. All queries are also fully parameterized and no SQL text is
built from request input; the lockdown is defense in depth.

## Spike results (verified against the live MinIO from WU3, DuckDB 2.0.0)
`tests/api/test_lockdown_live.py` (RUN_STACK=1) records the outcome:

| Question | Result |
|---|---|
| Does `allowed_directories` accept an `s3://bucket/prefix/` entry with `enable_external_access=false`? | **Yes.** Reads under the prefix work; a sibling prefix is rejected (`test_spike_allowed_directories_with_s3_prefix`). |
| Does `allowed_paths` (exact objects) accept `s3://` URIs? | **Yes.** Used by the API, because it is tighter than a prefix: only the exact objects named in the manifest are readable. A sibling file in the same prefix, another prefix, another bucket, globs, local files and http(s) URLs all fail. |
| Is the S3 secret's `SCOPE` the boundary? | No. With a bucket-wide secret, `allowed_paths` alone still blocks everything else (`test_allowed_paths_is_the_boundary_not_the_secret_scope`). |
| Is the ordering requirement real? | Yes. `LOAD httpfs` and `CREATE SECRET` run BEFORE `SET enable_external_access=false` and `SET lock_configuration=true`; afterwards every `SET`/`RESET` (including `allowed_*`, `autoinstall_known_extensions`, `lock_configuration`) fails, and `INSTALL` fails. |

Local-file snapshots (tests, local compose without MinIO) use the same code path with plain
paths in `allowed_paths`; `tests/api/test_lockdown.py` proves a snapshot read works and arbitrary
file or URL reads, writes (`COPY ... TO`), `ATTACH`, and reconfiguration all fail.

### Settings applied by `backend/api/lockdown.py:open_locked`
1. `autoinstall_known_extensions=false`, `autoload_known_extensions=false` at connect.
2. `LOAD httpfs` (only for remote paths; the extension is baked into the image, never INSTALLed at runtime).
3. `CREATE SECRET (TYPE S3 ... SCOPE '<snapshot prefix>')`.
4. `SET allowed_paths = [<exact files>]`.
5. `SET enable_external_access = false`, then `SET lock_configuration = true`.

## Separate list and detail paths (DoD 9)
The store opens two independently locked connections per snapshot:
- **list connection**: `allowed_paths = [list file]` (the manifest is read by a short-lived third
  connection allowed only the manifest). The list file is loaded into an in-memory table once per
  snapshot; list queries touch only that table. There is no code path from the list connection to
  the borrower file: it fails at the engine (`test_list_connection_cannot_read_borrower_file`).
- **detail connection**: `allowed_paths = [detail file]`; reads on demand by `loan_id`.
- Each gets its own `S3Credentials` (`LIST_S3_*` vs `DETAIL_S3_*` env). **Local compose reuses the
  MinIO root credentials for both** (WU3 provisions no per-service users); in cloud deployments the
  two secrets must map to distinct IAM principals (list: list prefix only; detail: detail prefix
  only). That IAM split is not testable locally and is NOT verified here.

## Fallbacks and honest gaps
- Direct Iceberg-catalog reads (`ATTACH ... TYPE ICEBERG`, Lakekeeper) are **not implemented** in the
  API: per the orchestrator's WU7 scope the API reads only the published snapshot. The plan's
  "local direct-Iceberg read path" DoD sub-item is therefore deferred; with
  `enable_external_access=false` an Iceberg REST attach would additionally require allow-listing
  the catalog endpoint, which DuckDB 2.0.0 does not express (there is no host allow-list), so a
  future direct-Iceberg local mode would have to run unlocked with compose network restrictions.
  This is documented rather than faked.
- `semantic_loans_v1` (WU5b `backend/ingest/sql/semantic_loans_v1.sql`) is not loaded: the snapshot
  already is the materialized view and WU5b has not landed.
- The sha256 of the borrower detail file cannot be verified by the API (the list connection must
  not read it); only the list file hash and row counts are checked. The publish step (WU6) owns
  detail integrity.

## Snapshot contract additions (for WU6 publish)
The API reads the WU2 snapshot layout unchanged and tolerates two optional additions, which the
publish step should emit so the API can serve them:
- detail Parquet column `fields_json` (string): JSON object `{field: {value, source_extractor,
  confidence, disagreement}}`. Absent -> `fields` is `{}` (not fabricated).
- `manifest.json` key `coverage`: `{town: {documents_fetched, gaps: {paid, blocked, failed},
  last_ingested_at}}`. Absent -> `/coverage` derives `documents_fetched` and `last_ingested_at`
  from the list file and reports `gaps: null` (unknown, not zero).

## Audit log and retention (DoD 14)
Detail and source-PDF access appends one JSON line `{ts, key_id, loan_id, action}` to
`AUDIT_LOG_PATH` (mode 0600; fsync). **No names or other PII.** If the write fails the request
fails closed (503 `audit_unavailable`) and no borrower data is returned.

**Retention: `AUDIT_RETENTION_DAYS` (default 365, must be >= 1).** Entries older than this are
purged at process start and at most once per 24 h while writing; unparseable lines are never
deleted. Tested in `tests/api/test_audit.py`. Design 3.5 says "retention = until user deletes" for
data and asks for a retention period on the audit log; 365 days is the default chosen here and is
a user decision to confirm.

## Logging
All API and uvicorn loggers share one `RedactionFilter` (`backend.common.logging`). Borrower names
are registered into it the moment they are read from the detail file, so later log lines and
exception tracebacks are scrubbed; `borrower:`/`grantor=` style key-values are scrubbed even for
names the filter has not seen. A catch-all middleware logs unhandled exceptions itself through the
redacting logger and returns a generic 500 problem, so uvicorn never prints a raw traceback.
Verified end to end over a real uvicorn server in `tests/api/test_logging_redaction.py`.

## Other decisions
- Gateway trust: `X-Gateway-Secret` compared with `hmac.compare_digest`; forwarded `X-Key-Id`
  must match `[A-Za-z0-9._-]{1,64}`. `/health/*` is open (no data) so container and gateway
  health checks work; `/docs`, `/redoc`, `/openapi.json` are not served.
- Query-parameter validation errors are 400 (`invalid_parameters`); 422 is reserved for bodies
  (none exist yet). A cursor from another snapshot is 410 `stale_cursor`; a cursor reused with
  different filters is 400 `cursor_filter_mismatch`.
- `unmapped_count` honors every filter except `bbox` (unmapped rows have no geometry and could
  never satisfy a spatial filter, so the "N not mapped" indicator ignores it).
- Source PDFs are always served as `application/pdf` (never the object's metadata type) as
  `attachment` with `Content-Security-Policy: default-src 'none'; sandbox`,
  `X-Content-Type-Options: nosniff`, `Cache-Control: private, no-store`; the first bytes must be
  `%PDF-` or the response is a 502.
