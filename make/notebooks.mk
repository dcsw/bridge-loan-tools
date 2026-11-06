# Notebooks (WU10). Sources: notebooks/*.py (jupytext percent); .ipynb are generated, outputs stripped.
# Execution environment: the `notebooks` compose service (Java 17 + Spark + nbmake), fixtures only.
# Uses compose.notebooks.yaml standalone: no stack secrets (.env) and no network are needed.
COMPOSE_NOTEBOOKS = docker compose -p bridgeloan-notebooks -f deploy/docker/compose.notebooks.yaml --profile notebooks
NOTEBOOK_SRC = $(wildcard notebooks/[0-9]*.py)

.PHONY: notebooks-sync notebooks-requirements notebooks-build notebooks-test notebooks-static-test

notebooks-sync:  ## Regenerate notebooks/*.ipynb from the jupytext .py sources (no outputs)
	uv run --frozen jupytext --to ipynb $(NOTEBOOK_SRC)

notebooks-requirements:  ## Re-export the hash-pinned notebooks image requirements from uv.lock
	uv export --frozen --package bridge-loan-ingest --only-group notebooks --no-emit-workspace \
	  --no-header -o deploy/docker/notebooks/requirements.txt

notebooks-build:  ## Build the notebooks image
	$(COMPOSE_NOTEBOOKS) build notebooks

notebooks-test: notebooks-static-test  ## Execute every notebook end-to-end on fixtures (nbmake) inside the container
	$(COMPOSE_NOTEBOOKS) run --rm notebooks pytest --nbmake -p no:cacheprovider notebooks

notebooks-static-test:  ## Host checks: sync, stripped outputs, import-only (no duplicated logic)
	uv run --frozen pytest tests/notebooks
