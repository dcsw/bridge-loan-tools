"""No borrower names in API logs, including exception tracebacks, end to end."""

from __future__ import annotations

import io
import socket
import threading
import time

import httpx
import pyarrow.parquet as pq
import pytest
import uvicorn

from backend.common.snapshot import DETAIL_FILE
from tests.api.conftest import AUTH_HEADERS


def _first(snap):
    row = pq.read_table(snap / DETAIL_FILE).to_pylist()[0]
    return row["loan_id"], row["borrower"]


def test_name_registered_after_lookup_is_redacted_in_handled_error_traceback(
    small_snapshot, make_client
):
    snap, _ = small_snapshot
    loan_id, borrower = _first(snap)
    buf = io.StringIO()
    client = make_client(snap, app_kwargs={"log_stream": buf})

    def failing_audit(*_a, **_k):
        raise OSError(f"cannot write audit for {borrower}")

    client.app.state.audit.record = failing_audit
    r = client.get(f"/loans/{loan_id}")
    assert r.status_code == 503
    logs = buf.getvalue()
    assert "Traceback" in logs and "cannot write audit" in logs
    assert borrower not in logs and borrower.lower() not in logs.lower()
    assert "[REDACTED]" in logs
    assert borrower not in r.text


def test_unhandled_exception_traceback_is_redacted_and_not_reraised(small_snapshot, make_client):
    snap, _ = small_snapshot
    loan_id, borrower = _first(snap)
    buf = io.StringIO()
    client = make_client(snap, app_kwargs={"log_stream": buf})
    real = client.app.state.store.current

    def explode():
        real()
        raise RuntimeError(f"row exploded: borrower: {borrower}")

    client.app.state.store.current = explode
    r = client.get(f"/loans/{loan_id}")
    assert r.status_code == 500
    logs = buf.getvalue()
    assert "Traceback" in logs and "row exploded" in logs
    assert borrower not in logs and borrower not in r.text


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_end_to_end_over_real_uvicorn_server(small_snapshot, make_client):
    """Detail request via a live ASGI server: nothing (incl. uvicorn loggers) leaks the name."""
    snap, _ = small_snapshot
    loan_id, borrower = _first(snap)
    buf = io.StringIO()
    client = make_client(snap, app_kwargs={"log_stream": buf})
    app = client.app

    def failing_audit(*_a, **_k):
        raise OSError(f"audit failed for {borrower}")

    app.state.audit.record = failing_audit
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, log_level="debug")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        r = httpx.get(f"http://127.0.0.1:{port}/loans/{loan_id}", headers=AUTH_HEADERS)
        assert r.status_code == 503
        r2 = httpx.get(f"http://127.0.0.1:{port}/loans/{loan_id}/../x", headers=AUTH_HEADERS)
        assert r2.status_code in (400, 404)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    logs = buf.getvalue()
    assert borrower not in logs
    assert "Traceback" in logs


def test_redaction_filter_installed_on_all_api_and_uvicorn_loggers():
    import logging

    from backend.api.logging_setup import configure_logging

    buf = io.StringIO()
    configure_logging(buf)
    for name in ("bridge_loan.api", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).error("borrower: Zelda Zimmer failed")
    out = buf.getvalue()
    assert "Zelda" not in out and out.count("[REDACTED]") == 4


def test_configure_logging_is_idempotent():
    import logging

    from backend.api.logging_setup import configure_logging

    buf = io.StringIO()
    configure_logging(buf)
    configure_logging(buf)
    logging.getLogger("bridge_loan.api").info("once")
    assert buf.getvalue().count("once") == 1


@pytest.fixture(autouse=True)
def _reset_logging():
    yield
    import logging

    for name in ("bridge_loan.api", "uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            lg.removeHandler(h)
