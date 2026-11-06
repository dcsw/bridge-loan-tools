"""Census geocoding behind a transport interface (design 3.2).

* :class:`CensusGeocoder` turns a Census ``onelineaddress`` JSON response into a
  :class:`GeocodeResult` (``matched`` / ``low_match`` / ``no_match``; no_match carries no
  coordinates and the loan row is kept with a null geometry).
* The transport is an interface: tests use :class:`RecordedTransport` (recorded fixtures); the real
  :class:`UrllibTransport` exists but is only used by the host-side ``python -m
  backend.ingest.geocode`` step, because the Spark ingest container has no route to the internet.
* :class:`CachedGeocoder` is what the silver job uses: cache hits are free; a miss with no inner
  geocoder is reported as ``no_match`` (not cached) and queued in ``pending`` so the host step can
  resolve it and the next silver run picks the answer up.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from backend.common.normalize import normalize_address

Status = Literal["matched", "low_match", "no_match"]
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
BENCHMARK = "Public_AR_Current"
_UNIT = re.compile(r"\b(?:SUITE|STE|UNIT|APT|APARTMENT|FL|FLOOR)\b.*$")


class GeocodeError(Exception):
    """Transport failure or an unusable response (never cached as a result)."""


@dataclass(frozen=True)
class GeocodeResult:
    status: Status
    lat: float | None
    lon: float | None


NO_MATCH = GeocodeResult("no_match", None, None)
STATUSES = ("matched", "low_match", "no_match")
log = logging.getLogger(__name__)


class Transport(Protocol):
    def get(self, address: str) -> Mapping[str, Any]: ...


class Geocoder(Protocol):
    def geocode(self, address: str | None) -> GeocodeResult: ...


def _street(address: str) -> str:
    """Normalized street part (before the first comma) without unit designators."""
    head = normalize_address(address.split(",", 1)[0]) or ""
    return _UNIT.sub("", head).strip()


class CensusGeocoder:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def geocode(self, address: str | None) -> GeocodeResult:
        if address is None or not address.strip():
            return NO_MATCH
        payload = self._transport.get(address)
        try:
            matches = payload["result"]["addressMatches"]
        except (KeyError, TypeError) as exc:
            raise GeocodeError("response has no result.addressMatches") from exc
        if not matches:
            return NO_MATCH
        first = matches[0]
        try:
            lon, lat = float(first["coordinates"]["x"]), float(first["coordinates"]["y"])
            matched_address = str(first.get("matchedAddress", ""))
        except (KeyError, TypeError, ValueError) as exc:
            raise GeocodeError("address match lacks coordinates") from exc
        exact = len(matches) == 1 and _street(matched_address) == _street(address)
        return GeocodeResult("matched" if exact else "low_match", lat, lon)


class RecordedTransport:
    """Serves recorded Census responses keyed by the exact request address."""

    def __init__(self, responses: Mapping[str, Mapping[str, Any]]) -> None:
        self._responses = dict(responses)

    def get(self, address: str) -> Mapping[str, Any]:
        try:
            return self._responses[address]
        except KeyError as exc:
            raise GeocodeError(f"no recorded response for {address!r}") from exc


def _urlopen_read(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


class UrllibTransport:
    """The real Census HTTP client. Never used by tests (the autouse socket guard blocks it)."""

    def __init__(
        self, opener: Callable[[str, float], bytes] = _urlopen_read, timeout: float = 30.0
    ) -> None:
        self._opener = opener
        self._timeout = timeout

    def get(self, address: str) -> Mapping[str, Any]:
        query = urllib.parse.urlencode(
            {"address": address, "benchmark": BENCHMARK, "format": "json"}
        )
        try:
            return json.loads(self._opener(f"{CENSUS_URL}?{query}", self._timeout))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            raise GeocodeError(f"census request failed: {type(exc).__name__}") from exc


def _parse_cache_line(line: str) -> tuple[str, GeocodeResult]:
    row = json.loads(line)
    address, status = row["address"], row["status"]
    if not isinstance(address, str) or status not in STATUSES:
        raise ValueError("bad address/status")
    lat, lon = row.get("lat"), row.get("lon")
    if status == "no_match":
        if lat is not None or lon is not None:
            raise ValueError("no_match must not carry coordinates")
    elif not (
        isinstance(lat, int | float)
        and isinstance(lon, int | float)
        and -90 <= lat <= 90
        and -180 <= lon <= 180
    ):
        raise ValueError("coordinates missing or out of range")
    return address, GeocodeResult(status, lat, lon)


class JsonlGeocodeCache:
    """Append-only ``address -> result`` cache, one JSON object per line."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._data: dict[str, GeocodeResult] = {}
        self.rejected = 0
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                try:
                    address, result = _parse_cache_line(line)
                except (ValueError, KeyError, TypeError):
                    self.rejected += 1  # a torn, foreign or implausible line must not poison it
                    continue
                self._data[address] = result
            if self.rejected:
                log.warning("geocode cache: skipped %d malformed entr(ies)", self.rejected)

    def get(self, address: str) -> GeocodeResult | None:
        return self._data.get(address)

    def put(self, address: str, result: GeocodeResult) -> None:
        self._data[address] = result
        self._path.parent.mkdir(parents=True, exist_ok=True)
        row = {"address": address, "status": result.status, "lat": result.lat, "lon": result.lon}
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


class CachedGeocoder:
    def __init__(self, cache: JsonlGeocodeCache, inner: Geocoder | None = None) -> None:
        self._cache = cache
        self._inner = inner
        self.pending: set[str] = set()

    def geocode(self, address: str | None) -> GeocodeResult:
        if address is None or not address.strip():
            return NO_MATCH
        hit = self._cache.get(address)
        if hit is not None:
            return hit
        if self._inner is not None:
            try:
                result = self._inner.geocode(address)
            except GeocodeError:
                pass
            else:
                self._cache.put(address, result)
                return result
        self.pending.add(address)
        return NO_MATCH


def main(argv: Sequence[str] | None = None, transport: Transport | None = None) -> int:
    """Host-side step: resolve queued addresses via the Census API into the cache file."""
    parser = argparse.ArgumentParser(prog="backend.ingest.geocode")
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args(argv)
    geocoder = CensusGeocoder(transport or UrllibTransport())
    cache = JsonlGeocodeCache(args.cache)
    failures = 0
    for line in args.requests.read_text(encoding="utf-8").splitlines():
        address = line.strip()
        if not address or cache.get(address) is not None:
            continue
        try:
            cache.put(address, geocoder.geocode(address))
        except GeocodeError as exc:
            failures += 1
            print(f"geocode failed address_len={len(address)}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
