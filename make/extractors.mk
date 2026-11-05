# Sandboxed PDF extractors (WU5a). Hand-off is host dirs only: data/extract-in -> data/extract-out/<extractor>.
EXTRACTOR ?= pymupdf
EXTRACTOR_LIST := pymupdf docling unstructured
EXTRACT_COMPOSE = docker compose -p bridgeloan-extract -f deploy/docker/compose.extractors.yaml --profile extract
EXTRACT_ENV = EXTRACT_UID=$$(id -u) EXTRACT_GID=$$(id -g)

.PHONY: extractors-build extract extract-all extractors-test extractors-docker-test extractors-requirements regen-goldens

extractors-build:  ## Build extractor image(s): make extractors-build [EXTRACTOR=pymupdf]
	$(EXTRACT_COMPOSE) build extract-$(EXTRACTOR)

extract:  ## Run one extractor over data/extract-in (read-only) into data/extract-out/<EXTRACTOR>
	@mkdir -p data/extract-in data/extract-out/$(EXTRACTOR)
	$(EXTRACT_ENV) $(EXTRACT_COMPOSE) run --rm extract-$(EXTRACTOR)

extract-all:  ## Run every extractor
	@for e in $(EXTRACTOR_LIST); do $(MAKE) extract EXTRACTOR=$$e || exit 1; done

extractors-test:  ## Host-side extractor unit tests (no Docker)
	uv run --frozen pytest tests/extractors

extractors-docker-test:  ## Container tests: make extractors-docker-test [EXTRACTORS=pymupdf,docling]
	RUN_DOCKER=1 uv run --frozen pytest tests/extractors

extractors-requirements:  ## Re-export hash-pinned per-extractor requirements from uv.lock
	@for e in $(EXTRACTOR_LIST); do \
	  uv export --frozen --package bridge-loan-ingest --only-group $$e --no-emit-workspace --no-header \
	    -o backend/ingest/extractors/$$e/requirements.txt; \
	done

regen-goldens:  ## Reviewed/manual: dry-run golden regeneration: make regen-goldens EXTRACTOR=pymupdf (add WRITE=1)
	uv run --frozen python scripts/regen_goldens.py --extractor $(EXTRACTOR) $(if $(WRITE),--write)
