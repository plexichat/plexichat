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
    # ``config.get`` caches lookups and is only invalidated on reload/set. The
    # direct assignment above bypasses that cache, so without an explicit
    # invalidation an earlier ``config.get("media")`` (cached by another
    # fixture earlier in the session) would make ``media.setup`` read the stale
    # default storage path and write the upload outside this temp dir,
    # producing an order-dependent FileNotFoundError.
    config._config_instance._invalidate_cache()
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

        # The decoded bytes reached storage byte-for-byte. Read them back
        # through the media module so the assertion uses the same storage
        # backend the upload actually wrote to -- this is robust to how the
        # local base path is resolved and does not depend on reconstructing
        # the path from the test's temp directory.
        from src.core import media as media_module

        stored_bytes, stored_ct = media_module.get_file_data(int(media_object_id))
        assert stored_bytes == payload
        assert stored_ct == "text/plain"

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

    def test_upload_server_scoped_requires_membership(
        self, stash_media_dir, test_client, stash_enabled, auth_manager, server_manager
    ):
        # Server owner (and therefore member) and a separate, non-member user.
        token_owner, owner = _register_and_login(auth_manager, "owner")
        token_intruder, _ = _register_and_login(auth_manager, "intruder")
        server = server_manager.create_server(owner.id, "Stash server")

        payload = _upload_payload(b"server scoped bytes")
        payload["scope"] = "server"
        payload["server_id"] = str(server.id)
        owner_headers = {"Authorization": f"Bearer {token_owner}"}
        intruder_headers = {"Authorization": f"Bearer {token_intruder}"}

        # A non-member must be rejected before any media is written.
        resp_intruder = test_client.post(
            UPLOAD_URL, json=payload, headers=intruder_headers
        )
        assert resp_intruder.status_code == 403, resp_intruder.text

        # The server owner (a member) may upload server-scoped items.
        resp_owner = test_client.post(UPLOAD_URL, json=payload, headers=owner_headers)
        assert resp_owner.status_code == 201, resp_owner.text
        assert resp_owner.json()["scope"] == "server"
        assert resp_owner.json()["server_id"] == str(server.id)

    def test_upload_rejects_oversized_decoded_payload(
        self, stash_media_dir, test_client, stash_enabled, auth_manager, monkeypatch
    ):
        import src.api.routes.suite.stash as stash_routes

        monkeypatch.setattr(stash_routes, "MAX_UPLOAD_DECODED_BYTES", 100)
        token, _ = _register_and_login(auth_manager)
        # 200 decoded bytes exceeds the (lowered) 100-byte ceiling.
        payload = _upload_payload(b"x" * 200)
        resp = test_client.post(
            UPLOAD_URL, json=payload, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 413, resp.text

    def test_upload_rejects_oversized_base64_payload(
        self, stash_media_dir, test_client, stash_enabled, auth_manager, monkeypatch
    ):
        import src.api.routes.suite.stash as stash_routes

        monkeypatch.setattr(stash_routes, "MAX_UPLOAD_ENCODED_LEN", 100)
        token, _ = _register_and_login(auth_manager)
        # An over-long base64 string trips the encoded-length ceiling before
        # any decoding happens.
        payload = {
            "title": "Big",
            "filename": "big.bin",
            "content": "A" * 200,
            "content_type": "application/octet-stream",
            "scope": "personal",
        }
        resp = test_client.post(
            UPLOAD_URL, json=payload, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 413, resp.text


class TestShareLinkDownloadRoute:
    """``POST /suite/stash/share-links/download`` wires share links into the
    existing download-ticket redeem flow, so a link granting ``download``
    actually lets a recipient fetch bytes."""

    def _create_link(self, test_client, token, item_id, **kwargs):
        resp = test_client.post(
            f"/api/v1/suite/stash/items/{item_id}/share-links",
            json=kwargs,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    def test_share_link_downloads_end_to_end(
        self, stash_media_dir, test_client, stash_enabled, auth_manager
    ):
        owner_token, owner = _register_and_login(auth_manager, "owner")
        recipient_token, _ = _register_and_login(auth_manager, "recipient")

        # Owner uploads a file and shares it with download permission.
        upload = test_client.post(
            UPLOAD_URL,
            json=_upload_payload(b"share me please"),
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert upload.status_code == 201, upload.text
        item_id = upload.json()["id"]
        link = self._create_link(
            test_client,
            owner_token,
            item_id,
            permissions=["view_metadata", "download"],
        )

        # Recipient exchanges the share token for a download ticket.
        ticket_resp = test_client.post(
            "/api/v1/suite/stash/share-links/download",
            json={"token": link["token"]},
            headers={"Authorization": f"Bearer {recipient_token}"},
        )
        assert ticket_resp.status_code == 201, ticket_resp.text
        ticket = ticket_resp.json()["ticket"]
        assert ticket

        # And redeems it for a signed media URL.
        redeem_resp = test_client.post(
            "/api/v1/suite/stash/download-ticket/redeem",
            json={"ticket": ticket},
            headers={"Authorization": f"Bearer {recipient_token}"},
        )
        assert redeem_resp.status_code == 200, redeem_resp.text
        assert redeem_resp.json()["media_url"]

    def test_share_link_without_download_permission_gets_403(
        self, stash_media_dir, test_client, stash_enabled, auth_manager
    ):
        owner_token, owner = _register_and_login(auth_manager, "owner")
        recipient_token, _ = _register_and_login(auth_manager, "recipient")

        upload = test_client.post(
            UPLOAD_URL,
            json=_upload_payload(b"metadata only"),
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        item_id = upload.json()["id"]
        link = self._create_link(
            test_client,
            owner_token,
            item_id,
            permissions=["view_metadata", "preview"],
        )

        resp = test_client.post(
            "/api/v1/suite/stash/share-links/download",
            json={"token": link["token"]},
            headers={"Authorization": f"Bearer {recipient_token}"},
        )
        assert resp.status_code == 403, resp.text
