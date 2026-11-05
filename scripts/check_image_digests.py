#!/usr/bin/env python3
"""Fail if any Dockerfile ``FROM`` or compose ``image:`` lacks an ``@sha256:`` digest.

Usage: check_image_digests.py [ROOT]   (ROOT defaults to the current directory)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}(?![0-9a-f])")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox"}
FROM_RE = re.compile(
    r"^\s*FROM\s+(?:--\S+\s+)*(?P<image>\S+)(?:\s+AS\s+(?P<alias>\S+))?\s*$",
    re.IGNORECASE,
)
IMAGE_RE = re.compile(r"^\s*(?:-\s+)?image:\s*(?P<image>.+?)\s*(?:#.*)?$")


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or SKIP_DIRS & set(path.relative_to(root).parts):
            continue
        yield path


def _is_dockerfile(path: Path) -> bool:
    name = path.name.lower()
    return name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile")


def _is_compose(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith((".yaml", ".yml")) and (name.startswith(("compose", "docker-compose")))


def _check_dockerfile(path: Path) -> list[str]:
    violations = []
    stages: set[str] = set()
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        match = FROM_RE.match(line)
        if not match:
            continue
        image = match["image"]
        if image.lower() != "scratch" and image.lower() not in stages:
            if not DIGEST_RE.search(image):
                violations.append(f"{path}:{lineno}: FROM {image} is not digest-pinned")
        if match["alias"]:
            stages.add(match["alias"].lower())
    return violations


def _check_compose(path: Path) -> list[str]:
    violations = []
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        match = IMAGE_RE.match(line)
        if not match:
            continue
        image = match["image"].strip("'\"")
        if not DIGEST_RE.search(image):
            violations.append(f"{path}:{lineno}: image {image} is not digest-pinned")
    return violations


def find_violations(root: Path) -> list[str]:
    violations: list[str] = []
    for path in _iter_files(Path(root)):
        if _is_dockerfile(path):
            violations += _check_dockerfile(path)
        elif _is_compose(path):
            violations += _check_compose(path)
    return violations


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path.cwd()
    violations = find_violations(root)
    for violation in violations:
        print(violation, file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
