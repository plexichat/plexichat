"""
Unit tests for the TLS module.
"""

import pytest
import os
import shutil
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization


class TestTLSModule:
    """Tests for TLS module functionality."""

    def test_get_default_cert_dir(self):
        """Test default certificate directory path."""
        from src.core.tls import get_default_cert_dir

        cert_dir = get_default_cert_dir()
        assert cert_dir == Path.home() / ".plexichat" / "certs"

    @patch("src.core.tls.config")
    def test_is_tls_enabled_false_by_default(self, mock_config):
        """Test TLS is disabled by default."""
        mock_config.get.return_value = {}

        from src.core.tls import is_tls_enabled

        assert is_tls_enabled() is False

    @patch("src.core.tls.config")
    def test_is_tls_enabled_when_configured(self, mock_config):
        """Test TLS is enabled when configured."""
        mock_config.get.return_value = {"enabled": True}

        from src.core.tls import is_tls_enabled

        assert is_tls_enabled() is True

    @patch("src.core.tls.config")
    def test_is_tls_enabled_with_auto_generate(self, mock_config):
        """Test TLS is enabled with auto_generate_self_signed."""
        mock_config.get.return_value = {"auto_generate_self_signed": True}

        from src.core.tls import is_tls_enabled

        assert is_tls_enabled() is True

    def test_ensure_certificates_generates_files(self):
        """Test certificate generation creates files."""
        from src.core.tls import ensure_certificates

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = os.path.join(tmpdir, "test.crt")
            key_path = os.path.join(tmpdir, "test.key")

            with patch("src.core.tls.config") as mock_config:
                mock_config.get.return_value = {}

                result_cert, result_key = ensure_certificates(
                    cert_path=cert_path,
                    key_path=key_path,
                    validity_days=30,
                    hostname="localhost",
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

    def test_ensure_certificates_reuses_existing(self):
        """Test that existing valid certificates are reused."""
        from src.core.tls import ensure_certificates

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = os.path.join(tmpdir, "test.crt")
            key_path = os.path.join(tmpdir, "test.key")

            with patch("src.core.tls.config") as mock_config:
                mock_config.get.return_value = {}

                # Generate first time
                ensure_certificates(
                    cert_path=cert_path, key_path=key_path, validity_days=30
                )

                # Get modification time
                first_mtime = os.path.getmtime(cert_path)

                # Call again - should reuse
                ensure_certificates(
                    cert_path=cert_path, key_path=key_path, validity_days=30
                )

                # Modification time should be the same
                second_mtime = os.path.getmtime(cert_path)
                assert first_mtime == second_mtime

    def test_get_ssl_context_missing_cert(self):
        """Test get_ssl_context raises error for missing cert."""
        from src.core.tls import get_ssl_context

        with pytest.raises(FileNotFoundError):
            get_ssl_context("/nonexistent/cert.crt", "/nonexistent/key.key")

    def _make_cert(self, cert_path, key_path, hostname="localhost"):
        from src.core.tls import ensure_certificates

        with patch("src.core.tls.config") as mock_config:
            mock_config.get.return_value = {}
            return ensure_certificates(
                cert_path=cert_path,
                key_path=key_path,
                validity_days=30,
                hostname=hostname,
            )

    def test_generated_cert_is_leaf_not_ca(self):
        """F2: generated certificate must not be a CA and must have serverAuth EKU."""
        from cryptography import x509

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = os.path.join(tmpdir, "test.crt")
            key_path = os.path.join(tmpdir, "test.key")
            self._make_cert(cert_path, key_path)

            with open(cert_path, "rb") as f:
                cert = x509.load_pem_x509_certificate(f.read())

            bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
            assert bc.ca is False
            eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            from cryptography.x509.oid import ExtendedKeyUsageOID

            assert ExtendedKeyUsageOID.SERVER_AUTH in eku

    def test_private_key_permissions_restricted(self):
        """F3/F4: private key is created 0600 (POSIX only)."""
        if os.name == "nt":
            pytest.skip("chmod semantics are POSIX-only")

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = os.path.join(tmpdir, "test.crt")
            key_path = os.path.join(tmpdir, "test.key")
            self._make_cert(cert_path, key_path)
            assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600

    def test_tls_enabled_missing_cert_raises(self):
        """F1: configured TLS with missing certs must raise, not fail open."""
        from src.core.tls import TLSConfigError, get_tls_config

        with patch("src.core.tls.config") as mock_config:
            mock_config.get.return_value = {
                "enabled": True,
                "cert_path": "/nonexistent/tls/server.crt",
                "key_path": "/nonexistent/tls/server.key",
            }
            with pytest.raises(TLSConfigError):
                get_tls_config()

    @patch("src.core.tls.config")
    def test_tls_disabled_returns_empty(self, mock_config):
        """TLS disabled still returns empty config (plain HTTP is intentional)."""
        from src.core.tls import get_tls_config

        mock_config.get.side_effect = lambda k, d=None: {} if k == "tls" else None
        with patch("src.core.tls.is_tls_enabled", return_value=False):
            assert get_tls_config() == {}

    def test_regenerated_cert_preserves_hostname_and_sans(self):
        """F5/F6: renewal preserves original CN/SANs instead of localhost."""
        from cryptography import x509

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = os.path.join(tmpdir, "test.crt")
            key_path = os.path.join(tmpdir, "test.key")
            self._make_cert(cert_path, key_path, hostname="chat.example.com")

            with open(cert_path, "rb") as f:
                old_cert = x509.load_pem_x509_certificate(f.read())
            old_san = old_cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.DNSName)
            assert "chat.example.com" in old_san

            # Force expiry by rewriting the cert as expired, then renew.
            expired = self._expired_cert_bytes("chat.example.com", old_san)
            with open(cert_path, "wb") as f:
                f.write(expired)

            from src.core.tls import ensure_certificates

            with patch("src.core.tls.config") as mock_config:
                mock_config.get.return_value = {"hostname": "localhost"}
                ensure_certificates(cert_path=cert_path, key_path=key_path)

            with open(cert_path, "rb") as f:
                new_cert = x509.load_pem_x509_certificate(f.read())
            cn = new_cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[
                0
            ].value
            assert cn == "chat.example.com"
            san_values = [
                str(g.value)
                for g in new_cert.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName
                ).value
            ]
            assert "chat.example.com" in san_values

    def _expired_cert_bytes(self, hostname, sans):
        import ipaddress
        from datetime import datetime, timedelta, timezone

        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )
        subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, hostname)])
        names = [x509.DNSName(s) for s in sans]
        for s in sans:
            try:
                names.append(x509.IPAddress(ipaddress.ip_address(s)))
            except ValueError:
                pass
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=30))
            .not_valid_after(now - timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .sign(key, hashes.SHA256(), default_backend())
        )
        return cert.public_bytes(serialization.Encoding.PEM)

    def test_validity_days_validation(self):
        """F10: absurd cert_days values raise a clear config error."""
        from src.core.tls import TLSConfigError, ensure_certificates

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("src.core.tls.config") as mock_config:
                mock_config.get.return_value = {"cert_days": 0}
                with pytest.raises(TLSConfigError):
                    ensure_certificates(
                        cert_path=os.path.join(tmpdir, "a.crt"),
                        key_path=os.path.join(tmpdir, "a.key"),
                    )
                mock_config.get.return_value = {"cert_days": 99999}
                with pytest.raises(TLSConfigError):
                    ensure_certificates(
                        cert_path=os.path.join(tmpdir, "b.crt"),
                        key_path=os.path.join(tmpdir, "b.key"),
                    )

    def test_hostname_validation(self):
        """F10: invalid hostname values raise a clear config error."""
        from src.core.tls import TLSConfigError, ensure_certificates

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("src.core.tls.config") as mock_config:
                mock_config.get.return_value = {"hostname": ""}
                with pytest.raises(TLSConfigError):
                    ensure_certificates(
                        cert_path=os.path.join(tmpdir, "c.crt"),
                        key_path=os.path.join(tmpdir, "c.key"),
                    )
                mock_config.get.return_value = {"hostname": "bad host\nname"}
                with pytest.raises(TLSConfigError):
                    ensure_certificates(
                        cert_path=os.path.join(tmpdir, "d.crt"),
                        key_path=os.path.join(tmpdir, "d.key"),
                    )

    def test_bare_relative_filename_no_crash(self):
        """F7: bare relative filename without directory component works."""
        # Scratch cwd only; no fixed base dir (e.g. /tmp/opencode) that
        # would have to pre-exist on the runner.
        workdir = tempfile.mkdtemp()
        try:
            old_cwd = os.getcwd()
            os.chdir(workdir)
            try:
                cert_path, key_path = self._make_cert("rel.crt", "rel.key")
                assert os.path.exists(cert_path)
                assert os.path.exists(key_path)
            finally:
                os.chdir(old_cwd)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def test_duplicate_sans_deduped(self):
        """F8: no duplicate SAN entries even when hostname is ::1 or localhost."""
        from cryptography import x509

        for hostname in ("::1", "127.0.0.1", "localhost"):
            with tempfile.TemporaryDirectory() as tmpdir:
                cert_path = os.path.join(tmpdir, "t.crt")
                key_path = os.path.join(tmpdir, "t.key")
                self._make_cert(cert_path, key_path, hostname=hostname)

                with open(cert_path, "rb") as f:
                    cert = x509.load_pem_x509_certificate(f.read())
                sans = [
                    str(g.value)
                    for g in cert.extensions.get_extension_for_class(
                        x509.SubjectAlternativeName
                    ).value
                ]
                assert len(sans) == len(set(sans)), f"duplicate SANs for {hostname}"

    def test_concurrent_regeneration_produces_matching_pair(self):
        """F5: concurrent regeneration leaves a matching cert/key pair."""
        import threading

        from cryptography import x509

        from src.core.tls import ensure_certificates

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = os.path.join(tmpdir, "r.crt")
            key_path = os.path.join(tmpdir, "r.key")
            self._make_cert(cert_path, key_path)

            threads = []
            for _ in range(4):
                t = threading.Thread(
                    target=ensure_certificates,
                    kwargs={
                        "cert_path": cert_path,
                        "key_path": key_path,
                        "force_regenerate": True,
                    },
                )
                threads.append(t)
                t.start()
            for t in threads:
                t.join()

            with open(key_path, "rb") as f:
                key = serialization.load_pem_private_key(f.read(), password=None)
            with open(cert_path, "rb") as f:
                cert = x509.load_pem_x509_certificate(f.read())

            pub_from_key = key.public_key().public_numbers()
            pub_from_cert = cert.public_key().public_numbers()
            assert pub_from_key == pub_from_cert

    def test_version_tolerant_validity_check(self):
        """F9: validity check handles naive/aware expiry datetimes."""
        from datetime import datetime, timedelta, timezone

        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        from src.core.tls import _check_certificate_validity

        key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )
        subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "ok")])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=90))
            .sign(key, hashes.SHA256(), default_backend())
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "v.crt")
            with open(path, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
            assert _check_certificate_validity(path) is True
