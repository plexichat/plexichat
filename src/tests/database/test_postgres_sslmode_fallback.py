"""sslmode fail-closed tests for the pg8000 PostgreSQL engine.

``_create_single_connection`` retries a failed TLS handshake once WITHOUT the
SSL context. That is only correct for the opportunistic ``prefer`` mode, where
libpq is allowed to fall back to cleartext. ``require``, ``verify-ca`` and
``verify-full`` all demand TLS, so a handshake failure there must propagate:
silently reconnecting in the clear would put the password and every row on an
unencrypted connection the operator explicitly forbade.

No live PostgreSQL is needed — only ``pg8000`` for its module object, whose
``connect`` is replaced with a recorder.
"""

from __future__ import annotations

import pytest

pg8000_dbapi = pytest.importorskip("pg8000.dbapi")

from src.core.database.engines.postgres import PostgresEngine  # noqa: E402


class _FakeConn:
    """Minimal stand-in for a pg8000 connection object."""


def _engine(sslmode: str) -> PostgresEngine:
    return PostgresEngine({"postgres": {"sslmode": sslmode}, "connection_pool": {}})


def _connect_recorder(monkeypatch, message: str = "could not connect: ssl error"):
    """Patch ``pg8000.dbapi.connect`` to fail once, then succeed; return calls."""
    calls = []

    def fake_connect(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError(message)
        return _FakeConn()

    monkeypatch.setattr(pg8000_dbapi, "connect", fake_connect)
    return calls


def test_prefer_falls_back_to_plaintext(monkeypatch):
    """sslmode=prefer keeps its documented plaintext fallback."""
    calls = _connect_recorder(monkeypatch)

    _engine("prefer")._create_single_connection()

    assert len(calls) == 2
    assert "ssl_context" in calls[0]
    # The retry must have dropped the SSL context.
    assert "ssl_context" not in calls[1]


@pytest.mark.parametrize("sslmode", ["require", "verify-ca", "verify-full"])
def test_tls_requiring_modes_fail_closed(monkeypatch, sslmode):
    """require/verify-* must NOT be silently downgraded to cleartext."""
    calls = _connect_recorder(monkeypatch)

    with pytest.raises(RuntimeError):
        _engine(sslmode)._create_single_connection()

    # Exactly one attempt: no ssl_context-less retry happened.
    assert len(calls) == 1
    assert "ssl_context" in calls[0]


def test_uppercase_prefer_still_falls_back(monkeypatch):
    """The mode comparison is case-insensitive, like _build_ssl_context."""
    calls = _connect_recorder(monkeypatch)

    _engine("PREFER")._create_single_connection()

    assert len(calls) == 2
    assert "ssl_context" not in calls[1]


def test_non_ssl_error_is_never_retried(monkeypatch):
    """An auth failure in prefer mode is not a TLS problem — do not retry."""
    calls = _connect_recorder(monkeypatch, message="authentication failed")

    with pytest.raises(RuntimeError):
        _engine("prefer")._create_single_connection()

    assert len(calls) == 1
