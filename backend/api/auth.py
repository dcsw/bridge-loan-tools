"""Gateway-only access: shared-secret header (constant-time compare) + forwarded key id.

The backend never authenticates end users; KrakenD does (static per-key HS256 JWT, ADR-0005)
and forwards the key id. Anything that does not carry the gateway secret is rejected.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from backend.api.errors import ApiProblem

SECRET_HEADER = "X-Gateway-Secret"
KEY_ID_HEADER = "X-Key-Id"
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_secret_scheme = APIKeyHeader(name=SECRET_HEADER, scheme_name="GatewayAuth", auto_error=False)
_key_scheme = APIKeyHeader(name=KEY_ID_HEADER, scheme_name="KeyId", auto_error=False)


@dataclass(frozen=True)
class Principal:
    key_id: str


def gateway_auth(
    request: Request,
    secret: Annotated[str | None, Depends(_secret_scheme)] = None,
    key_id: Annotated[str | None, Depends(_key_scheme)] = None,
) -> Principal:
    expected: str = request.app.state.settings.gateway_secret
    # always compare, even when the header is absent, so timing does not reveal presence
    ok = hmac.compare_digest((secret or "").encode("utf-8"), expected.encode("utf-8"))
    if not secret or not ok:
        raise ApiProblem(
            401,
            "gateway_auth_required",
            "requests must come through the gateway",
            headers={"WWW-Authenticate": "GatewayAuth"},
        )
    if not key_id or not KEY_ID_PATTERN.fullmatch(key_id):
        raise ApiProblem(401, "key_id_required", "a valid forwarded key id is required")
    return Principal(key_id=key_id)
