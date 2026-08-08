"""Targeted tests for ``src.utils.email.SMTPEmailSender``."""

import smtplib

import pytest

from src.utils.email import SMTPEmailSender


class FakeSMTP:
    instances = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in = False
        self.sent = []
        self.closed = False
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def _sender(**kw):
    defaults = dict(
        host="smtp.example.com",
        port=587,
        user="user",
        password="pass",
        from_email="noreply@example.com",
        use_tls=True,
    )
    defaults.update(kw)
    return SMTPEmailSender(**defaults)


def test_send_plain_success(fake_smtp):
    s = _sender()
    assert s.send("to@example.com", "Subject", "body") is True
    inst = fake_smtp.instances[0]
    assert inst.host == "smtp.example.com"
    assert inst.port == 587
    assert inst.started_tls is True
    assert inst.logged_in == ("user", "pass")
    assert len(inst.sent) == 1
    msg = inst.sent[0]
    assert msg["Subject"] == "Subject"
    assert msg["From"] == "noreply@example.com"
    assert msg["To"] == "to@example.com"
    assert inst.closed is True


def test_send_html_content_type(fake_smtp):
    s = _sender()
    assert s.send("to@example.com", "S", "<b>hi</b>", html=True) is True
    msg = fake_smtp.instances[0].sent[0]
    payload = msg.get_payload()[0]
    assert payload.get_content_type() == "text/html"


def test_send_plain_content_type(fake_smtp):
    s = _sender()
    s.send("to@example.com", "S", "plain body")
    msg = fake_smtp.instances[0].sent[0]
    payload = msg.get_payload()[0]
    assert payload.get_content_type() == "text/plain"


def test_no_tls_no_login_without_credentials(fake_smtp):
    s = _sender(use_tls=False, user="", password="")
    assert s.send("to@example.com", "S", "b") is True
    inst = fake_smtp.instances[0]
    assert inst.started_tls is False
    assert inst.logged_in is False


def test_no_login_when_credentials_empty_but_tls(fake_smtp):
    s = _sender(user="", password="")
    s.send("to@example.com", "S", "b")
    inst = fake_smtp.instances[0]
    assert inst.started_tls is True
    assert inst.logged_in is False


def test_smtp_connection_error_returns_false(fake_smtp, monkeypatch):
    def boom(*a, **k):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    s = _sender()
    assert s.send("to@example.com", "S", "b") is False


@pytest.mark.parametrize("fail_stage", ["starttls", "login", "send"])
def test_smtp_stage_failures_return_false(fake_smtp, monkeypatch, fail_stage):
    import src.utils.email as _email

    class TlsFail(FakeSMTP):
        def starttls(self, context=None):
            raise smtplib.SMTPException("tls failed")

    class LoginFail(FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad")

    class SendFail(FakeSMTP):
        def send_message(self, msg):
            raise smtplib.SMTPSenderRefused(550, b"denied", "noreply@example.com")

    culprit = {"starttls": TlsFail, "login": LoginFail, "send": SendFail}[fail_stage]
    monkeypatch.setattr(_email.smtplib, "SMTP", culprit)
    assert _sender().send("to@example.com", "S", "b") is False


def test_multiple_recipients_each_call(fake_smtp):
    s = _sender()
    assert s.send("a@example.com", "S", "b") is True
    assert s.send("b@example.com", "S", "b") is True
    assert len(fake_smtp.instances) == 2
