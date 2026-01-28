import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

# Setup config
import utils.config as config
config.setup(config_path="non_existent.yaml", default_config={})

from src.utils.email import SMTPEmailSender

class TestEmailSender(unittest.TestCase):
    @patch("smtplib.SMTP")
    def test_send_email_success(self, mock_smtp):
        """Test successful email sending."""
        sender = SMTPEmailSender(
            host="smtp.example.com",
            port=587,
            user="user@example.com",
            password="password",
            from_email="noreply@example.com"
        )
        
        instance = mock_smtp.return_value.__enter__.return_value
        
        result = sender.send(
            to="recipient@example.com",
            subject="Test Subject",
            body="Test Body"
        )
        
        self.assertTrue(result)
        instance.starttls.assert_called_once()
        instance.login.assert_called_once_with("user@example.com", "password")
        instance.send_message.assert_called_once()

    @patch("smtplib.SMTP")
    def test_send_email_failure(self, mock_smtp):
        """Test email sending failure."""
        sender = SMTPEmailSender(
            host="smtp.example.com",
            port=587,
            user="user@example.com",
            password="password",
            from_email="noreply@example.com"
        )
        
        instance = mock_smtp.return_value.__enter__.return_value
        instance.send_message.side_effect = Exception("SMTP error")
        
        result = sender.send(
            to="recipient@example.com",
            subject="Test Subject",
            body="Test Body"
        )
        
        self.assertFalse(result)

if __name__ == "__main__":
    unittest.main()