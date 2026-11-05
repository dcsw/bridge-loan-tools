# WU9: frontend (Vite + React + TS). Needs Node >= 24 and npm; Docker only for web-up.
WEB_COMPOSE := docker compose -p bridgeloan-web -f deploy/docker/compose.web.yaml --profile web
.PHONY: web-install web-types web-types-check web-lint web-typecheck web-test web-e2e web-audit web-build web-dev web-up web-down

web-install:  ## Install frontend deps from the lockfile (+ Playwright Chromium)
	cd frontend && npm ci && npx playwright install chromium

web-types:  ## Regenerate frontend/src/api/schema.d.ts from openapi.json
	cd frontend && npm run gen:types

web-types-check:  ## Fail if the generated TS types are stale relative to openapi.json
	cd frontend && npm run check:types

web-lint:  ## ESLint (bans dangerouslySetInnerHTML / innerHTML)
	cd frontend && npm run lint

web-typecheck:  ## tsc --noEmit (strict)
	cd frontend && npm run typecheck

web-test:  ## Vitest: transforms, URL state, contrast, drift check, bundle checks
	cd frontend && npm test

web-e2e:  ## Playwright + axe against the MSW mock build (offline)
	cd frontend && npm run e2e

web-audit:  ## npm audit (fails on high or critical)
	cd frontend && npm audit --audit-level=high

web-build:  ## Production build to frontend/dist
	cd frontend && npm run build

web-dev:  ## Vite dev server on 127.0.0.1:5173; BRIDGE_API_URL / BRIDGE_API_JWT feed the dev proxy only
	cd frontend && npm run dev

web-up:  ## Build and start the web image on 127.0.0.1:3000 (project bridgeloan-web)
	$(WEB_COMPOSE) up -d --build --wait

web-down:  ## Stop the web image
	$(WEB_COMPOSE) down
