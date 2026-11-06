"""Helpers shared by the gateway tests (unique module name; see tests/stack/stack_helpers.py)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "gateway"
KRAKEND_JSON = GATEWAY_DIR / "krakend.json"
COMPOSE_DIR = ROOT / "deploy" / "docker"
GATEWAY_COMPOSE = COMPOSE_DIR / "compose.gateway.yaml"
EDGE_PATH = COMPOSE_DIR / "gateway" / "edge.py"
OPENAPI_PATH = ROOT / "openapi.json"
ENV_FILE = ROOT / ".env"


def load_edge():
    """Import deploy/docker/gateway/edge.py (a standalone script, not a package)."""
    spec = importlib.util.spec_from_file_location("gateway_edge", EDGE_PATH)
    assert spec and spec.loader, EDGE_PATH
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def load_krakend() -> dict[str, Any]:
    return json.loads(KRAKEND_JSON.read_text())


def load_openapi() -> dict[str, Any]:
    return json.loads(OPENAPI_PATH.read_text())


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_jwt(
    key: str,
    sub: str = "key-alpha",
    *,
    kid: str | None = "gw-1",
    alg: str = "HS256",
    exp_in: int | None = 3600,
    iss: str | None = "bridge-loan-gateway",
    aud: str | None = "bridge-loan-api",
    extra: dict[str, Any] | None = None,
) -> str:
    """Minimal HS256 JWT signer (no PyJWT dependency); ``alg`` only changes the header."""
    header: dict[str, Any] = {"alg": alg, "typ": "JWT"}
    if kid:
        header["kid"] = kid
    claims: dict[str, Any] = {"sub": sub, "iat": int(time.time())}
    if exp_in is not None:
        claims["exp"] = int(time.time()) + exp_in
    if iss:
        claims["iss"] = iss
    if aud:
        claims["aud"] = aud
    claims.update(extra or {})
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
    sig = hmac.new(key.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(sig)}"


def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def docker_compose(
    project: str,
    files: list[Path],
    *args: str,
    check: bool = True,
    timeout: int = 900,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-p", project, "--env-file", str(ENV_FILE)]
    for f in files:
        cmd += ["-f", str(f)]
    cmd += ["--profile", "core", *args]
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        cmd, capture_output=True, text=True, check=check, timeout=timeout, env=full_env
    )


# --- tiny OpenAPI (3.1, as emitted by FastAPI) response validator -------------------------


def _resolve(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        node: Any = spec
        for part in schema["$ref"].removeprefix("#/").split("/"):
            node = node[part]
        schema = node
    return schema


def validate(
    spec: dict[str, Any], schema: dict[str, Any], value: Any, path: str = "$"
) -> list[str]:
    """Return a list of violations of ``schema`` (subset used by openapi.json) by ``value``."""
    schema = _resolve(spec, schema)
    errors: list[str] = []
    if "anyOf" in schema or "oneOf" in schema:
        options = schema.get("anyOf") or schema["oneOf"]
        attempts = [validate(spec, o, value, path) for o in options]
        return [] if any(not a for a in attempts) else [f"{path}: matches no alternative"]
    if "const" in schema and value != schema["const"]:
        return [f"{path}: expected {schema['const']!r}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: {value!r} not in enum"]
    kind = schema.get("type")
    checks = {
        "null": lambda v: v is None,
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "array": lambda v: isinstance(v, list),
        "object": lambda v: isinstance(v, dict),
    }
    if kind and not checks[kind](value):
        return [f"{path}: expected {kind}, got {type(value).__name__}"]
    if kind == "object":
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing required {name!r}")
        for name, item in value.items():
            if name in props:
                errors += validate(spec, props[name], item, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {name!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors += validate(spec, schema["additionalProperties"], item, f"{path}.{name}")
    if kind == "array" and "items" in schema:
        for i, item in enumerate(value):
            errors += validate(spec, schema["items"], item, f"{path}[{i}]")
    return errors


def response_schema(
    spec: dict[str, Any], path: str, method: str, status: int, content_type: str
) -> dict[str, Any] | None:
    """The documented schema for a response, or None if the status/content type is undocumented."""
    responses = spec["paths"][path][method]["responses"]
    content = responses.get(str(status), {}).get("content", {})
    entry = content.get(content_type)
    return None if entry is None else entry.get("schema", {})


@dataclass
class Http:
    status: int
    headers: dict[str, str]
    body: bytes

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


def http(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 30,
) -> Http:
    """Plain urllib request that returns error statuses instead of raising."""
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return Http(resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read())
    except urllib.error.HTTPError as err:
        return Http(err.code, {k.lower(): v for k, v in err.headers.items()}, err.read())


_B64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def signature_variants(token: str) -> list[str]:
    """The token plus its 3 non-canonical spellings (same signature bytes, other trailing bits).

    KrakenD 3.0.3 accepts all four, so without edge canonicalisation one token would have four
    distinct Authorization strings and therefore four rate-limit buckets.
    """
    head, sig = token.rsplit(".", 1)
    value = _B64URL.index(sig[-1])
    return [f"{head}.{sig[:-1]}{_B64URL[(value & ~3) | k]}" for k in range(4)]
