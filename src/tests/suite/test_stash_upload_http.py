"""HTTP-level tests for ``POST /suite/stash/upload``.

These exercise the full route end-to-end through the app test client:
base64 decoding (raw and data-URL forms), the media storage boundary, and
the Stash item that references the resulting media object. They also pin the
fail-closed capability gate and the invalid-payload error paths.
"""

import base64
import hashlib
import uuid
from unittest.mock import patch

import pytest

from src.utils import encryption
from src.core.artifacts.capabilities import CapabilityInfo, CapabilityState

UPLOAD_URL = "/api/v1/suite/stash/upload"


def _available_capability(feature: str) -> CapabilityInfo:
    """Capability stub used where licensing is absent in the test process."""
    return CapabilityInfo(feature, CapabilityState.AVAILABLE, "enabled")


def _register_and_login(auth_manager, label="uploader"):
    unique = uuid.uuid4().hex[:8]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username, email=email, password="TestPass123!"
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    return result.token, user


@pytest.fixture
def stash_enabled(monkeypatch):
    """Gate PlexiStash as AVAILABLE (tests run without a license)."""
    import src.api.routes.suite.stash as stash_routes

    monkeypatch.setattr(stash_routes, "get_capability", _available_capability)


@pytest.fixture
def stash_media_dir(tmp_path):
    """Point media storage at a per-test temp dir, before ``test_client`` boots."""
    import utils.config as config

    upload_dir = tmp_path / "uploads"
    config._config_instance.config["media"] = {
        "storage_backend": "local",
        "local_path": str(upload_dir),
        "local_url": "/media",
        "compression": {"enabled": False},
        "signing_key": "test-secret-key-for-signing",
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
    return upload_dir


def _upload_payload(payload: bytes, *, filename="notes.txt", content_type="text/plain"):
    return {
        "title": "Uploaded notes",
        "filename": filename,
        "content": base64.b64encode(payload).decode("ascii"),
        "content_type": content_type,
        "scope": "personal",
    }


class TestStashUploadRoute:
    def test_upload_requires_auth(self, stash_media_dir, test_client, stash_enabled):
        resp = test_client.post(UPLOAD_URL, json=_upload_payload(b"secret"))
        assert resp.status_code == 401

    def test_upload_decodes_base64_and_hits_media_boundary(
        self, stash_media_dir, test_client, stash_enabled, auth_manager, db
    ):
        token, user = _register_and_login(auth_manager)
        payload = b"hello plexistash \xe2\x9c\x93 upload"
        headers = {"Authorization": f"Bearer {token}"}

        resp = test_client.post(
            UPLOAD_URL, json=_upload_payload(payload), headers=headers
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()

        # The Stash item references a real media object and reports the
        # decoded byte count (not the base64 string length).
        assert body["item_type"] == "file"
        assert body["title"] == "Uploaded notes"
        assert body["scope"] == "personal"
        media_object_id = body["media_object_id"]
        assert media_object_id and str(media_object_id).isdigit()
        assert body["metadata"]["size"] == len(payload)
        assert body["metadata"]["content_type"] == "text/plain"

        # Media boundary: a media_files row was created by the uploader with
        # the decoded bytes (size + SHA-256 match the pre-base64 payload).
        media_row = db.fetch_one(
            "SELECT id, size, checksum, uploaded_by, storage_path "
            "FROM media_files WHERE id = ?",
            (int(media_object_id),),
        )
        assert media_row is not None
        assert media_row["size"] == len(payload)
        assert media_row["checksum"] == hashlib.sha256(payload).hexdigest()
        assert int(media_row["uploaded_by"]) == int(user.id)

        # The decoded bytes reached local storage byte-for-byte.
        stored = stash_media_dir / media_row["storage_path"]
        assert stored.read_bytes() == payload

        # Stash item persisted, owned by the uploader and referencing the object.
        item_row = db.fetch_one(
            "SELECT owner_id, media_object_id FROM stash_items "
            "WHERE media_object_id = ?",
            (str(media_object_id),),
        )
        assert item_row is not None
        assert int(item_row["owner_id"]) == int(user.id)
        assert str(item_row["media_object_id"]) == str(media_object_id)

    def test_upload_accepts_data_url_form(
        self, stash_media_dir, test_client, stash_enabled, auth_manager, db
    ):
        token, _ = _register_and_login(auth_manager)
        payload = b"data url payload bytes"
        encoded = base64.b64encode(payload).decode("ascii")

        resp = test_client.post(
            UPLOAD_URL,
            json={
                "title": "Data URL notes",
                "filename": "data.txt",
                "content": f"data:text/plain;base64,{encoded}",
                "content_type": "text/plain",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["metadata"]["size"] == len(payload)
        media_row = db.fetch_one(
            "SELECT checksum FROM media_files WHERE id = ?",
            (int(body["media_object_id"]),),
        )
        assert media_row["checksum"] == hashlib.sha256(payload).hexdigest()

    def test_upload_rejects_invalid_base64(
        self, stash_media_dir, test_client, stash_enabled, auth_manager
    ):
        token, _ = _register_and_login(auth_manager)
        resp = test_client.post(
            UPLOAD_URL,
            json={
                "title": "Broken",
                "filename": "broken.bin",
                "content": "this is !!! not base64 !!!",
                "content_type": "text/plain",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400

    def test_upload_fails_closed_when_capability_unavailable(
        self, stash_media_dir, test_client, auth_manager, monkeypatch
    ):
        import src.api.routes.suite.stash as stash_routes

        token, _ = _register_and_login(auth_manager)
        monkeypatch.setattr(
            stash_routes,
            "get_capability",
            lambda _feature: CapabilityInfo(
                "plexistash", CapabilityState.DISABLED_BY_LICENSE, "no license"
            ),
        )
        resp = test_client.post(
            UPLOAD_URL,
            json=_upload_payload(b"should not upload"),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
