# Fetcher (WU4). Fetch runs on the isolated `fetch` network; raw-ingest runs on the host.
COMPOSE_FETCHER = docker compose -p bridgeloan --env-file $(ENV_FILE) -f deploy/docker/compose.yaml --profile ingest

.PHONY: fetcher-build fetcher-run fetcher-ingest fetcher-test fakeportal manual-drop

fetcher-build:  ## Build the fetcher image (Playwright + Chromium baked in)
	@mkdir -p data/drop
	$(COMPOSE_FETCHER) build fetcher

fetcher-run:  ## Fetch URLs into data/drop: make fetcher-run URLS=path/in/container TOWN=<town>
	@mkdir -p data/drop
	$(COMPOSE_FETCHER) run --rm fetcher --urls $(URLS) --town $(TOWN)

fetcher-ingest:  ## Upload data/drop to MinIO raw store + manifest rows (needs `make stack-up`)
	@set -a; . ./$(ENV_FILE); set +a; \
	uv run --frozen python -m backend.fetcher.raw_ingest --drop data/drop

fetcher-test:  ## Fetcher unit tests plus Docker-backed isolation/dedupe tests (needs the stack up)
	RUN_STACK=1 uv run --frozen pytest tests/fetcher

fakeportal:  ## Run the local fake portal on 127.0.0.1:8080 (synthetic PDFs only)
	uv run --frozen python -m tests.fakeportal.server --port 8080

manual-drop:  ## Hand-downloaded PDF -> data/drop: make manual-drop FILE=x.pdf URL=<ref> TOWN=<town> | GAP=paid|blocked|failed URL=.. TOWN=..
	@test -n "$(URL)" && test -n "$(TOWN)" && { test -n "$(FILE)" || test -n "$(GAP)"; } || \
		{ echo "usage: make manual-drop FILE=x.pdf URL=<ref> TOWN=<town>   (or GAP=paid|blocked|failed instead of FILE)"; exit 2; }
	uv run --frozen python -m backend.fetcher.manual_drop --url "$(URL)" --town "$(TOWN)" \
		$(if $(FILE),--file "$(FILE)",--gap "$(GAP)") $(if $(DROP),--drop "$(DROP)")
