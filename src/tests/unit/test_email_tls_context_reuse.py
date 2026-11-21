"""Regression tests for the SMTP sender's bounded TLS-context reuse.

Before the fix, ``SMTPEmailSender.send`` called ``ssl.create_default_context()``
on *every* send -- even for non-TLS senders that never used it. Building that
context loads and parses the system trust store, which is the dominant cost of
a short email send and was paid once per verification/reset/notification mail.

The context is now built lazily (only when TLS is enabled) and cached once per
sender instance under double-checked locking. It is never process-global, so
no configuration or credential state can cross sender/worker boundaries.
"""

import smtplib
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor

from src.utils.mailer import SMTPEmailSender


class RecordingSMTP:
    """Context-manager SMTP double that records the context passed to STARTTLS."""

    def __init__(self, host, port, contexts, lock):
        self.host = host
        self.port = port
        self._contexts = contexts
        self._lock = lock

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        with self._lock:
            self._contexts.append(context)

    def login(self, user, password):
        return None

    def send_message(self, msg):
        return None


def _install_smtp(monkeypatch):
    contexts = []
    lock = threading.Lock()

    def factory(host, port):
        return RecordingSMTP(host, port, contexts, lock)

    monkeypatch.setattr(smtplib, "SMTP", factory)
    return contexts


def _sender(**overrides):
    kwargs = dict(
        host="smtp.example.com",
        port=587,
        user="user@example.com",
        password="password",
        from_email="noreply@example.com",
        use_tls=True,
    )
    kwargs.update(overrides)
    return SMTPEmailSender(**kwargs)


def test_context_built_once_and_reused_across_sends(monkeypatch):
    contexts = _install_smtp(monkeypatch)
    calls = []

    def fake_context():
        calls.append(object())
        return calls[-1]

    monkeypatch.setattr(ssl, "create_default_context", fake_context)
    sender = _sender()

    for i in range(6):
        assert sender.send(f"user{i}@example.com", "Subject", "Body") is True

    # Before: 6 trust-store loads (one per send). After: exactly one.
    assert len(calls) == 1
    assert len(contexts) == 6
    assert all(ctx is calls[0] for ctx in contexts)


def test_no_context_built_when_tls_disabled(monkeypatch):
    contexts = _install_smtp(monkeypatch)

    def forbidden():
        raise AssertionError("trust store loaded for a non-TLS sender")

    monkeypatch.setattr(ssl, "create_default_context", forbidden)
    sender = _sender(use_tls=False)

    assert sender.send("user@example.com", "Subject", "Body") is True
    assert contexts == []


def test_concurrent_sends_build_exactly_one_context(monkeypatch):
    contexts = _install_smtp(monkeypatch)
    calls = []
    call_lock = threading.Lock()

    def fake_context():
        with call_lock:
            calls.append(object())
            return calls[-1]

    monkeypatch.setattr(ssl, "create_default_context", fake_context)
    sender = _sender()
    barrier = threading.Barrier(8)

    def send(i):
        barrier.wait()
        return sender.send(f"user{i}@example.com", "Subject", "Body")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(send, range(8)))

    assert all(results)
    assert len(calls) == 1
    assert len(contexts) == 8
    assert all(ctx is calls[0] for ctx in contexts)


def test_distinct_senders_do_not_share_context(monkeypatch):
    contexts = _install_smtp(monkeypatch)
    counter = []

    def fake_context():
        counter.append(object())
        return counter[-1]

    monkeypatch.setattr(ssl, "create_default_context", fake_context)

    first = _sender(from_email="one@example.com")
    second = _sender(from_email="two@example.com")
    assert first.send("a@example.com", "s", "b") is True
    assert second.send("b@example.com", "s", "b") is True

    # One cache entry per instance: no cross-sender (or cross-tenant) reuse.
    assert len(counter) == 2
    assert contexts[0] is counter[0]
    assert contexts[1] is counter[1]
    assert contexts[0] is not contexts[1]


def test_failed_context_creation_is_retried(monkeypatch):
    contexts = _install_smtp(monkeypatch)
    calls = []

    def flaky_context():
        calls.append(object())
        if len(calls) == 1:
            raise OSError("trust store temporarily unavailable")
        return calls[-1]

    monkeypatch.setattr(ssl, "create_default_context", flaky_context)
    sender = _sender()

    # A failed creation is not cached, so the sender fails closed on that send
    # and recovers on the next one instead of being stuck permanently.
    assert sender.send("a@example.com", "s", "b") is False
    assert sender.send("a@example.com", "s", "b") is True
    assert len(calls) == 2
    assert contexts == [calls[1]]
