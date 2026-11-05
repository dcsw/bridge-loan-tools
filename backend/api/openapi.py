"""Generate the committed ``openapi.json`` (``python -m backend.api.openapi [--write|--check]``)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from backend.api.config import Settings
from backend.api.main import create_app

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "openapi.json"
PROBLEM_JSON = "application/problem+json"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def build_openapi() -> dict[str, Any]:
    """OpenAPI for the app with problem+json error contracts (no FastAPI default 422)."""
    settings = Settings(
        gateway_secret="openapi-generation-placeholder-secret",
        snapshot_base="/nonexistent/snapshot",
        audit_log_path="/nonexistent/audit.log",
    )
    spec = create_app(settings).openapi()
    for item in spec["paths"].values():
        for method, op in item.items():
            if method not in HTTP_METHODS:
                continue
            responses = op["responses"]
            responses.pop("422", None)
            for status, resp in responses.items():
                if int(status) < 400:
                    continue
                content = resp.get("content", {})
                if content:  # FastAPI labels error models with the route's success media type
                    resp["content"] = {PROBLEM_JSON: next(iter(content.values()))}
                if status == "503":
                    resp["headers"] = {
                        "Retry-After": {
                            "description": "Seconds to wait before retrying",
                            "schema": {"type": "integer", "minimum": 0},
                        }
                    }
    for name in ("HTTPValidationError", "ValidationError"):
        spec["components"]["schemas"].pop(name, None)
    return spec


def render(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render(build_openapi())
    if "--write" in argv:
        OPENAPI_PATH.write_text(text)
        print(f"wrote {OPENAPI_PATH}")
        return 0
    if "--check" in argv:
        if OPENAPI_PATH.read_text() != text:
            print("openapi.json is out of date; run `make openapi`", file=sys.stderr)
            return 1
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
