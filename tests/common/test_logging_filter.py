"""Log redaction filter hides borrower names."""

import logging

from backend.common.logging import REDACTED, RedactionFilter, get_logger


def _capture(filter_):
    records = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger(f"t.{id(filter_)}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    h = H()
    h.addFilter(filter_)
    logger.addHandler(h)
    return logger, records


def test_redacts_registered_names_in_message_and_args():
    f = RedactionFilter(["Jane Q. Doe", "John Roe"])
    logger, out = _capture(f)
    logger.info("loan for Jane Q. Doe and %s", "john roe")
    assert out == ["loan for [REDACTED] and [REDACTED]"]


def test_redacts_key_value_patterns():
    f = RedactionFilter()
    logger, out = _capture(f)
    logger.info('borrower=Alice Smith lender=ACME grantor: "Bob Jones", {"grantee": "Carl X"}')
    assert "Alice" not in out[0] and "Bob" not in out[0] and "Carl" not in out[0]
    assert "ACME" in out[0]


def test_register_later_and_empty_ignored():
    f = RedactionFilter()
    f.register("")
    f.register("Zed Zorro")
    logger, out = _capture(f)
    logger.info("hi Zed Zorro")
    assert out == ["hi [REDACTED]"]


def test_get_logger_installs_filter_once():
    a = get_logger("wu2.x", names=["Q R"])
    b = get_logger("wu2.x", names=["S T"])
    assert a is b
    n = sum(isinstance(x, RedactionFilter) for h in a.handlers for x in h.filters)
    assert n == 1


def test_redacts_names_in_exception_tracebacks():
    f = RedactionFilter(["Jane Q. Doe"])
    logger = logging.getLogger("t.exc")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    lines = []

    class H(logging.Handler):
        def emit(self, record):
            lines.append(self.format(record))

    h = H()
    h.addFilter(f)
    logger.addHandler(h)
    try:
        raise ValueError("bad loan for Jane Q. Doe")
    except ValueError:
        logger.exception("failed")
    assert "Traceback" in lines[0] and "Jane" not in lines[0] and REDACTED in lines[0]
    # pre-rendered exc_text is scrubbed too
    rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "m", None, None)
    rec.exc_text = "Traceback ... Jane Q. Doe"
    f.filter(rec)
    assert "Jane" not in rec.exc_text
