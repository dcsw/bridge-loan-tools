"""CLI wiring: config-injected portal hosts, adapter choice, drop output."""

from __future__ import annotations

import pytest

from backend.fetcher.__main__ import build_fetcher, main
from backend.fetcher.allowlist import AllowList, AllowListConfigError
from backend.fetcher.obscura import ObscuraFetcher
from backend.fetcher.playwright import PlaywrightFetcher


def test_build_fetcher_selects_adapter():
    allow = AllowList(["portal.example.test"])
    assert isinstance(build_fetcher("obscura", allow, sandbox=True), ObscuraFetcher)
    assert isinstance(build_fetcher("playwright", allow, sandbox=False), PlaywrightFetcher)


def test_missing_portal_hosts_config_is_refused(tmp_path):
    urls = tmp_path / "urls.txt"
    urls.write_text("https://portal.example.test/a.pdf\n")
    with pytest.raises(AllowListConfigError):
        main(["--urls", str(urls), "--town", "t", "--drop", str(tmp_path / "d")], env={})


def test_private_override_refused_outside_test_mode(tmp_path):
    urls = tmp_path / "urls.txt"
    urls.write_text("x\n")
    env = {"FETCHER_PORTAL_HOSTS": "127.0.0.1", "FETCHER_ALLOW_PRIVATE_FOR_TESTS": "1"}
    with pytest.raises(AllowListConfigError, match="test mode"):
        main(["--urls", str(urls), "--town", "t", "--drop", str(tmp_path / "d")], env=env)


def test_end_to_end_against_fake_portal_writes_drop(portal, tmp_path, capsys):
    urls = tmp_path / "urls.txt"
    urls.write_text(f"{portal.url('/pdfs/deed.pdf')}\n{portal.url('/captcha')}\n")
    env = {
        "FETCHER_PORTAL_HOSTS": portal.host,
        "FETCHER_ALLOW_PRIVATE_FOR_TESTS": "1",
        "FETCHER_TEST_MODE": "1",
    }
    drop = tmp_path / "drop"
    code = main(
        ["--urls", str(urls), "--town", "mattapoisett", "--drop", str(drop), "--min-interval", "0"],
        env=env,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "fetched - " in out and "gap blocked " in out
    assert len(list(drop.glob("*.pdf"))) == 1 and len(list(drop.glob("*.json"))) == 2
