"""Playwright adapter: a Chromium browser context whose every request is allow-list checked.

Document downloads use the context's request API with ``max_redirects=0`` so this code, not
the HTTP stack, follows each redirect hop and re-validates it against the ``AllowList``. Any
page navigation in the same context is guarded by a route handler applying the same policy
(prep for JS-driven portals in Phase 2; unused for plain PDF links).
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from backend.fetcher.adapter import Fetcher, FetchResult, Outcome
from backend.fetcher.allowlist import AllowList, BlockedURLError

CAPTCHA_RE = re.compile(rb"captcha|are you (a )?human|verify you are human", re.IGNORECASE)
DEFAULT_USER_AGENT = "bridge-loan-tools-research-fetcher/0.1 (+contact: see repository)"


def _retry_after(headers: dict[str, str]) -> float | None:
    try:
        return max(0.0, float(headers.get("retry-after", "")))
    except ValueError:
        return None  # HTTP-date form or absent: fall back to exponential backoff


class PlaywrightFetcher(Fetcher):
    def __init__(
        self,
        allowlist: AllowList,
        *,
        timeout_s: float = 20.0,
        max_bytes: int = 50_000_000,
        max_redirects: int = 5,
        user_agent: str = DEFAULT_USER_AGENT,
        chromium_sandbox: bool = True,
    ) -> None:
        self.allowlist = allowlist
        self.timeout_ms = timeout_s * 1000
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.user_agent = user_agent
        self.chromium_sandbox = chromium_sandbox
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    def start(self) -> None:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True, chromium_sandbox=self.chromium_sandbox
        )
        self._context = self._browser.new_context(user_agent=self.user_agent)
        self._context.route("**/*", self._guard)

    def close(self) -> None:
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        self._pw = self._browser = self._context = None

    def _guard(self, route, request) -> None:
        try:
            self.allowlist.validate(request.url)
        except BlockedURLError:
            route.abort("blockedbyclient")
            return
        route.continue_()

    def fetch(self, url: str) -> FetchResult:
        if self._context is None:
            raise RuntimeError("PlaywrightFetcher not started; use it as a context manager")
        current = url
        for _ in range(self.max_redirects + 1):
            try:
                self.allowlist.validate(current)
            except BlockedURLError as exc:
                return FetchResult(url=url, outcome=Outcome.BLOCKED_URL, detail=str(exc))
            try:
                response = self._context.request.get(
                    current, max_redirects=0, timeout=self.timeout_ms, fail_on_status_code=False
                )
                body = response.body()
            except PlaywrightTimeoutError:
                return FetchResult(url=url, outcome=Outcome.TIMEOUT, detail="request timed out")
            except PlaywrightError as exc:
                return FetchResult(
                    url=url,
                    outcome=Outcome.HTTP_ERROR,
                    detail=f"transport error: {exc.message[:200]}",
                )
            location = response.headers.get("location")
            if response.status in (301, 302, 303, 307, 308) and location:
                current = urljoin(current, location)
                continue
            return self._classify(url, response.status, response.headers, body)
        return FetchResult(url=url, outcome=Outcome.HTTP_ERROR, detail="too many redirects")

    def _classify(self, url: str, status: int, headers: dict[str, str], body: bytes) -> FetchResult:
        def result(outcome: Outcome, **kw) -> FetchResult:
            return FetchResult(url=url, outcome=outcome, status=status, **kw)

        if status == 429:
            return result(Outcome.RATE_LIMITED, retry_after=_retry_after(headers))
        if status == 402:
            return result(Outcome.PAID)
        is_html = "html" in headers.get("content-type", "").lower()
        if is_html and CAPTCHA_RE.search(body[:200_000]):
            return result(Outcome.CAPTCHA)
        if status != 200:
            return result(Outcome.HTTP_ERROR)
        if len(body) > self.max_bytes:
            return result(Outcome.HTTP_ERROR, detail=f"body exceeds max_bytes={self.max_bytes}")
        if not body.startswith(b"%PDF"):
            return result(Outcome.NOT_PDF, detail=headers.get("content-type", ""))
        return result(Outcome.OK, body=body)
