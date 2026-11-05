# Local core stack (WU3): MinIO, Postgres, Lakekeeper. Run `make env` first.
ENV_FILE ?= .env
COMPOSE_CORE = docker compose -p bridgeloan --env-file $(ENV_FILE) -f deploy/docker/compose.yaml --profile core

.PHONY: env stack-up stack-down stack-logs stack-test expire-snapshots

env:  ## Generate .env with random credentials (never overwrites)
	@python3 deploy/docker/gen_env.py $(ENV_FILE)

stack-up: env  ## Start the core stack and wait until healthy
	$(COMPOSE_CORE) up -d --build --wait
	$(COMPOSE_CORE) wait bootstrap

stack-down:  ## Stop the core stack and delete its volumes
	$(COMPOSE_CORE) down -v --remove-orphans

stack-logs:  ## Tail core stack logs
	$(COMPOSE_CORE) logs -f --tail=100

stack-test:  ## Run the Docker-backed stack tests
	RUN_STACK=1 uv run --frozen pytest tests/stack

expire-snapshots:  ## Expire/compact a table: make expire-snapshots TABLE=ns.table [RETAIN=1]
	@set -a; . ./$(ENV_FILE); set +a; \
	uv run --frozen python scripts/maintenance/expire_snapshots.py --table $(TABLE) --retain-last $(or $(RETAIN),1)
