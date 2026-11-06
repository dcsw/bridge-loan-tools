# WU11: end-to-end vertical slice, security suites, coverage gates and perf. Slow; Docker required.
# Uses ENV_FILE=.env.e2e and non-default ports (gateway 18090, web 13000) so a developer's own
# `.env` and any service on 8080/3000 are untouched. It DESTROYS the bridgeloan compose project's volumes.
E2E_ENV ?= .env.e2e
E2E_GATEWAY_PORT ?= 18090
E2E_SEC_GATEWAY_PORT ?= 18091

.PHONY: e2e e2e-security e2e-coverage perf e2e-all

e2e:  ## Clean-state vertical slice: stack -> fetch -> extract -> bronze -> silver -> publish -> API -> UI, then rerun
	uv run --frozen playwright install chromium
	RUN_E2E=1 E2E_ENV_FILE=$(E2E_ENV) uv run --frozen pytest tests/integration -v

e2e-security:  ## Security suites of WU3/4/5a/7/8 against the live e2e stack 
	GATEWAY_PORT=$(E2E_GATEWAY_PORT) $(MAKE) stack-up ENV_FILE=$(E2E_ENV)
	RUN_STACK=1 RUN_DOCKER=1 BRIDGE_ENV_FILE=$(E2E_ENV) GATEWAY_PORT=$(E2E_SEC_GATEWAY_PORT) \
		uv run --frozen pytest tests/stack tests/fetcher tests/extractors tests/api tests/gateway -q

e2e-coverage:  ## Every coverage gate (common, api, silver)
	$(MAKE) cov
	$(MAKE) api-cov
	$(MAKE) silver-cov

perf:  ## Informational perf on the 5k-row synthetic snapshot (see scripts/measure_perf.py)
	uv run --frozen python scripts/measure_perf.py --out docs/perf/phase1.json

e2e-all: e2e e2e-security e2e-coverage perf  ## Everything WU11 needs for docs/phase1-exit.md
