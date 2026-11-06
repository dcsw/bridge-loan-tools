"""gateway/mint_token.py mints the static per-key-id HS256 JWT the gateway accepts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json

import pytest
from gateway_helpers import GATEWAY_DIR

spec = importlib.util.spec_from_file_location("mint_token", GATEWAY_DIR / "mint_token.py")
assert spec and spec.loader
mint_token = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mint_token)

KEY = "k" * 64


def _decode(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def test_token_is_hs256_with_kid_sub_iss_aud_and_valid_signature():
    token = mint_token.mint(KEY, "key-alpha", ttl_seconds=60)
    header, claims, sig = token.split(".")
    assert _decode(header) == {"alg": "HS256", "typ": "JWT", "kid": "gw-1"}
    c = _decode(claims)
    assert c["sub"] == "key-alpha"
    assert c["iss"] == "bridge-loan-gateway" and c["aud"] == "bridge-loan-api"
    assert 0 < c["exp"] - c["iat"] <= 60
    expected = hmac.new(KEY.encode(), f"{header}.{claims}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)) == expected


@pytest.mark.parametrize("bad", ["", "has space", "a" * 65, "semi;colon", "ünï"])
def test_key_id_must_match_the_backend_pattern(bad):
    with pytest.raises(ValueError, match="key id"):
        mint_token.mint(KEY, bad, ttl_seconds=60)


def test_short_signing_key_is_refused():
    with pytest.raises(ValueError, match="at least 32"):
        mint_token.mint("short", "key-alpha", ttl_seconds=60)


def test_ttl_must_be_positive():
    with pytest.raises(ValueError, match="ttl"):
        mint_token.mint(KEY, "key-alpha", ttl_seconds=0)


def test_main_reads_key_from_environment_and_prints_token(monkeypatch, capsys):
    monkeypatch.setenv("JWT_SIGNING_KEY", KEY)
    assert mint_token.main(["--key-id", "key-beta", "--ttl-days", "1"]) == 0
    token = capsys.readouterr().out.strip()
    assert _decode(token.split(".")[1])["sub"] == "key-beta"


def test_main_reads_key_from_env_file_without_echoing_it(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("JWT_SIGNING_KEY", raising=False)
    env_file = tmp_path / "x.env"
    env_file.write_text(f"OTHER=1\nJWT_SIGNING_KEY={KEY}\n")
    assert mint_token.main(["--key-id", "key-gamma", "--env-file", str(env_file)]) == 0
    out = capsys.readouterr()
    assert KEY not in out.out + out.err


def test_main_fails_cleanly_when_key_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("JWT_SIGNING_KEY", raising=False)
    empty = tmp_path / "empty.env"
    empty.write_text("")
    assert mint_token.main(["--key-id", "k", "--env-file", str(empty)]) == 2
    assert "JWT_SIGNING_KEY" in capsys.readouterr().err


def test_main_reports_bad_arguments(monkeypatch, capsys):
    monkeypatch.setenv("JWT_SIGNING_KEY", KEY)
    assert mint_token.main(["--key-id", "bad id"]) == 2
    assert "key id" in capsys.readouterr().err
