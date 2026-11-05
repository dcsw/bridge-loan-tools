"""Local fake records portal for fetcher tests. Serves ONLY synthetic fixture PDFs.

Routes
  /pdfs/<name>.pdf        fixture PDF from tests/fixtures/pdfs
  /captcha                200 HTML CAPTCHA challenge
  /paid                   402 payment required
  /forbidden, /error      403, 500
  /notpdf                 200 HTML that is not a CAPTCHA
  /slow?delay=<s>         responds only after <s> seconds (timeout simulation)
  /ratelimit?n=<k>&retry_after=<s>
                          first <k> hits per path get 429 + Retry-After, then a PDF
  /redirect/pdf           302 -> /pdfs/mortgage.pdf
  /redirect/internal      302 -> http://169.254.169.254/latest/meta-data/
  /redirect/loopback-other 302 -> http://127.0.0.2/
  /redirect/loop          302 -> itself

Run standalone: ``python -m tests.fakeportal.server --host 0.0.0.0 --port 8080``.
"""

from __future__ import annotations

import argparse
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PDF_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"

CAPTCHA_HTML = (
    b"<html><body><h1>Verify you are human</h1>"
    b"<div class='g-recaptcha'>Please complete the CAPTCHA to continue</div></body></html>"
)


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format, *args):  # silence test output
        return

    def _send(self, status, body=b"", ctype="text/html", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        self._send(302, headers={"Location": location})

    def _pdf(self, name):
        path = PDF_DIR / name
        if "/" in name or not name.endswith(".pdf") or not path.is_file():
            return self._send(404, b"not found")
        self._send(200, path.read_bytes(), "application/pdf")

    def do_GET(self):
        parts = urlsplit(self.path)
        path, query = parts.path, parse_qs(parts.query)
        self.server.hits[path] += 1
        if path.startswith("/pdfs/"):
            return self._pdf(path.removeprefix("/pdfs/"))
        routes = {
            "/captcha": lambda: self._send(200, CAPTCHA_HTML),
            "/paid": lambda: self._send(402, b"payment required"),
            "/forbidden": lambda: self._send(403, b"forbidden"),
            "/error": lambda: self._send(500, b"server error"),
            "/notpdf": lambda: self._send(200, b"<html><body>search results</body></html>"),
            "/redirect/pdf": lambda: self._redirect("/pdfs/mortgage.pdf"),
            "/redirect/internal": lambda: self._redirect(
                "http://169.254.169.254/latest/meta-data/"
            ),
            "/redirect/loopback-other": lambda: self._redirect("http://127.0.0.2/"),
            "/redirect/loop": lambda: self._redirect("/redirect/loop"),
        }
        if path in routes:
            return routes[path]()
        if path == "/slow":
            delay = float(query.get("delay", ["5"])[0])
            self.server.stopping.wait(delay)
            return self._send(200, b"too late")
        if path == "/ratelimit":
            limit = int(query.get("n", ["1"])[0])
            if self.server.hits[path] <= limit:
                retry_after = query.get("retry_after", ["1"])[0]
                return self._send(429, b"slow down", headers={"Retry-After": retry_after})
            return self._pdf("mortgage.pdf")
        self._send(404, b"not found")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    hits: Counter
    stopping: threading.Event


class FakePortal:
    """Context-managed fake portal bound to 127.0.0.1 on an ephemeral port by default."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._server = _Server((host, port), _Handler)
        self._server.hits = Counter()
        self._server.stopping = threading.Event()
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._closed = False
        self.host = host

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def hits(self) -> Counter:
        return self._server.hits

    def url(self, path: str) -> str:
        return f"http://{self.host}:{self.port}{path}"

    def start(self) -> FakePortal:
        self._thread.start()
        return self

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._server.stopping.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> FakePortal:
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.close()


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - manual use
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    portal = FakePortal(args.host, args.port)
    print(f"fake portal on {portal.url('/')}")
    portal._server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
