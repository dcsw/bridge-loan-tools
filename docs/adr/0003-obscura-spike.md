# ADR-0003: Obscura spike and fetcher adapter choice

- Status: Accepted (Phase 1)
- Date: 2026-11-05
- Work unit: WU4 (BEADS bridge-loan-tools-7hn.4)

## Context

The design (3.1/3.5) names Obscura as the fetcher's headless browser, on an isolated network.
Phase 1 only fetches from a local fake portal (no live portal contact; a ToS review gates any
live scrape), so the browser choice must be reversible. The plan time-boxes the Obscura spike
to about one day and accepts either a working adapter or a documented stub.

## What was found (research, 2026-11-05; nothing below was executed)

- Obscura is an open-source (Apache-2.0) headless browser engine written in Rust, running
  JavaScript on V8 and exposing the Chrome DevTools Protocol, so Playwright can attach with
  `chromium.connectOverCDP`. Upstream: `github.com/h4ckf0r0day/obscura`. Roughly 30k stars at
  the time of writing, but its own README calls the rendering engine an "evolving independent
  engine" (long-tail CSS/Web APIs, fonts, media may differ from Chromium).
- A Linux x86_64 binary is obtainable: release `v0.2.6` (2026-10-31) has
  `obscura-x86_64-linux.tar.gz` (sha256 `eed986e4...bd76`, as reported by the GitHub API
  `digest` field) plus `-no-render` and `-stealth` variants and aarch64/macOS/Windows builds.
  The README states glibc >= 2.35 (Ubuntu 22.04 baseline), which the Playwright noble base
  image satisfies.
- No signature or provenance attestation was found; the only integrity datum is the
  GitHub-computed digest. Published Docker Hub / AUR / third-party mirrors were not verified
  as official channels.
- The binary has a built-in SSRF guard (blocks private IPs unless `--allow-private-network`),
  which is useful but does not replace our own allow-list.
- A `-stealth` variant exists (anti-bot-detection evasion). That is incompatible with this
  project's posture (polite, identified fetching, ToS-gated). If Obscura is ever adopted, the
  non-stealth build is the only acceptable one.

## What was NOT done

I did not download or run the binary, and did not build an adapter against it. Reasons: the
download is an unsigned third-party executable (no verified provenance), a meaningful adapter
needs CDP wiring plus parity tests against the fake portal, and Phase 1 has no requirement
Obscura satisfies that Playwright does not. So: **the Obscura adapter is a stub**
(`backend/fetcher/obscura.py`): it implements the `Fetcher` interface and raises
`ObscuraNotAvailableError` pointing at this ADR. A test asserts it fails loudly rather than
returning data. Claims about Obscura's behavior above come from its README and release
metadata only and are unverified here.

## Decision

1. Default and only working adapter in Phase 1: `PlaywrightFetcher` (Chromium baked into the
   digest-pinned `mcr.microsoft.com/playwright/python:v1.64.1-noble` base image; pip
   `playwright==1.64.1` matches it).
2. Keep the `Fetcher` interface (`backend/fetcher/adapter.py`) as the seam. Revisit Obscura at
   the "Obscura vs Playwright default" decision point after WU4 using these entry criteria:
   verified checksum pinned in the Dockerfile (`sha256sum -c`), non-stealth build, a CDP
   adapter passing the same fake-portal suite as Playwright, and a measured benefit.
3. The Playwright adapter currently downloads via the browser context's request API with
   manual, allow-list-checked redirect handling; page navigation is guarded by a route
   handler. Real JS rendering is left for Phase 2 portals.

## Security notes and Phase 2 requirements

- SSRF controls live in `allowlist.py` (https only, port 443, exact-host allow-list from
  config, every resolved address must be globally routable, metadata addresses always
  blocked, every redirect hop re-validated). The test-only override (http + loopback for the
  fake portal) is refused unless `FETCHER_TEST_MODE=1` and never unblocks metadata IPs.
- Known residual risk: DNS is resolved again by the HTTP stack after validation (rebinding
  window). In Phase 1 the compose `fetch` network is `internal: true`, so the container has no
  route to the internet, `minio`, `lakekeeper` or the metadata IP (verified by a live Docker
  test). **Phase 2 live fetching needs an egress proxy with a domain allow-list** (proxy
  resolves and connects; the fetcher's only reachable peer), because an internal network
  cannot reach any portal.
- Hand-off is the host dir `data/drop/` (no docker socket, no MinIO route from the fetcher).
  `raw_ingest` runs on the host/core side, uploads content-addressed `pdf/<sha256>.pdf` and
  `manifest/<doc_id>.json` to the `raw` bucket; duplicates are no-ops.
- Gap taxonomy: CAPTCHA, exhausted 429, 401/403 and blocked URLs -> `blocked`; 402 -> `paid`;
  timeouts, 5xx, non-PDF, oversize, transport errors -> `failed`. Gap rows use
  `doc_id = sha256(source_url)` because no document bytes exist.
- Image dependencies other than the base image are pinned to the `uv.lock` versions but
  installed with plain `pip` (not hash-locked); tighten when the image is productionized.
