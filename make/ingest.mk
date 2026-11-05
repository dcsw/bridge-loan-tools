# Bronze (Spark) ingest (WU5b). Input: data/extract-out/<extractor>/*.json (read-only mount).
COMPOSE_INGEST = docker compose -p bridgeloan --env-file $(ENV_FILE) -f deploy/docker/compose.yaml --profile core --profile ingest

.PHONY: ingest-build ingest-run ingest-smoke ingest-test

ingest-build:  ## Build the Spark ingest image (Java 17, pyspark, Iceberg jars baked in)
	$(COMPOSE_INGEST) build ingest

ingest-run:  ## Load data/extract-out into bronze + quarantine Iceberg tables (needs `make stack-up`)
	@mkdir -p data/extract-out
	$(COMPOSE_INGEST) run --rm ingest backend.ingest.bronze $(if $(EXTRACTORS),--extractors $(EXTRACTORS))

ingest-smoke:  ## Spark -> REST catalog create/append/read smoke (needs `make stack-up`)
	$(COMPOSE_INGEST) run --rm ingest backend.ingest.smoke

ingest-test:  ## Bronze tests; the live ones need Docker and the stack: RUN_STACK=1 make ingest-test
	uv run --frozen pytest tests/ingest
