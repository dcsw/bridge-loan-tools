#!/usr/bin/env python3
"""Mint the static per-key-id HS256 JWT the gateway accepts (ADR-0005).

    python gateway/mint_token.py --key-id alice [--ttl-days 30] [--env-file .env]

The signing key is JWT_SIGNING_KEY from the environment or the env file; it is never printed.
The token carries ``sub`` = key id (forwarded to the API as ``X-Key-Id`` for the audit log).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
from pathlib import Path

ISSUER = "bridge-loan-gateway"
AUDIENCE = "bridge-loan-api"
KID = "gw-1"  # the `kid` of the single JWK built from JWT_SIGNING_KEY (gateway/entrypoint.sh)
MIN_KEY_LENGTH = 32
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")  # same as backend/api/auth.py


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint(signing_key: str, key_id: str, ttl_seconds: int) -> str:
    if not KEY_ID_PATTERN.fullmatch(key_id):
        raise ValueError("key id must match [A-Za-z0-9._-]{1,64}")
    if len(signing_key) < MIN_KEY_LENGTH:
        raise ValueError(f"signing key must be at least {MIN_KEY_LENGTH} characters")
    if ttl_seconds <= 0:
        raise ValueError("ttl must be positive")
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT", "kid": KID}
    claims = {"sub": key_id, "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + ttl_seconds}
    signing_input = (
        f"{_b64(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64(json.dumps(claims, separators=(',', ':')).encode())}"
    )
    sig = hmac.new(signing_key.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(sig)}"


def _key_from(env_file: Path) -> str | None:
    if os.environ.get("JWT_SIGNING_KEY"):
        return os.environ["JWT_SIGNING_KEY"]
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "JWT_SIGNING_KEY" and value.strip():
                return value.strip()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--ttl-days", type=float, default=30)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)
    key = _key_from(args.env_file)
    if key is None:
        print("JWT_SIGNING_KEY not set (run `make env`)", file=sys.stderr)
        return 2
    try:
        print(mint(key, args.key_id, int(args.ttl_days * 86400)))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
