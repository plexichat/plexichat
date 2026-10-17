"""Dense matrix tests for ``SMTPEmailSender`` (TLS, auth, MIME shape, failures)."""

import smtplib

import pytest

from src.utils.mailer import SMTPEmailSender


class FakeSMTP:
    """Records calls instead of talking to a real server."""

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.starttls_calls = 0
        self.login_args = None
        self.sent_messages = []
        self.fail_starttls = None
        self.fail_send = None
        self.fail_login = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.starttls_calls += 1
        if self.fail_starttls:
            raise self.fail_starttls

    def login(self, user, password):
        self.login_args = (user, password)
        if self.fail_login:
            raise self.fail_login

    def send_message(self, msg):
        if self.fail_send:
            raise self.fail_send
        self.sent_messages.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch):
    server = FakeSMTP("smtp.example.com", 587)
    monkeypatch.setattr(smtplib, "SMTP", lambda host, port: server)
    return server


@pytest.fixture
def sender():
    return SMTPEmailSender(
        host="smtp.example.com",
        port=587,
        user="u",
        password="p",
        from_email="no-reply@example.com",
        use_tls=True,
    )


def test_success_plain(fake_smtp, sender):
    assert sender.send("user@example.com", "Subj", "Body") is True
    assert (fake_smtp.host, fake_smtp.port) == ("smtp.example.com", 587)
    assert len(fake_smtp.sent_messages) == 1


def test_message_headers(fake_smtp, sender):
    sender.send("user@example.com", "The Subject", "Body")
    msg = fake_smtp.sent_messages[0]
    assert msg["To"] == "user@example.com"
    assert msg["From"] == "no-reply@example.com"
    assert msg["Subject"] == "The Subject"
    assert msg.get_content_type() == "multipart/alternative"


def test_plain_part_payload_and_type(fake_smtp, sender):
    sender.send("a@b.c", "s", "plain body")
    part = fake_smtp.sent_messages[0].get_payload()[0]
    assert part.get_content_type() == "text/plain"
    assert part.get_payload() == "plain body"


def test_html_part_type(fake_smtp, sender):
    sender.send("a@b.c", "s", "<b>html</b>", html=True)
    part = fake_smtp.sent_messages[0].get_payload()[0]
    assert part.get_content_type() == "text/html"
    assert part.get_payload() == "<b>html</b>"


def test_tls_used_by_default(fake_smtp, sender):
    sender.send("a@b.c", "s", "x")
    assert fake_smtp.starttls_calls == 1


@pytest.mark.parametrize("use_tls", [True, False])
def test_tls_flag_controls_starttls(fake_smtp, use_tls):
    s = SMTPEmailSender("h", 587, "", "", "from@x", use_tls=use_tls)
    s.send("a@b.c", "s", "x")
    assert fake_smtp.starttls_calls == (1 if use_tls else 0)


def test_login_when_credentials_present(fake_smtp, sender):
    sender.send("a@b.c", "s", "x")
    assert fake_smtp.login_args == ("u", "p")


@pytest.mark.parametrize(
    ("user", "password"),
    [
        ("", ""),
        ("u", ""),
        ("", "p"),
    ],
)
def test_no_login_without_full_credentials(fake_smtp, user, password):
    s = SMTPEmailSender("h", 587, user, password, "from@x")
    s.send("a@b.c", "s", "x")
    assert fake_smtp.login_args is None


def test_connection_failure_returns_false(monkeypatch):
    def boom(host, port):
        raise OSError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    sender = SMTPEmailSender("h", 587, "", "", "from@x")
    assert sender.send("a@b.c", "s", "x") is False


def test_send_failure_returns_false(fake_smtp, sender):
    fake_smtp.fail_send = RuntimeError("smtp 550")
    assert sender.send("a@b.c", "s", "x") is False
    # The with-block still exits cleanly, but no message is recorded.
    assert fake_smtp.sent_messages == []


def test_starttls_failure_returns_false(fake_smtp, sender):
    fake_smtp.fail_starttls = OSError("tls handshake failed")
    assert sender.send("a@b.c", "s", "x") is False


def test_login_failure_returns_false(fake_smtp, sender):
    fake_smtp.fail_login = smtplib.SMTPAuthenticationError(535, b"auth failed")
    assert sender.send("a@b.c", "s", "x") is False


def test_empty_body_and_subject_ok(fake_smtp, sender):
    assert sender.send("a@b.c", "", "") is True
