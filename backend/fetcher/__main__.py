"""``python -m backend.fetcher``: fetch URLs from a file into the drop dir.

Portal hosts come from config (``FETCHER_PORTAL_HOSTS``, comma separated), never from code.
The fetcher runs on the isolated ``fetch`` network and only writes to the drop dir.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from backend.fetcher.adapter import Fetcher, RateLimiter, RetryPolicy
from backend.fetcher.allowlist import AllowList
from backend.fetcher.manifest import fetch_to_drop
from backend.fetcher.playwright import PlaywrightFetcher


def build_fetcher(name: str, allowlist: AllowList, *, sandbox: bool) -> Fetcher:
    if name == "obscura":
        from backend.fetcher.obscura import ObscuraFetcher

        return ObscuraFetcher(allowlist)
    return PlaywrightFetcher(allowlist, chromium_sandbox=sandbox)


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    environment = dict(os.environ) if env is None else env
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--urls", required=True, type=Path, help="file with one URL per line")
    parser.add_argument("--town", required=True)
    parser.add_argument("--drop", type=Path, default=Path(environment.get("FETCHER_DROP", "/drop")))
    parser.add_argument("--adapter", choices=["playwright", "obscura"], default="playwright")
    parser.add_argument("--min-interval", type=float, default=2.0, help="seconds between requests")
    args = parser.parse_args(argv)

    hosts = [h for h in environment.get("FETCHER_PORTAL_HOSTS", "").split(",") if h.strip()]
    allowlist = AllowList(
        hosts,
        allow_private_for_tests=environment.get("FETCHER_ALLOW_PRIVATE_FOR_TESTS") == "1",
        env=environment,
    )
    urls = [u.strip() for u in args.urls.read_text().splitlines() if u.strip()]
    # Chromium's sandbox needs privileges the locked-down container does not grant.
    sandbox = environment.get("FETCHER_CHROMIUM_SANDBOX", "1") == "1"
    with build_fetcher(args.adapter, allowlist, sandbox=sandbox) as fetcher:
        rows = fetch_to_drop(
            fetcher,
            urls,
            town=args.town,
            drop_dir=args.drop,
            policy=RetryPolicy(),
            limiter=RateLimiter(args.min_interval),
        )
    for row in rows:
        print(f"{row.status} {row.gap_reason or '-'} {row.source_url}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
