"""Tests for soundboard routes."""

import uuid

import pytest
from src.utils import encryption


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _mp3_bytes() -> bytes:
    """Minimal MP3-looking payload (ID3 header passes magic-byte detection)."""
    return b"ID3\x04\x00" + b"\x00" * 256


@pytest.fixture
def owner_headers(test_user_with_token):
    return _headers(test_user_with_token["token"])


class TestSoundboardRoutes:
    def test_list_sounds_empty(self, test_client, test_server, owner_headers):
        server, _user = test_server
        response = test_client.get(
            f"/api/v1/servers/{server.id}/soundboard", headers=owner_headers
        )
        assert response.status_code == 200
        assert response.json() == []

    def test_list_sounds_requires_auth(self, test_client, test_server):
        server, _user = test_server
        response = test_client.get(f"/api/v1/servers/{server.id}/soundboard")
        assert response.status_code == 401

    def test_list_sounds_invalid_server_id(self, test_client, owner_headers):
        response = test_client.get(
            "/api/v1/servers/notanumber/soundboard", headers=owner_headers
        )
        assert response.status_code == 400

    def test_upload_sound_success(self, test_client, test_server, owner_headers):
        server, _user = test_server
        unique = uuid.uuid4().hex[:6]
        response = test_client.post(
            f"/api/v1/servers/{server.id}/soundboard",
            headers=owner_headers,
            data={"name": f"airhorn_{unique}", "volume": "0.8"},
            files={"file": ("airhorn.mp3", _mp3_bytes(), "audio/mpeg")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["name"] == f"airhorn_{unique}"
        assert body["format"] == "mp3"
        assert body["size"] == len(_mp3_bytes())
        # URL is produced by the media pipeline, not caller-supplied.
        assert body["url"]

    def test_upload_sound_rejects_non_audio_bytes(
        self, test_client, test_server, owner_headers
    ):
        server, _user = test_server
        unique = uuid.uuid4().hex[:6]
        response = test_client.post(
            f"/api/v1/servers/{server.id}/soundboard",
            headers=owner_headers,
            data={"name": f"evil_{unique}"},
            files={"file": ("payload.mp3", b"MZ\x90\x00" + b"A" * 128, "audio/mpeg")},
        )
        assert response.status_code == 400

    def test_upload_sound_rejects_oversize(
        self, test_client, test_server, owner_headers, monkeypatch
    ):
        from types import SimpleNamespace

        import src.api.routes.soundboard as sb_routes

        monkeypatch.setattr(
            sb_routes, "config", SimpleNamespace(get=lambda k, d=None: 64)
        )
        server, _user = test_server
        unique = uuid.uuid4().hex[:6]
        response = test_client.post(
            f"/api/v1/servers/{server.id}/soundboard",
            headers=owner_headers,
            data={"name": f"big_{unique}"},
            files={"file": ("big.mp3", _mp3_bytes(), "audio/mpeg")},
        )
        assert response.status_code == 413

    def test_get_and_delete_sound(self, test_client, test_server, owner_headers):
        server, _user = test_server
        unique = uuid.uuid4().hex[:6]
        created = test_client.post(
            f"/api/v1/servers/{server.id}/soundboard",
            headers=owner_headers,
            data={"name": f"getdel_{unique}"},
            files={"file": ("g.mp3", _mp3_bytes(), "audio/mpeg")},
        ).json()

        got = test_client.get(
            f"/api/v1/servers/{server.id}/soundboard/{created['id']}",
            headers=owner_headers,
        )
        assert got.status_code == 200
        assert got.json()["id"] == created["id"]

        deleted = test_client.delete(
            f"/api/v1/servers/{server.id}/soundboard/{created['id']}",
            headers=owner_headers,
        )
        assert deleted.status_code == 200
        assert deleted.json()["success"] is True

        gone = test_client.get(
            f"/api/v1/servers/{server.id}/soundboard/{created['id']}",
            headers=owner_headers,
        )
        assert gone.status_code == 404

    def test_update_sound_metadata(self, test_client, test_server, owner_headers):
        server, _user = test_server
        unique = uuid.uuid4().hex[:6]
        created = test_client.post(
            f"/api/v1/servers/{server.id}/soundboard",
            headers=owner_headers,
            data={"name": f"upd_{unique}"},
            files={"file": ("u.mp3", _mp3_bytes(), "audio/mpeg")},
        ).json()

        updated = test_client.patch(
            f"/api/v1/servers/{server.id}/soundboard/{created['id']}",
            headers=owner_headers,
            json={"volume": 0.5},
        )
        assert updated.status_code == 200
        assert updated.json()["volume"] == 0.5

    def test_member_without_manage_scope_cannot_delete(
        self,
        test_client,
        test_server,
        owner_headers,
        auth_manager,
        server_manager,
        pii_gen,
        monkeypatch,
    ):
        server, _user = test_server
        username = f"sb_member_{uuid.uuid4().hex[:6]}"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(encryption, "hash_password", lambda *a, **k: "fake_hash_$test")
            member = auth_manager.register(
                username=username,
                email=f"{username}@example.com",
                password="TestPass123!",
            )
        from src.core.database.cache import invalidate_pattern

        invalidate_pattern("*")
        server_manager.add_member(server.id, member.id)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(encryption, "verify_password", lambda *a, **k: True)
            result = auth_manager.login(username, "TestPass123!")
        member_headers = _headers(result.token)

        # Member can list...
        listing = test_client.get(
            f"/api/v1/servers/{server.id}/soundboard", headers=member_headers
        )
        assert listing.status_code == 200

        # ...but cannot delete.
        target = test_client.post(
            f"/api/v1/servers/{server.id}/soundboard",
            headers=owner_headers,
            data={"name": f"protected_{uuid.uuid4().hex[:6]}"},
            files={"file": ("p.mp3", _mp3_bytes(), "audio/mpeg")},
        ).json()
        denied = test_client.delete(
            f"/api/v1/servers/{server.id}/soundboard/{target['id']}",
            headers=member_headers,
        )
        assert denied.status_code == 403

    def test_play_sound_tracking(
        self, test_client, test_server, owner_headers, server_manager
    ):
        server, owner = test_server
        channel = server_manager.create_channel(
            user_id=owner.id,
            server_id=server.id,
            name=f"voice-sb-{uuid.uuid4().hex[:6]}",
            channel_type=server_manager.ChannelType.VOICE,
        )
        created = test_client.post(
            f"/api/v1/servers/{server.id}/soundboard",
            headers=owner_headers,
            data={"name": f"play_{uuid.uuid4().hex[:6]}"},
            files={"file": ("pl.mp3", _mp3_bytes(), "audio/mpeg")},
        ).json()

        played = test_client.post(
            f"/api/v1/servers/{server.id}/soundboard/{created['id']}/play",
            headers=owner_headers,
            json={"channel_id": str(channel.id)},
        )
        assert played.status_code == 200, played.text
        body = played.json()
        assert body["channel_id"] == str(channel.id)
        assert body["sound"]["id"] == created["id"]

        # Second immediate play hits the default cooldown -> 429.
        again = test_client.post(
            f"/api/v1/servers/{server.id}/soundboard/{created['id']}/play",
            headers=owner_headers,
            json={"channel_id": str(channel.id)},
        )
        assert again.status_code == 429

    def test_set_role_permission(
        self, test_client, test_server, owner_headers, server_manager
    ):
        server, owner = test_server
        role = server_manager.create_role(
            user_id=owner.id, server_id=server.id, name="SB Role"
        )
        created = test_client.post(
            f"/api/v1/servers/{server.id}/soundboard",
            headers=owner_headers,
            data={"name": f"perm_{uuid.uuid4().hex[:6]}"},
            files={"file": ("pe.mp3", _mp3_bytes(), "audio/mpeg")},
        ).json()

        res = test_client.put(
            f"/api/v1/servers/{server.id}/soundboard/{created['id']}"
            f"/permissions/{role.id}",
            headers=owner_headers,
            json={"can_use": False},
        )
        assert res.status_code == 200
        body = res.json()
        assert str(body["role_id"]) == str(role.id)
        assert body["can_use"] is False

    def test_sound_scoped_to_url_server(
        self, test_client, test_server, owner_headers, server_manager
    ):
        """A sound from server A is not visible under server B's URL (404)."""
        server, owner = test_server
        other = server_manager.create_server(
            owner.id, f"Other Server {uuid.uuid4().hex[:6]}"
        )
        created = test_client.post(
            f"/api/v1/servers/{server.id}/soundboard",
            headers=owner_headers,
            data={"name": f"scoped_{uuid.uuid4().hex[:6]}"},
            files={"file": ("s.mp3", _mp3_bytes(), "audio/mpeg")},
        ).json()

        mismatched = test_client.get(
            f"/api/v1/servers/{other.id}/soundboard/{created['id']}",
            headers=owner_headers,
        )
        assert mismatched.status_code == 404

        mismatched_patch = test_client.patch(
            f"/api/v1/servers/{other.id}/soundboard/{created['id']}",
            headers=owner_headers,
            json={"volume": 0.5},
        )
        assert mismatched_patch.status_code == 404

        mismatched_delete = test_client.delete(
            f"/api/v1/servers/{other.id}/soundboard/{created['id']}",
            headers=owner_headers,
        )
        assert mismatched_delete.status_code == 404

        # The sound is still reachable under its own server.
        got = test_client.get(
            f"/api/v1/servers/{server.id}/soundboard/{created['id']}",
            headers=owner_headers,
        )
        assert got.status_code == 200

    def test_non_positive_ids_rejected(self, test_client, test_server, owner_headers):
        server, _user = test_server
        assert (
            test_client.get("/api/v1/servers/0/soundboard", headers=owner_headers)
        ).status_code == 400
        assert (
            test_client.get(
                f"/api/v1/servers/{server.id}/soundboard/0",
                headers=owner_headers,
            )
        ).status_code == 400
        assert (
            test_client.get("/api/v1/servers/-5/soundboard", headers=owner_headers)
        ).status_code == 400
