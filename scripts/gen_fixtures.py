"""Regenerate synthetic fixtures: `uv run python scripts/gen_fixtures.py [--with-5k]`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fixtures.generate import generate_all  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "fixtures")
    parser.add_argument("--with-5k", action="store_true", help="also write the 5k-row snapshot")
    args = parser.parse_args(argv)
    generate_all(args.out, with_5k=args.with_5k)
    print(f"fixtures written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
