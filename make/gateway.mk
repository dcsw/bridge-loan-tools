# WU8: KrakenD gateway (+ thin edge). Needs `make env` (GATEWAY_SECRET, JWT_SIGNING_KEY).
.PHONY: gateway-check gateway-token gateway-test gateway-live-test

KRAKEND_IMAGE = $(shell sed -n 's|^ *image: \(krakend:[^ ]*\)$$|\1|p' deploy/docker/compose.gateway.yaml)

gateway-check:  ## Validate gateway/krakend.json with the pinned KrakenD image (built-in schema, offline)
	docker run --rm -v "$(CURDIR)/gateway:/gateway:ro" $(KRAKEND_IMAGE) \
		krakend check -n -c /gateway/krakend.json

gateway-token:  ## Mint a gateway JWT: make gateway-token KEY_ID=alice [TTL_DAYS=30]
	@test -n "$(KEY_ID)" || { echo "usage: make gateway-token KEY_ID=<id> [TTL_DAYS=30]"; exit 2; }
	@uv run --frozen python gateway/mint_token.py --key-id "$(KEY_ID)" \
		--ttl-days "$(or $(TTL_DAYS),30)" --env-file $(ENV_FILE)

gateway-test:  ## Gateway unit + static tests (no Docker daemon needed beyond `docker compose config`)
	uv run --frozen pytest tests/gateway

gateway-live-test:  ## Gateway tests against real KrakenD/api containers (RUN_STACK=1; uses ports 18080/18081, and 8080 unless GATEWAY_PORT is set)
	RUN_STACK=1 uv run --frozen pytest tests/gateway
