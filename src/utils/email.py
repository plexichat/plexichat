"""
Email utilities for Plexichat.
"""

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import utils.logger as logger


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
        self.from_email = from_email
        self.use_tls = use_tls

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
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.from_email
            msg["To"] = to

            part = MIMEText(body, "html" if html else "plain")
            msg.attach(part)

            context = ssl.create_default_context()

            # Using synchronous smtplib; in FastAPI this should be run in a threadpool
            with smtplib.SMTP(self.host, self.port) as server:
                if self.use_tls:
                    server.starttls(context=context)

                if self.user and self.password:
                    server.login(self.user, self.password)

                server.send_message(msg)

            logger.info(f"Email sent successfully to {to}: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {to}: {e}")
            return False
