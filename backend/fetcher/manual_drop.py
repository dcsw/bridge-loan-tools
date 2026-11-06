"""``python -m backend.fetcher.manual_drop``: put a hand-downloaded PDF (or a gap) in the drop dir.

For portals whose terms or bot protection rule out automated fetching (see ``info/ToS``): a person
downloads the instrument in a browser, then this builds exactly what the fetcher would have written,
using the same ``build_row``/``write_drop`` code, so ``make fetcher-ingest`` takes it from there.

    python -m backend.fetcher.manual_drop --file instrument.pdf --url <portal URL or book/page> \
        --town mattapoisett
    python -m backend.fetcher.manual_drop --gap paid --url <reference> --town madison

``--url`` is the provenance shown to users as the source reference. It is recorded verbatim
(control characters are refused). No network access happens here.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from backend.fetcher.adapter import FetchResult, Outcome
from backend.fetcher.manifest import build_row, write_drop

MAX_BYTES = 50 * 1024 * 1024
TOWN_RE = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
# A gap stands in for a document that could not be obtained by hand.
GAP_OUTCOME = {"paid": Outcome.PAID, "blocked": Outcome.CAPTCHA, "failed": Outcome.TIMEOUT}


class ManualDropError(ValueError):
    """The input cannot be turned into a valid drop entry."""


def _check_common(url: str, town: str) -> None:
    if not url.strip() or any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise ManualDropError("--url must be non-empty and contain no control characters")
    if not TOWN_RE.match(town):
        raise ManualDropError("--town must be a lowercase slug such as 'mattapoisett'")


def drop_pdf(pdf: Path, *, url: str, town: str, drop_dir: Path, now: datetime | None = None) -> str:
    """Validate ``pdf``; write its .pdf and .json pair (named by sha256) to ``drop_dir``."""
    _check_common(url, town)
    if not pdf.is_file():
        raise ManualDropError(f"not a file: {pdf}")
    size = pdf.stat().st_size
    if size == 0 or size > MAX_BYTES:
        raise ManualDropError(f"PDF size must be 1..{MAX_BYTES} bytes (got {size})")
    body = pdf.read_bytes()
    if not body.startswith(b"%PDF"):
        raise ManualDropError("file does not start with %PDF; refusing")
    result = FetchResult(url=url, outcome=Outcome.OK, body=body, status=200)
    row, payload = build_row(result, town=town, fetched_at=now or datetime.now(UTC))
    write_drop(drop_dir, row, payload)
    return row.doc_id


def drop_gap(
    reason: str, *, url: str, town: str, drop_dir: Path, now: datetime | None = None
) -> str:
    """Record a document that could not be obtained (e.g. paid image not bought). Returns doc_id."""
    _check_common(url, town)
    if reason not in GAP_OUTCOME:
        raise ManualDropError(f"--gap must be one of {sorted(GAP_OUTCOME)}")
    result = FetchResult(url=url, outcome=GAP_OUTCOME[reason])
    row, _ = build_row(result, town=town, fetched_at=now or datetime.now(UTC))
    write_drop(drop_dir, row, None)
    return row.doc_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--file", type=Path, help="PDF downloaded by hand")
    what.add_argument("--gap", choices=sorted(GAP_OUTCOME), help="record an unobtainable document")
    parser.add_argument("--url", required=True, help="source reference (portal URL or book/page)")
    parser.add_argument("--town", required=True)
    parser.add_argument("--drop", type=Path, default=Path("data/drop"))
    args = parser.parse_args(argv)
    try:
        if args.file is not None:
            doc_id = drop_pdf(args.file, url=args.url, town=args.town, drop_dir=args.drop)
            print(f"dropped {doc_id}.pdf + .json in {args.drop}")
        else:
            doc_id = drop_gap(args.gap, url=args.url, town=args.town, drop_dir=args.drop)
            print(f"recorded gap ({args.gap}) {doc_id}.json in {args.drop}")
    except ManualDropError as exc:
        print(f"manual-drop: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
