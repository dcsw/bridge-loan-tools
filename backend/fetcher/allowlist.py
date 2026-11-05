"""SSRF controls for the fetcher (design 3.5).

Every URL, including every redirect target, must pass ``AllowList.validate``:

* https only, port 443 only, no userinfo;
* host must be in the configured portal host list (exact match, no implicit subdomains);
* every resolved address must be globally routable (blocks loopback, private, link-local,
  CGNAT, multicast, reserved, unspecified, v4-mapped variants) and never a metadata endpoint.

A test-only override lets the local fake portal (http, loopback) work. It is refused unless the
environment explicitly declares test mode, and it never unblocks the cloud metadata endpoints.

Residual risk: the browser/HTTP client resolves DNS again after validation (rebinding window).
The compose ``fetch`` network is therefore ``internal`` and Phase 2 live fetching must add an
egress proxy with a domain allow-list (see ADR-0003).
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Callable, Iterable, Mapping
from urllib.parse import urlsplit

TEST_MODE_ENV = "FETCHER_TEST_MODE"
METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(a) for a in ("169.254.169.254", "fd00:ec2::254", "100.100.100.200")
)

Resolver = Callable[[str], list[str]]


class BlockedURLError(Exception):
    """The URL (or a redirect target) violates the fetch policy."""


class AllowListConfigError(ValueError):
    """The allow-list itself is misconfigured."""


def _system_resolver(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]


def _unmap(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


class AllowList:
    def __init__(
        self,
        hosts: Iterable[str],
        *,
        allow_private_for_tests: bool = False,
        resolver: Resolver | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.hosts = frozenset(h.strip().lower() for h in hosts)
        if not self.hosts:
            raise AllowListConfigError("allow-list needs at least one portal host")
        for host in self.hosts:
            if not host or any(c in host for c in "/:@ "):
                raise AllowListConfigError(f"allow-list entries must be bare hostnames: {host!r}")
        if allow_private_for_tests:
            environment = os.environ if env is None else env
            if environment.get(TEST_MODE_ENV) != "1":
                raise AllowListConfigError(
                    f"allow_private_for_tests is refused outside test mode ({TEST_MODE_ENV}=1)"
                )
        self.allow_private_for_tests = allow_private_for_tests
        self._resolver = resolver or _system_resolver

    def validate(self, url: str) -> str:
        """Return ``url`` if it may be fetched, else raise ``BlockedURLError``."""
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            port = parts.port
        except ValueError as exc:
            raise BlockedURLError(f"unparseable URL: {exc}") from exc
        override = self.allow_private_for_tests
        allowed_schemes = {"https", "http"} if override else {"https"}
        if parts.scheme not in allowed_schemes:
            raise BlockedURLError(f"scheme {parts.scheme!r} not allowed")
        if not host:
            raise BlockedURLError("URL has no host")
        if parts.username is not None or parts.password is not None:
            raise BlockedURLError("userinfo in URL not allowed")
        if host not in self.hosts:
            raise BlockedURLError(f"host {host!r} is not in the allow-list")
        if not override and port not in (None, 443):
            raise BlockedURLError(f"port {port} not allowed")
        self._check_addresses(host)
        return url

    def _check_addresses(self, host: str) -> None:
        try:
            addresses = self._resolver(host)
        except OSError as exc:
            raise BlockedURLError(f"cannot resolve {host!r}: {exc}") from exc
        if not addresses:
            raise BlockedURLError(f"cannot resolve {host!r}: no addresses")
        for raw in addresses:
            ip = _unmap(ipaddress.ip_address(raw.split("%")[0]))
            if ip in METADATA_ADDRESSES:
                raise BlockedURLError(f"{host!r} resolves to cloud metadata address {ip}")
            non_public = not ip.is_global or ip.is_multicast or ip.is_reserved
            if non_public and not self.allow_private_for_tests:
                raise BlockedURLError(f"{host!r} resolves to non-public address {ip}")
