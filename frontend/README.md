# Frontend (WU9)

Vite + React + TypeScript (strict). Map: d3-geo + d3-zoom over a bundled TopoJSON outline.
`src/assets/mattapoisett.topo.json` is a hand-drawn **placeholder, not official data** (see
`tests/fixtures/topojson/README.md`); replace it before any real use. No tiles or other network assets are used.

## Commands

| Task | Command |
| --- | --- |
| install | `npm ci && npx playwright install chromium` |
| dev server (127.0.0.1:5173) | `BRIDGE_API_URL=http://127.0.0.1:8080 BRIDGE_API_JWT=... npm run dev` |
| offline demo with mocks | `npx vite --mode mock` |
| unit tests | `npm test` |
| e2e + axe (offline, MSW) | `npm run e2e` |
| lint / types | `npm run lint` / `npm run typecheck` |
| regenerate API types | `npm run gen:types` |
| drift check (exit 1 if stale) | `npm run check:types` |

## Auth (v1 limitation, local single-user only)

The API key/JWT is never in the client bundle. In dev, Vite's proxy (`/api` -> `BRIDGE_API_URL`) adds the
`Authorization` header from `BRIDGE_API_JWT` (no `VITE_` prefix, so it is not exposed to client code). In the
compose image, nginx does the same from `WEB_API_JWT`. Anyone who can reach the dev server or the web port can use
that credential, so both listen on 127.0.0.1 only. OIDC/JWT per user is required before any non-local exposure.

## API contract

`../openapi.json` is the source of truth. `src/api/schema.d.ts` is generated and committed; `npm run check:types`
(also a vitest test) fails when it is stale. MSW mocks (`src/mocks`) are typed against the generated types and
validated against `openapi.json` component schemas in `tests/mocks-contract.test.ts`. The OpenAPI document carries no
`examples`, so mock data derives from `tests/fixtures/snapshots/single` plus invented rows.

## Behaviour notes

- The list payload has no borrower and no `signals_unevaluable`. "Show borrower" is the only call to
  `GET /loans/{id}` and also loads field confidence, unevaluable signals and the source link.
- Clustering is deferred to Phase 5. Pins that overlap stay separate buttons; use the table.
