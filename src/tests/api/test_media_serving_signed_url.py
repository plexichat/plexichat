"""HTTP-level tests for the signed-URL gate on
``GET /api/v1/media/attachments/{filename}``.

The route opens with signature verification, which is *synchronous*:
``media.verify_signed_url`` returns ``Tuple[bool, int]``. Awaiting that tuple
raises ``TypeError: 'tuple' object can't be awaited``, and the surrounding
``except Exception`` only logs it — so ``is_signed`` silently stayed False and
every signed attachment URL fell through to bearer auth. In production that
surfaced as a steady ``Signed URL verification failed for <file>: 'tuple'
object can't be awaited`` followed by ``401 Invalid token``, i.e. the whole
uid-bound signed-URL feature (the uploader-bound ``uid`` param, the per-file
access re-check on top of it, anonymous access to deliberately public assets)
was dead code.

These tests drive the real route through the app test client so the
coroutine-vs-tuple mistake cannot reappear unnoticed.
"""

import uuid
from unittest.mock import patch

import pytest

from src.utils import encryption

ATTACHMENTS_URL = "/api/v1/media/attachments"
SIGNING_KEY = "test-secret-key-for-signing"

# Smallest credible payload: content does not matter to the auth gate.
FILE_BYTES = b"attachment-bytes-for-signed-url-test"


@pytest.fixture
def attachment_media_dir(tmp_path):
    """Point media storage at a per-test temp dir, before ``test_client`` boots."""
    import utils.config as config

    upload_dir = tmp_path / "uploads"
    config._config_instance.config["media"] = {
        "storage_backend": "local",
        "local_path": str(upload_dir),
        "local_url": "/media",
        "compression": {"enabled": False},
        "signing_key": SIGNING_KEY,
        "signing_expiry": 3600,
        "scanner_enabled": False,
        "proxy_enabled": False,
        "size_limits": {
            "image": 10 * 1024 * 1024,
            "video": 100 * 1024 * 1024,
            "audio": 50 * 1024 * 1024,
            "document": 25 * 1024 * 1024,
            "other": 10 * 1024 * 1024,
        },
        "thumbnail_sizes": [64, 128, 256, 512],
        "rate_limit": {
            "enabled": True,
            "uploads_per_minute": 1000,
            "uploads_per_hour": 10000,
            "max_total_size_per_day": 10 * 1024 * 1024 * 1024,
        },
    }
    # ``config.get`` caches lookups; a direct assignment bypasses that cache, so
    # without this an earlier cached ``config.get("media")`` would leave
    # ``media.setup`` reading the stale default path and writing the upload
    # outside this temp dir.
    config._config_instance._invalidate_cache()
    return upload_dir


def _register_and_login(auth_manager, label="attachment"):
    unique = uuid.uuid4().hex[:8]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username, email=email, password="TestPass123!"
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    return user, result.token


def _upload_attachment(media, db, user_id, filename="signed.txt"):
    """Upload a file and return (storage filename, file_id).

    The route looks the row up by its *storage* filename, which the upload
    path may rewrite, so read it back rather than assuming ``filename``.
    """
    result = media.upload_file(
        user_id=user_id,
        file_data=FILE_BYTES,
        filename=filename,
        content_type="text/plain",
    )
    row = db.fetch_one(
        "SELECT filename, scan_status FROM media_files WHERE id = ?",
        (result.file_id,),
    )
    if row and row["scan_status"] == "pending":
        # The pending -> clean transition belongs to the background scanner
        # (disabled in tests); these tests pin the *authorization* gate, which
        # runs before the scan gate, so settle it explicitly instead of racing
        # the worker.
        db.execute(
            "UPDATE media_files SET scan_status = 'clean' WHERE id = ?",
            (result.file_id,),
        )
    return row["filename"], result.file_id


class TestSignedAttachmentAccess:
    def test_signature_alone_serves_a_public_asset(
        self, attachment_media_dir, test_client, db, media_manager, auth_manager
    ):
        """An unsigned-in-uid asset must be servable by signature alone.

        This is the discriminating regression test: the signature has to be
        the credential that grants access, so nothing else (bearer token,
        cookie) can mask a broken signature gate by authorizing the request
        anyway. With ``await media.verify_signed_url(...)`` the gate raised
        TypeError on the returned tuple, ``is_signed`` stayed False, and this
        request came back 401 Authentication required.

        Intentionally public assets (avatars, server icons, thumbnails) are
        signed without a ``uid`` precisely so they can be embedded in an
        anonymous ``<img>``; the owner-bound variant is covered below.
        """
        _user, _token = _register_and_login(auth_manager)
        filename, file_id = _upload_attachment(media_manager, db, _user.id)

        # Control: with no signature at all the same URL is unauthenticated.
        assert test_client.get(f"{ATTACHMENTS_URL}/{filename}").status_code == 401

        signed = media_manager.sign_url(
            file_id, url=f"{ATTACHMENTS_URL}/{filename}", expires_in=600
        )

        resp = test_client.get(signed.url)

        assert resp.status_code == 200, resp.text
        assert resp.content == FILE_BYTES

    def test_owner_signed_url_is_served(
        self, attachment_media_dir, test_client, db, media_manager, auth_manager
    ):
        """The owner's uid-bound signed URL is served end-to-end."""
        user, token = _register_and_login(auth_manager)
        filename, file_id = _upload_attachment(media_manager, db, user.id)

        signed = media_manager.sign_url(
            file_id,
            user_id=user.id,
            url=f"{ATTACHMENTS_URL}/{filename}",
            expires_in=600,
        )

        resp = test_client.get(signed.url, headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 200, resp.text
        assert resp.content == FILE_BYTES

    def test_uid_bound_signature_is_rejected_anonymously(
        self, attachment_media_dir, test_client, db, media_manager, auth_manager
    ):
        """A signature bound to a user must never work without that user.

        Fail-closed half of the same gate: the fix must not turn signed URLs
        into a bearer-less bypass for private attachments.
        """
        user, _token = _register_and_login(auth_manager)
        filename, file_id = _upload_attachment(media_manager, db, user.id)

        signed = media_manager.sign_url(
            file_id,
            user_id=user.id,
            url=f"{ATTACHMENTS_URL}/{filename}",
            expires_in=600,
        )

        resp = test_client.get(signed.url)

        assert resp.status_code == 401, resp.text

    def test_tampered_signature_grants_no_access_on_its_own(
        self, attachment_media_dir, test_client, db, media_manager, auth_manager
    ):
        """A corrupted ``sig`` must not authenticate the request by itself.

        A bad signature is not a hard 401 for an *authenticated* caller: the
        route treats it as "no valid signature" and falls through to bearer
        auth. What must never happen is the signature alone admitting an
        anonymous caller.
        """
        user, _token = _register_and_login(auth_manager)
        filename, file_id = _upload_attachment(media_manager, db, user.id)

        signed = media_manager.sign_url(
            file_id,
            user_id=user.id,
            url=f"{ATTACHMENTS_URL}/{filename}",
            expires_in=600,
        )
        tampered = signed.url.replace("sig=", "sig=A")
        assert tampered != signed.url  # the tamper really landed

        resp = test_client.get(tampered)

        assert resp.status_code == 401, resp.text

    def test_expired_signature_grants_no_access_on_its_own(
        self, attachment_media_dir, test_client, db, media_manager, auth_manager
    ):
        """An expired signature must not authenticate an anonymous caller."""
        user, _token = _register_and_login(auth_manager)
        filename, file_id = _upload_attachment(media_manager, db, user.id)

        signed = media_manager.sign_url(
            file_id,
            user_id=user.id,
            url=f"{ATTACHMENTS_URL}/{filename}",
            expires_in=-1,
        )

        resp = test_client.get(signed.url)

        assert resp.status_code == 401, resp.text
