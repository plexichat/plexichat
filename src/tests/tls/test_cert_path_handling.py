"""TLS certificate path handling (Agent 33 F7).

A relative certificate path resolves against the process cwd, which differs
between an interactive shell, systemd, and containers. ``_normalize_path``
now warns about the resolved location instead of silently writing key
material relative to a moving target. Absolute paths (including ``~``
expansion) stay quiet.
"""

from __future__ import annotations

import logging
import os

from src.core.tls import _normalize_path


def test_relative_path_is_absolutised_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="AppLogger"):
        result = _normalize_path("cert.pem")

    assert os.path.isabs(result), "the path must be absolutised"
    assert result.endswith("cert.pem")
    assert any("Relative TLS path" in record.message for record in caplog.records), (
        "a cwd-relative certificate path must be reported to the operator"
    )


def test_absolute_path_is_unchanged_without_warning(caplog, tmp_path):
    target = str(tmp_path / "cert.pem")
    with caplog.at_level(logging.WARNING, logger="AppLogger"):
        result = _normalize_path(target)

    assert result == target
    assert not any(
        "Relative TLS path" in record.message for record in caplog.records
    ), "absolute paths must not warn"


def test_home_expansion_is_absolute_without_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="AppLogger"):
        result = _normalize_path("~/plexichat/certs/cert.pem")

    assert os.path.isabs(result)
    assert "~" not in result
    assert not any(
        "Relative TLS path" in record.message for record in caplog.records
    ), "~ expansion yields an absolute path and must not warn"
