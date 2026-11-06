# ---
# jupyter:
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 01 - Fetch from the fake portal
# Runs the real `fetch_to_drop` pipeline against the local fake portal (synthetic PDFs only).
# A stdlib adapter stands in for the browser adapters (loopback fake portal only).

# %%
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path.cwd()))
from _env import repo_root  # noqa: E402

ROOT = repo_root()
sys.path.insert(0, str(ROOT))

from backend.fetcher.adapter import Fetcher, FetchResult, Outcome, RetryPolicy  # noqa: E402
from backend.fetcher.manifest import fetch_to_drop  # noqa: E402
from tests.fakeportal.server import FakePortal  # noqa: E402


# %%
class StdlibFetcher(Fetcher):
    """Minimal adapter for the demo: no browser needed for static fixture PDFs."""

    def fetch(self, url: str) -> FetchResult:
        try:
            with urlopen(url, timeout=10) as resp:  # noqa: S310 - loopback fake portal
                return FetchResult(
                    url=url, outcome=Outcome.OK, body=resp.read(), status=resp.status
                )
        except HTTPError as exc:
            return FetchResult(url=url, outcome=Outcome.HTTP_ERROR, status=exc.code)


# %%
drop = Path(tempfile.mkdtemp(prefix="drop-"))
with FakePortal() as portal:
    rows = fetch_to_drop(
        StdlibFetcher(),
        [
            portal.url("/pdfs/mortgage.pdf"),
            portal.url("/pdfs/discharge.pdf"),
            portal.url("/forbidden"),
        ],
        town="mattapoisett",
        drop_dir=drop,
        policy=RetryPolicy(max_attempts=1),
        sleep=time.sleep,
    )
for row in rows:
    print(row.status, row.gap_reason, row.source_url)
assert sum(r.status == "fetched" for r in rows) == 2, [r.status for r in rows]
