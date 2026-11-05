# WU7: read API. Needs the core stack only for the RUN_STACK live tests.
.PHONY: api-env api-test api-cov openapi openapi-check api-live-test

api-env:  ## Append a random GATEWAY_SECRET to .env if missing (never overwrites)
	@grep -q '^GATEWAY_SECRET=' $(ENV_FILE) 2>/dev/null || \
		( printf 'GATEWAY_SECRET=%s\n' "$$(python3 -c 'import secrets; print(secrets.token_hex(32))')" >> $(ENV_FILE) && echo "added GATEWAY_SECRET to $(ENV_FILE)" )

api-test:  ## Run the API unit tests
	uv run --frozen pytest tests/api

api-cov:  ## API coverage gate: >=80% on backend/api
	uv run --frozen pytest tests/api --cov=backend.api --cov-branch \
		--cov-report=term-missing --cov-fail-under=80

openapi:  ## Regenerate openapi.json from the app
	uv run --frozen python -m backend.api.openapi --write

openapi-check:  ## Fail if openapi.json is out of date
	uv run --frozen python -m backend.api.openapi --check

api-live-test:  ## Live MinIO tests: needs `make stack-up` (or just minio) first
	RUN_STACK=1 uv run --frozen pytest tests/api -m stack
