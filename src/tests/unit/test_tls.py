"""
Unit tests for the TLS module.
"""

import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


class TestTLSModule:
    """Tests for TLS module functionality."""

    def test_get_default_cert_dir(self):
        """Test default certificate directory path."""
        from src.core.tls import get_default_cert_dir

        cert_dir = get_default_cert_dir()
        assert cert_dir == Path.home() / ".plexichat" / "certs"

    @patch('src.core.tls.config')
    def test_is_tls_enabled_false_by_default(self, mock_config):
        """Test TLS is disabled by default."""
        mock_config.get.return_value = {}

        from src.core.tls import is_tls_enabled
        assert is_tls_enabled() is False

    @patch('src.core.tls.config')
    def test_is_tls_enabled_when_configured(self, mock_config):
        """Test TLS is enabled when configured."""
        mock_config.get.return_value = {"enabled": True}

        from src.core.tls import is_tls_enabled
        assert is_tls_enabled() is True

    @patch('src.core.tls.config')
    def test_is_tls_enabled_with_auto_generate(self, mock_config):
        """Test TLS is enabled with auto_generate_self_signed."""
        mock_config.get.return_value = {"auto_generate_self_signed": True}

        from src.core.tls import is_tls_enabled
        assert is_tls_enabled() is True

    @pytest.mark.skipif(
        not pytest.importorskip("cryptography", reason="cryptography not installed"),
        reason="cryptography library required"
    )
    def test_ensure_certificates_generates_files(self):
        """Test certificate generation creates files."""
        from src.core.tls import ensure_certificates
        import utils.logger as logger

        # Setup logger for test
        with tempfile.TemporaryDirectory() as tmpdir:
            logger.setup(log_dir=tmpdir, level="ERROR")

            cert_path = os.path.join(tmpdir, "test.crt")
            key_path = os.path.join(tmpdir, "test.key")

            with patch('src.core.tls.config') as mock_config:
                mock_config.get.return_value = {}

                result_cert, result_key = ensure_certificates(
                    cert_path=cert_path,
                    key_path=key_path,
                    validity_days=30,
                    hostname="localhost"
                )

            assert os.path.exists(result_cert)
            assert os.path.exists(result_key)

            # Verify certificate content
            with open(result_cert, "r") as f:
                cert_content = f.read()
                assert "BEGIN CERTIFICATE" in cert_content

            # Verify key content
            with open(result_key, "r") as f:
                key_content = f.read()
                assert "BEGIN RSA PRIVATE KEY" in key_content
            
            # Shutdown logger to release file handles before tmpdir cleanup
            logger.shutdown()

    @pytest.mark.skipif(
        not pytest.importorskip("cryptography", reason="cryptography not installed"),
        reason="cryptography library required"
    )
    def test_ensure_certificates_reuses_existing(self):
        """Test that existing valid certificates are reused."""
        from src.core.tls import ensure_certificates
        import utils.logger as logger

        with tempfile.TemporaryDirectory() as tmpdir:
            # Setup logger for test
            logger.setup(log_dir=tmpdir, level="ERROR")

            cert_path = os.path.join(tmpdir, "test.crt")
            key_path = os.path.join(tmpdir, "test.key")

            with patch('src.core.tls.config') as mock_config:
                mock_config.get.return_value = {}

                # Generate first time
                ensure_certificates(
                    cert_path=cert_path,
                    key_path=key_path,
                    validity_days=30
                )

                # Get modification time
                first_mtime = os.path.getmtime(cert_path)

                # Call again - should reuse
                ensure_certificates(
                    cert_path=cert_path,
                    key_path=key_path,
                    validity_days=30
                )

                # Modification time should be the same
                second_mtime = os.path.getmtime(cert_path)
                assert first_mtime == second_mtime
            
            # Shutdown logger to release file handles before tmpdir cleanup
            logger.shutdown()

    def test_get_ssl_context_missing_cert(self):
        """Test get_ssl_context raises error for missing cert."""
        from src.core.tls import get_ssl_context

        with pytest.raises(FileNotFoundError):
            get_ssl_context("/nonexistent/cert.crt", "/nonexistent/key.key")
