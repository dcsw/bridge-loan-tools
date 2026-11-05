"""SSRF controls: https-only, domain allow-list, IP blocking, redirect re-validation."""

from __future__ import annotations

import pytest

from backend.fetcher.allowlist import AllowList, AllowListConfigError, BlockedURLError

HOST = "portal.example.test"
GOOD_IP = "93.184.216.34"


def resolver_for(mapping: dict[str, list[str]]):
    def resolve(host: str) -> list[str]:
        if host not in mapping:
            raise OSError(f"cannot resolve {host}")
        return mapping[host]

    return resolve


def make(mapping=None, **kwargs) -> AllowList:
    mapping = {HOST: [GOOD_IP]} if mapping is None else mapping
    return AllowList([HOST], resolver=resolver_for(mapping), **kwargs)


def test_accepts_https_allowlisted_host_with_public_ip():
    url = f"https://{HOST}/docs/1.pdf"
    assert make().validate(url) == url


def test_accepts_explicit_443():
    assert make().validate(f"https://{HOST}:443/x")


def test_host_match_is_case_insensitive():
    assert make().validate("https://PORTAL.Example.TEST/x")


@pytest.mark.parametrize(
    "url",
    [
        f"http://{HOST}/x",  # not https
        f"ftp://{HOST}/x",
        "file:///etc/passwd",
        "https://evil.example.org/x",  # not allow-listed
        f"https://sub.{HOST}/x",  # subdomains are not implicitly trusted
        f"https://{HOST}.evil.example.org/x",
        "https://169.254.169.254/latest/meta-data/",  # metadata IP literal
        "https://127.0.0.1/",
        "https://[::1]/",
        f"https://user@{HOST}/x",  # userinfo
        f"https://{HOST}:8443/x",  # non-443 port
        "https:///nohost",
        "not a url",
        "",
    ],
)
def test_blocks_bad_urls(url):
    with pytest.raises(BlockedURLError):
        make().validate(url)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.1.2.3",
        "10.0.0.5",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "169.254.1.1",  # link-local
        "100.64.0.1",  # CGNAT
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "fd00:ec2::254",  # AWS IMDS IPv6
        "::ffff:127.0.0.1",  # v4-mapped loopback
        "::ffff:169.254.169.254",
    ],
)
def test_blocks_hostnames_resolving_to_internal_addresses(ip):
    with pytest.raises(BlockedURLError, match="non-public|metadata"):
        make({HOST: [ip]}).validate(f"https://{HOST}/x")


def test_blocks_when_any_resolved_address_is_internal():
    with pytest.raises(BlockedURLError):
        make({HOST: [GOOD_IP, "10.1.2.3"]}).validate(f"https://{HOST}/x")


def test_blocks_unresolvable_host():
    with pytest.raises(BlockedURLError, match="resolve"):
        make({}).validate(f"https://{HOST}/x")


def test_blocks_empty_resolution():
    with pytest.raises(BlockedURLError):
        make({HOST: []}).validate(f"https://{HOST}/x")


@pytest.mark.parametrize(
    "location",
    [
        "http://169.254.169.254/latest/meta-data/",
        "https://169.254.169.254/latest/meta-data/",
        "https://localhost/admin",
        "https://internal.example.org/",
        f"http://{HOST}/downgraded",
    ],
)
def test_redirect_targets_are_validated_like_any_url(location):
    allow = make()
    allow.validate(f"https://{HOST}/start")
    with pytest.raises(BlockedURLError):
        allow.validate(location)


def test_redirect_to_allowlisted_host_that_now_resolves_internal_is_blocked():
    mapping = {HOST: [GOOD_IP]}
    allow = make(mapping)
    allow.validate(f"https://{HOST}/a")
    mapping[HOST] = ["10.0.0.9"]  # rebinding between hops
    with pytest.raises(BlockedURLError):
        allow.validate(f"https://{HOST}/b")


def test_empty_host_list_is_a_config_error():
    with pytest.raises(AllowListConfigError):
        AllowList([])


def test_hosts_must_be_bare_hostnames():
    with pytest.raises(AllowListConfigError):
        AllowList(["https://portal.example.test"])


# --- test-only override -------------------------------------------------------------------

TEST_ENV = {"FETCHER_TEST_MODE": "1"}


def test_override_refused_outside_test_mode():
    with pytest.raises(AllowListConfigError, match="test mode"):
        AllowList(["127.0.0.1"], allow_private_for_tests=True, env={})


def test_override_refused_when_test_mode_flag_not_exactly_one():
    with pytest.raises(AllowListConfigError):
        AllowList(["127.0.0.1"], allow_private_for_tests=True, env={"FETCHER_TEST_MODE": "0"})


def test_override_permits_http_loopback_for_listed_host_only():
    allow = AllowList(["127.0.0.1"], allow_private_for_tests=True, env=TEST_ENV)
    assert allow.validate("http://127.0.0.1:8080/pdfs/a.pdf")
    with pytest.raises(BlockedURLError):
        allow.validate("http://127.0.0.2:8080/")  # loopback but not listed
    with pytest.raises(BlockedURLError):
        allow.validate("http://169.254.169.254/latest/meta-data/")


def test_override_never_unblocks_metadata_endpoint_even_if_listed():
    allow = AllowList(["169.254.169.254"], allow_private_for_tests=True, env=TEST_ENV)
    with pytest.raises(BlockedURLError, match="metadata"):
        allow.validate("http://169.254.169.254/latest/meta-data/")


def test_override_off_by_default_even_in_test_mode():
    allow = AllowList(["127.0.0.1"], env=TEST_ENV)
    with pytest.raises(BlockedURLError):
        allow.validate("http://127.0.0.1:8080/")
