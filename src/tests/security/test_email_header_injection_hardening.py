"""SMTP header-injection regression tests.

Python's ``email.message.Message`` does not reject CR/LF in header values, so a
single unsanitised value reaching :class:`SMTPEmailSender` can serialise as
multiple headers (an arbitrary ``Bcc``/``Reply-To``/``Subject`` injection).
The mailer is the last boundary before ``smtplib`` and must fail closed.
"""

from unittest.mock import patch

import pytest

from src.utils.mailer import SMTPEmailSender, _safe_header


def _sender(**overrides):
    kwargs = dict(
        host="smtp.example.com",
        port=587,
        user="user@example.com",
        password="password",
        from_email="noreply@example.com",
    )
    kwargs.update(overrides)
    return SMTPEmailSender(**kwargs)


@pytest.mark.parametrize(
    "payload",
    [
        "victim@example.com\r\nBcc: attacker@evil.test",
        "victim@example.com\nBcc: attacker@evil.test",
        "victim@example.com\x00\r\nBcc: attacker@evil.test",
        "subject\r\nReply-To: attacker@evil.test",
        "bad\x1b[31mvalue",
        "bad\x7fvalue",
    ],
)
def test_safe_header_rejects_control_characters(payload):
    with pytest.raises(ValueError, match="control characters"):
        _safe_header(payload, "header")


def test_safe_header_rejects_non_string():
    with pytest.raises(ValueError, match="must be a string"):
        _safe_header(b"victim@example.com", "to")


def test_safe_header_allows_plain_and_tab_values():
    assert _safe_header("victim@example.com", "to") == "victim@example.com"
    assert _safe_header("Hello\tworld", "subject") == "Hello\tworld"
    assert _safe_header("", "subject") == ""


def test_from_email_injection_rejected_at_construction():
    with pytest.raises(ValueError, match="control characters"):
        _sender(from_email="noreply@example.com\r\nBcc: attacker@evil.test")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"to": "victim@example.com\r\nBcc: attacker@evil.test"},
        {"subject": "Hello\r\nBcc: attacker@evil.test"},
        {"to": "victim@example.com\x00Bcc: x"},
    ],
)
def test_send_fails_closed_on_injected_headers(kwargs):
    """A CRLF/NUL header must never reach ``smtplib.send_message``."""
    send = dict(to="victim@example.com", subject="Hello", body="Body")
    send.update(kwargs)

    with patch("smtplib.SMTP") as mock_smtp:
        sender = _sender()
        assert sender.send(**send) is False
        instance = mock_smtp.return_value.__enter__.return_value
        instance.send_message.assert_not_called()


def test_send_legitimate_mail_still_works():
    with patch("smtplib.SMTP") as mock_smtp:
        sender = _sender()
        assert sender.send("victim@example.com", "Hello", "Body") is True
        instance = mock_smtp.return_value.__enter__.return_value
        instance.send_message.assert_called_once()
        message = instance.send_message.call_args.args[0]
        assert message["To"] == "victim@example.com"
        assert message["Subject"] == "Hello"
