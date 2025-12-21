"""
URL signing with HMAC and expiration.
"""

import hmac
import hashlib
import time
import base64
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
from typing import Optional, Tuple


from ..models import SignedUrl
from ..exceptions import SigningError, SignatureExpiredError, SignatureInvalidError


class UrlSigner:
    """URL signing with HMAC-SHA256 and expiration."""

    def __init__(self, secret_key: str, default_expiry: int = 3600):
        """
        Initialize URL signer.
        
        Args:
            secret_key: Secret key for HMAC signing
            default_expiry: Default expiration time in seconds
        """
        if not secret_key:
            raise SigningError("Secret key is required")

        self._secret_key = secret_key.encode("utf-8")
        self._default_expiry = default_expiry

    def sign_url(
        self,
        url: str,
        file_id: int,
        expires_in: Optional[int] = None,
        extra_data: Optional[dict] = None,
    ) -> SignedUrl:
        """
        Sign a URL with HMAC and expiration.
        
        Args:
            url: URL to sign
            file_id: File ID for verification
            expires_in: Expiration time in seconds (None for default)
            extra_data: Additional data to include in signature
            
        Returns:
            SignedUrl object
        """
        expiry_seconds = expires_in or self._default_expiry
        now_ms = int(time.time() * 1000)
        expires_at = now_ms + (expiry_seconds * 1000)

        signature_data = self._build_signature_data(url, file_id, expires_at, extra_data)
        signature = self._generate_signature(signature_data)

        signed_url = self._append_signature_params(url, file_id, expires_at, signature)

        return SignedUrl(
            url=signed_url,
            expires_at=expires_at,
            signature=signature,
            file_id=file_id,
        )

    def verify_url(self, url: str) -> Tuple[bool, int]:
        """
        Verify a signed URL.
        
        Args:
            url: Signed URL to verify
            
        Returns:
            Tuple of (is_valid, file_id)
            
        Raises:
            SignatureExpiredError: If URL has expired
            SignatureInvalidError: If signature is invalid
        """
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        signature = params.get("sig", [None])[0]
        expires_str = params.get("exp", [None])[0]
        file_id_str = params.get("fid", [None])[0]

        if not all([signature, expires_str, file_id_str]):
            raise SignatureInvalidError("Missing signature parameters")

        assert expires_str is not None and file_id_str is not None  # Checked above

        try:
            expires_at = int(expires_str)
            file_id = int(file_id_str)
        except ValueError:
            raise SignatureInvalidError("Invalid signature parameters")

        if int(time.time() * 1000) > expires_at:
            raise SignatureExpiredError("Signed URL has expired")

        base_url = self._remove_signature_params(url)
        expected_data = self._build_signature_data(base_url, file_id, expires_at, None)
        expected_signature = self._generate_signature(expected_data)

        assert signature is not None  # Checked above
        if not hmac.compare_digest(signature, expected_signature):
            raise SignatureInvalidError("Invalid signature")

        return True, file_id

    def _build_signature_data(
        self,
        url: str,
        file_id: int,
        expires_at: int,
        extra_data: Optional[dict],
    ) -> str:
        """Build the data string to sign."""
        parts = [url, str(file_id), str(expires_at)]

        if extra_data:
            for key in sorted(extra_data.keys()):
                parts.append(f"{key}={extra_data[key]}")

        return "|".join(parts)

    def _generate_signature(self, data: str) -> str:
        """Generate HMAC-SHA256 signature."""
        signature = hmac.new(
            self._secret_key,
            data.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        return base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")

    def _append_signature_params(
        self,
        url: str,
        file_id: int,
        expires_at: int,
        signature: str,
    ) -> str:
        """Append signature parameters to URL."""
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        params["fid"] = [str(file_id)]
        params["exp"] = [str(expires_at)]
        params["sig"] = [signature]

        flat_params = []
        for key, values in params.items():
            for value in values:
                flat_params.append((key, value))

        new_query = urlencode(flat_params)

        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        ))

    def _remove_signature_params(self, url: str) -> str:
        """Remove signature parameters from URL."""
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        for key in ["sig", "exp", "fid"]:
            params.pop(key, None)

        flat_params = []
        for key, values in params.items():
            for value in values:
                flat_params.append((key, value))

        new_query = urlencode(flat_params)

        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        ))

    def get_expiry_time(self, url: str) -> Optional[int]:
        """
        Get expiration timestamp from signed URL.
        
        Args:
            url: Signed URL
            
        Returns:
            Expiration timestamp in seconds, or None if not found
        """
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        expires_str = params.get("exp", [None])[0]
        if expires_str:
            try:
                return int(expires_str)
            except ValueError:
                pass

        return None

    def is_expired(self, url: str) -> bool:
        """
        Check if signed URL has expired.
        
        Args:
            url: Signed URL
            
        Returns:
            True if expired
        """
        expiry_ms = self.get_expiry_time(url)
        if expiry_ms is None:
            return True
        return int(time.time() * 1000) > expiry_ms
