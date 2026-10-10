# pyright: reportAttributeAccessIssue=false
"""
URL-signing methods mixed into MediaManager.
"""

import os
import base64
import time
import logging
from typing import Optional, Tuple

from .models import StorageBackend, SignedUrl
from .exceptions import MediaError, SignatureInvalidError, SignatureExpiredError
from .security import UrlSigner

logger = logging.getLogger(__name__)

# Keys that must never be used to sign URLs - they are publicly known.
_INSECURE_SIGNING_KEYS = {
    "",
    "change-this-secret-key",
    "CHANGE_THIS_SIGNING_KEY",
    "change-me",
    "changeme",
}


class _SigningMixin:
    """URL-signing methods mixed into MediaManager."""

    def _init_url_signer(self) -> UrlSigner:
        secret_key = self._config.get("signing_key")
        if not secret_key or secret_key in _INSECURE_SIGNING_KEYS:
            # No secure signing key configured: generate an ephemeral per-process
            # key rather than falling back to a publicly-known default that anyone
            # could use to forge signed URLs / proxy tokens.
            secret_key = base64.b64encode(os.urandom(32)).decode()
            self._config["signing_key"] = secret_key
            logger.warning(
                "MEDIA_SIGNING_KEY is not configured or uses an insecure default; "
                "generated an ephemeral signing key for this process. Set a stable, "
                "secret signing_key in config to keep signed URLs valid across restarts."
            )
        return UrlSigner(
            secret_key=secret_key,
            default_expiry=self._config.get("signing_expiry", 3600),
        )

    def sign_url(
        self,
        file_id: int,
        expires_in: Optional[int] = None,
        params: Optional[dict] = None,
        user_id: Optional[int] = None,
        url: Optional[str] = None,
    ) -> SignedUrl:
        """
        Sign a URL for ``file_id``.

        Args:
            file_id: Media file ID
            expires_in: Signature lifetime in seconds
            params: Optional storage-specific parameters
            user_id: User authorized to use this URL. When provided the
                uid is embedded in the signature and enforced during
                verification so a leaked URL is useless to other users.
                Leave ``None`` only for intentionally public assets.
            url: Explicit URL to sign (e.g. the attachment proxy path).
                When omitted the URL is derived from the storage backend.
        """
        if url:
            return self._url_signer.sign_url(
                url, file_id, expires_in=expires_in, user_id=user_id
            )

        file = self.get_file(file_id)
        if not file:
            raise MediaError("File not found")
        storage = self._get_storage_by_backend(file.storage_backend.value)
        is_encrypted = storage.is_encrypted(file.storage_path)

        if is_encrypted:
            proxy_url = f"/api/v1/media/attachments/{file.filename}"
            return self._url_signer.sign_url(
                proxy_url, file_id, expires_in=expires_in, user_id=user_id
            )

        if file.storage_backend == StorageBackend.S3:
            if hasattr(storage, "generate_presigned_url"):
                url = storage.generate_presigned_url(
                    file.storage_path, expires_in or 3600, params=params
                )
                if not url:
                    raise RuntimeError(
                        f"S3 presign returned no URL for {file.filename}"
                    )
                return SignedUrl(
                    url=url,
                    expires_at=int(time.time() * 1000) + ((expires_in or 3600) * 1000),
                    signature="native",
                    file_id=file_id,
                )
            logger.warning(
                f"S3 native signing unavailable for {file.filename}, "
                f"falling back to proxy"
            )
            proxy_url = f"/api/v1/media/attachments/{file.filename}"
            return self._url_signer.sign_url(
                proxy_url, file_id, expires_in=expires_in, user_id=user_id
            )

        url = storage.get_url(file.storage_path)
        return self._url_signer.sign_url(
            url, file_id, expires_in=expires_in, user_id=user_id
        )

    def verify_signed_url(
        self, url: str, current_user_id: Optional[int] = None
    ) -> Tuple[bool, int]:
        try:
            return self._url_signer.verify_url(url, current_user_id=current_user_id)
        except (SignatureInvalidError, SignatureExpiredError):
            return False, 0
