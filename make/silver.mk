# Silver, loans, semantic view, geocode and snapshot publish (WU6).
#
# Flow:  make stack-up && make ingest-run            (bronze)
#        make silver-manifests                       (WU4 manifest rows from MinIO `raw` -> data/manifests)
#        make silver-run                             (Spark: unify -> MERGE instruments -> loans)
#        make silver-geocode && make silver-run      (only if silver-run reported geocode_pending > 0)
#        make publish                                (MANUAL: materialize semantic_loans_v1 -> snapshots bucket)
# `publish` is never run automatically by any other target.
COMPOSE_SILVER = docker compose -p bridgeloan --env-file $(ENV_FILE) -f deploy/docker/compose.yaml --profile core --profile ingest
SILVER_WORK_DIR ?= data/silver-work
SILVER_MANIFEST_DIR ?= data/manifests
SILVER_COV_MODULES = --cov=backend.ingest.unify --cov=backend.ingest.loans --cov=backend.ingest.geocode \
	--cov=backend.ingest.publish --cov=backend.ingest.views --cov=backend.ingest.silver_job

.PHONY: silver-manifests silver-run silver-geocode publish silver-maintain silver-test silver-cov

silver-manifests:  ## Export WU4 manifest rows from MinIO raw/manifest/ to data/manifests (host, needs stack)
	@set -a; . ./$(ENV_FILE); set +a; \
	uv run --frozen python -m backend.ingest.publish --export-manifests $(SILVER_MANIFEST_DIR)

silver-run:  ## Spark silver job in the ingest container (needs stack-up + ingest-run + silver-manifests)
	@mkdir -p $(SILVER_WORK_DIR) $(SILVER_MANIFEST_DIR) && chmod 0777 $(SILVER_WORK_DIR)
	$(COMPOSE_SILVER) run --rm ingest backend.ingest.silver_job

silver-geocode:  ## Host step (INTERNET): resolve queued addresses via the Census geocoder into the cache
	@test -f $(SILVER_WORK_DIR)/geocode-requests.txt || { echo "nothing queued: run silver-run first"; exit 1; }
	uv run --frozen python -m backend.ingest.geocode \
		--requests $(SILVER_WORK_DIR)/geocode-requests.txt --cache $(SILVER_WORK_DIR)/geocode-cache.jsonl

publish:  ## MANUAL: publish semantic_loans_v1 as versioned Parquet to s3://snapshots/current, then flip manifest.json
	@set -a; . ./$(ENV_FILE); set +a; \
	uv run --frozen python -m backend.ingest.publish --bucket snapshots --prefix current

silver-maintain:  ## Compact + expire old Iceberg snapshots of silver tables (RETAIN=1 by default)
	@set -a; . ./$(ENV_FILE); set +a; \
	for t in silver.instruments silver.loans; do \
	  uv run --frozen python scripts/maintenance/expire_snapshots.py --table $$t --compact --retain-last $(or $(RETAIN),1); \
	done

silver-test:  ## Silver tests; the live Spark one needs Docker and the stack: RUN_STACK=1 make silver-test
	uv run --frozen pytest tests/silver

silver-cov:  ## Coverage gates: >=80% overall on the silver modules, >=95% branch on unify
	uv run --frozen pytest tests/silver $(SILVER_COV_MODULES) --cov-branch \
		--cov-report=term-missing --cov-fail-under=80
	uv run --frozen coverage report --include='backend/ingest/unify.py' --fail-under=95
