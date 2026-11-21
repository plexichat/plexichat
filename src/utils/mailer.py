"""
Email utilities for Plexichat.

Deliberately NOT named ``email.py``: ``src/utils`` is an import root (it is
on ``pytest.ini``'s ``pythonpath``), so a module called ``email`` there
shadows the standard-library ``email`` *package*. Any interpreter that
imports ``smtplib`` before the stdlib package does -- a freshly spawned
``multiprocessing`` child, for one -- then resolves ``email.utils`` back to
this file and dies with ``ModuleNotFoundError: No module named
'email.utils'``, because this module is not a package.
"""

import re
import smtplib
import ssl
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import utils.logger as logger


# ``email.message.Message.__setitem__`` does NOT reject CR/LF in header values:
# ``msg["To"] = "a@b.com\r\nBcc: attacker@evil.test"`` is serialised as two
# separate headers. Any value that reaches this mailer from an upstream caller
# is therefore a potential header-injection primitive (extra Bcc/Subject/
# Reply-To headers, phishing relay through the server's trusted SMTP account).
# This is the last boundary before the message is handed to smtplib, so enforce
# it here even though the current auth callers happen to pre-validate addresses.
# Tab (0x09) is the only C0 character permitted in an RFC 5322 header value.
_HEADER_CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


def _safe_header(value: object, name: str) -> str:
    """Return a header value that cannot inject additional SMTP headers.

    Raises ``ValueError`` fail-closed on non-strings or embedded control
    characters (CR, LF, NUL, ESC, DEL, ...). Tab is allowed because it is a
    legal part of a folded header value.
    """
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if _HEADER_CONTROL_RE.search(value):
        raise ValueError(f"{name} contains control characters")
    return value


class SMTPEmailSender:
    """SMTP implementation of the EmailSender protocol."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        from_email: str,
        use_tls: bool = True,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        # Validate the operator-supplied From address once, at construction,
        # so a misconfigured value cannot become an injection vector later.
        self.from_email = _safe_header(from_email, "from_email")
        self.use_tls = use_tls
        # Building an SSL context loads and parses the whole system trust
        # store, which dominates the cost of a short SMTP send. The sender is
        # a long-lived singleton (see server.initializer), so build once and
        # reuse. The cache is per instance, never process-global: no config or
        # credential state can bleed between senders or server workers.
        self._ssl_context: ssl.SSLContext | None = None
        self._ssl_context_lock = threading.Lock()

    def _get_ssl_context(self) -> ssl.SSLContext:
        """Return this sender's TLS context, creating it on first use.

        Double-checked locking bounds creation to exactly one context per
        sender even when several request threads send mail concurrently. A
        failed creation is never cached, so a later send simply retries.
        """
        context = self._ssl_context
        if context is None:
            with self._ssl_context_lock:
                context = self._ssl_context
                if context is None:
                    context = ssl.create_default_context()
                    self._ssl_context = context
        return context

    def send(self, to: str, subject: str, body: str, html: bool = False) -> bool:
        """
        Send an email via SMTP.

        Args:
            to: Recipient email address
            subject: Email subject
            body: Email body (text or HTML)
            html: Whether the body is HTML

        Returns:
            True if successful, False otherwise
        """
        try:
            # Validate every value that becomes an SMTP header. Without this,
            # a caller passing CRLF in ``to``/``subject`` would let smtplib
            # serialise injected Bcc/Subject headers into the outgoing mail.
            to = _safe_header(to, "to")
            subject = _safe_header(subject, "subject")
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.from_email
            msg["To"] = to

            part = MIMEText(body, "html" if html else "plain")
            msg.attach(part)

            # Using synchronous smtplib; in FastAPI this should be run in a threadpool
            with smtplib.SMTP(self.host, self.port) as server:
                if self.use_tls:
                    # Built only when TLS is actually used, then reused on
                    # every subsequent send (see _get_ssl_context).
                    server.starttls(context=self._get_ssl_context())

                if self.user and self.password:
                    server.login(self.user, self.password)

                server.send_message(msg)

            logger.info(f"Email sent successfully to {to}: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {to}: {e}")
            return False
