.DEFAULT_GOAL := help
.PHONY: help sync test lint format audit

help:  ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

sync:  ## Install dev dependencies
	uv sync --frozen

test:  ## Run unit tests
	uv run --frozen pytest

lint:  ## Ruff lint + format check
	uv run --frozen ruff check tests scripts backend
	uv run --frozen ruff format --check tests scripts backend

format:  ## Auto-format
	uv run --frozen ruff check --fix tests scripts backend
	uv run --frozen ruff format tests scripts backend

audit:  ## Audit python dependencies
	uv run --frozen pip-audit

# Per-unit fragments (owned by later work units); missing files are fine.
-include make/*.mk
