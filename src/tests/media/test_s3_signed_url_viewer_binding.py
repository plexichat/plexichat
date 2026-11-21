"""Regression: a viewer-bound sign_url request must never degrade to an
unbound native S3 presigned URL.

``MediaManager.sign_url(file_id, user_id=viewer)`` is used on the Stash
download-ticket redemption path and when hydrating message attachments. The
contract, relied on across the media routes, is that the returned URL is bound
to the viewer (uid embedded in the HMAC and enforced on verification). On an
S3-backed deployment the signer used to short-circuit to a native presigned
URL that ignored ``user_id`` entirely, handing anyone who obtained the URL the
bytes with no auth and no per-file access re-check.
"""

import pytest

from src.core.media._signing import _SigningMixin
from src.core.media.exceptions import SignatureInvalidError
from src.core.media.models import MediaFile, MediaType, StorageBackend
from src.core.media.security.signing import UrlSigner

_SIGNING_KEY = "regression-test-signing-key-not-a-known-insecure-default"


class _FakeS3Storage:
    """Storage that hands out a real native presigned URL if asked."""

    def is_encrypted(self, path: str) -> bool:
        return False

    def generate_presigned_url(
        self, path: str, expires_in: int = 3600, params: dict | None = None
    ) -> str:
        return (
            f"https://example-bucket.s3.amazonaws.com/{path}"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=deadbeef"
        )


class _Signer(_SigningMixin):
    """Minimal host for the signing mixin with an S3-backed file."""

    def __init__(self) -> None:
        self._file = MediaFile(
            id=555,
            filename="stored-secret.png",
            original_filename="secret.png",
            content_type="image/png",
            size=10,
            media_type=MediaType.IMAGE,
            storage_backend=StorageBackend.S3,
            storage_path="objects/stored-secret.png",
        )
        self._config = {"signing_key": _SIGNING_KEY}
        self._url_signer = UrlSigner(_SIGNING_KEY)

    def get_file(self, file_id):
        return self._file if file_id == self._file.id else None

    def _get_storage_by_backend(self, backend):
        return _FakeS3Storage()


def test_viewer_bound_s3_sign_url_uses_uid_bound_proxy():
    signer = _Signer()
    signed = signer.sign_url(555, user_id=42)

    # The vulnerable path returned a direct S3 presign with no uid at all.
    assert "/api/v1/media/attachments/stored-secret.png" in signed.url
    assert "uid=42" in signed.url
    assert "X-Amz-Signature" not in signed.url

    # The returned URL genuinely enforces the viewer binding: the intended
    # viewer verifies, a different authenticated user does not.
    assert signer._url_signer.verify_url(signed.url, current_user_id=42)[0] is True
    with pytest.raises(SignatureInvalidError):
        signer._url_signer.verify_url(signed.url, current_user_id=99)


def test_public_uid_less_s3_sign_url_keeps_native_presign():
    """Uid-less (intentionally public) calls must not regress."""
    signed = _Signer().sign_url(555)
    assert "X-Amz-Signature" in signed.url
