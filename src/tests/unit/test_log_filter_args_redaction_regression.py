"""Regression: the shared log filter must sanitize lazy-format args, not just msg.

``SanitizingFilter`` used to inspect only ``record.msg``. Most call sites log
lazily (``logger.info("token %s", token)``) or with a mapping
(``logger.info("%(password)s", data)``), so the credential lives in
``record.args`` and is substituted by the Formatter *after* every filter has
run. That let secrets reach the log files verbatim (CWE-532) even though the
filter advertised PII redaction. Exception tracebacks and explicit stack info
are likewise rendered by the Formatter from fields a filter never sees.

These tests drive a real ``logging.Logger`` through the filter into a
``StringIO`` sink, i.e. the same path a file handler uses.
"""

from __future__ import annotations

import io
import logging
import uuid

import pytest

from src.utils.common_utils.utils.logger.core import SanitizingFilter

pytestmark = pytest.mark.unit

_BEARER_TOKEN = "supersecretbearertoken123456"
_PASSWORD = "hunter2supersecretpw"


def _filtered_logger():
    """A private logger wired exactly like the app's file logger."""
    stream = io.StringIO()
    logger = logging.getLogger(f"harden.logfilter.{uuid.uuid4().hex}")
    logger.handlers.clear()
    logger.filters.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.addFilter(SanitizingFilter())
    return logger, stream


def test_lazy_positional_arg_secret_is_redacted():
    logger, stream = _filtered_logger()

    logger.info("Authorization: Bearer %s", _BEARER_TOKEN)

    rendered = stream.getvalue()
    assert _BEARER_TOKEN not in rendered
    assert "********" in rendered


def test_mapping_arg_secret_is_redacted_by_key():
    logger, stream = _filtered_logger()

    logger.info(
        "login user=%(username)s password=%(password)s",
        {"username": "bob", "password": _PASSWORD},
    )

    rendered = stream.getvalue()
    assert _PASSWORD not in rendered
    assert "bob" in rendered
    assert "********" in rendered


def test_exception_traceback_secret_is_redacted():
    logger, stream = _filtered_logger()

    try:
        raise RuntimeError(f"failed with password={_PASSWORD}; Bearer {_BEARER_TOKEN}")
    except RuntimeError:
        logger.exception("operation failed")

    rendered = stream.getvalue()
    assert "operation failed" in rendered
    assert _PASSWORD not in rendered
    assert _BEARER_TOKEN not in rendered


def test_preformatted_stack_info_is_redacted():
    record = logging.LogRecord(
        "AppLogger",
        logging.ERROR,
        __file__,
        1,
        "event",
        (),
        None,
        stack_info=f"Stack trace password={_PASSWORD}",
    )
    assert SanitizingFilter().filter(record) is True
    formatted = logging.Formatter("%(message)s\n%(stack_info)s").format(record)
    assert _PASSWORD not in formatted


def test_normal_messages_are_not_weakened():
    """The fix must not corrupt ordinary, secret-free log lines."""
    logger, stream = _filtered_logger()

    logger.info("user %s joined channel %d", "alice", 42)

    rendered = stream.getvalue()
    assert "user alice joined channel 42" in rendered
