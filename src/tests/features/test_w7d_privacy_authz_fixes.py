"""W7-D regression tests.

Pins the privacy/authorization fixes from the Agent 61/62 wave:

- H1: profile_visibility enforcement on GET /users/{user_id}/profile
- H2: bookmark membership + message/conversation consistency checks
- H3: admin-only fields projected out of reporter-visible report details
- M3: client-supplied voice-message JSON metadata validated server-side
- M4: slowmode reads gated behind channel/thread access
- M5: self-DM rejection on POST /users/@me/channels
"""

import uuid
from unittest.mock import patch

import pytest

from src.utils import encryption
from src.core import settings as settings_module

USERS_DM_URL = "/api/v1/users/@me/channels"
PROFILE_URL = "/api/v1/features/users/{user_id}/profile"
BOOKMARK_URL = "/api/v1/features/bookmarks"
REPORTS_URL = "/api/v1/features/reports"
VOICE_URL = "/api/v1/features/voice-messages"
CHANNEL_SLOWMODE_URL = "/api/v1/features/channels/{channel_id}/slowmode"


def _register_and_login(auth_manager, label="w7d_user"):
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


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def trio(auth_manager):
    """Three unrelated logged-in users."""
    users = {}
    for label in ("alice", "bob", "mallory"):
        user, token = _register_and_login(auth_manager, label)
        users[label] = (user, token)
    return users


# ---------------------------------------------------------------------------
# H1: profile visibility
# ---------------------------------------------------------------------------


class TestProfileVisibility:
    def _get_profile(self, test_client, viewer_token, owner):
        resp = test_client.get(
            PROFILE_URL.format(user_id=owner.id), headers=_headers(viewer_token)
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["profile"]

    def test_public_default_exposes_full_profile(self, test_client, trio):
        alice, alice_tok = trio["alice"]
        bob, _ = trio["bob"]
        profile = self._get_profile(test_client, alice_tok, bob)
        assert profile["user_id"] == bob.id
        assert "location" in profile
        assert "timezone" in profile

    def test_friends_only_limits_non_friend_view(self, test_client, trio):
        alice, alice_tok = trio["alice"]
        bob, _ = trio["bob"]
        settings_module.set_setting(bob.id, "profile_visibility", "friends")

        profile = self._get_profile(test_client, alice_tok, bob)
        assert profile["user_id"] == bob.id
        # Quasi-identifying fields must be stripped for non-friends.
        assert "location" not in profile
        assert "timezone" not in profile
        assert "pronouns" not in profile
        assert "social_links" not in profile
        # Public-safe fields remain.
        assert "bio" in profile

    def test_friend_sees_full_profile_under_friends_only(
        self, test_client, rel_manager, trio
    ):
        alice, alice_tok = trio["alice"]
        bob, _ = trio["bob"]
        settings_module.set_setting(bob.id, "profile_visibility", "friends")
        request = rel_manager.send_friend_request(alice.id, bob.id)
        rel_manager.accept_friend_request(bob.id, request.id)

        profile = self._get_profile(test_client, alice_tok, bob)
        assert "location" in profile
        assert "timezone" in profile

    def test_private_returns_minimal_identity(self, test_client, trio):
        alice, alice_tok = trio["alice"]
        bob, _ = trio["bob"]
        settings_module.set_setting(bob.id, "profile_visibility", "private")

        profile = self._get_profile(test_client, alice_tok, bob)
        assert profile["user_id"] == bob.id
        assert "bio" not in profile
        assert "location" not in profile
        assert len(profile) <= 2

    def test_self_read_is_always_full(self, test_client, trio):
        bob, bob_tok = trio["bob"]
        settings_module.set_setting(bob.id, "profile_visibility", "private")
        profile = self._get_profile(test_client, bob_tok, bob)
        assert "bio" in profile

    def test_unknown_visibility_fails_closed_to_limited(self, test_client, trio):
        alice, alice_tok = trio["alice"]
        bob, _ = trio["bob"]
        settings_module.set_setting(bob.id, "profile_visibility", "nobody")
        profile = self._get_profile(test_client, alice_tok, bob)
        # Unrecognized stored values fail CLOSED to the limited projection:
        # quasi-identifying fields are stripped, public-safe fields remain.
        assert "location" not in profile
        assert "timezone" not in profile
        assert "pronouns" not in profile
        assert "social_links" not in profile
        assert "bio" in profile

    def test_everyone_maps_to_public_full_profile(self, test_client, trio):
        alice, alice_tok = trio["alice"]
        bob, _ = trio["bob"]
        settings_module.set_setting(bob.id, "profile_visibility", "everyone")
        profile = self._get_profile(test_client, alice_tok, bob)
        assert profile["user_id"] == bob.id
        assert "location" in profile
        assert "timezone" in profile

    def test_none_maps_to_private_minimal_identity(self, test_client, trio):
        alice, alice_tok = trio["alice"]
        bob, _ = trio["bob"]
        settings_module.set_setting(bob.id, "profile_visibility", "none")
        profile = self._get_profile(test_client, alice_tok, bob)
        assert profile["user_id"] == bob.id
        assert "bio" not in profile
        assert len(profile) <= 2

    def test_servers_denies_non_shared_server_viewer(
        self, test_client, server_manager, auth_manager, trio
    ):
        alice, alice_tok = trio["alice"]
        bob, _ = trio["bob"]
        # Alice and Bob share NO server.
        owner_c, _ = _register_and_login(auth_manager, "srvc")
        server_manager.create_server(
            owner_id=owner_c.id, name=f"SrvC {uuid.uuid4().hex[:6]}"
        )

        settings_module.set_setting(bob.id, "profile_visibility", "servers")
        profile = self._get_profile(test_client, alice_tok, bob)
        assert "location" not in profile
        assert "timezone" not in profile
        assert "bio" in profile

    def test_servers_allows_shared_server_member(
        self, test_client, server_manager, auth_manager, trio
    ):
        alice, alice_tok = trio["alice"]
        bob, _ = trio["bob"]
        srv = server_manager.create_server(
            owner_id=bob.id, name=f"SrvShared {uuid.uuid4().hex[:6]}"
        )
        server_manager.add_member(srv.id, alice.id, inviter_id=bob.id)

        settings_module.set_setting(bob.id, "profile_visibility", "servers")
        profile = self._get_profile(test_client, alice_tok, bob)
        assert "location" in profile


# ---------------------------------------------------------------------------
# H2: bookmark membership enforcement
# ---------------------------------------------------------------------------


class TestBookmarkMembership:
    def _setup_dm_with_message(self, messaging_manager, a, b, content="hello"):
        dm = messaging_manager.create_dm(a.id, b.id)
        msg = messaging_manager.send_message(a.id, dm.id, content)
        return dm, msg

    def test_participant_can_bookmark(
        self, test_client, auth_manager, messaging_manager, trio
    ):
        alice, alice_tok = trio["alice"]
        bob, bob_tok = trio["bob"]
        dm, msg = self._setup_dm_with_message(messaging_manager, alice, bob)

        resp = test_client.post(
            BOOKMARK_URL,
            json={
                "message_id": str(msg.id),
                "conversation_id": str(dm.id),
            },
            headers=_headers(bob_tok),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True

    def test_non_participant_is_forbidden(
        self, test_client, auth_manager, messaging_manager, trio
    ):
        alice, _ = trio["alice"]
        bob, bob_tok = trio["bob"]
        mallory, mallory_tok = trio["mallory"]
        dm, msg = self._setup_dm_with_message(messaging_manager, alice, bob)

        resp = test_client.post(
            BOOKMARK_URL,
            json={
                "message_id": str(msg.id),
                "conversation_id": str(dm.id),
                "label": "probe",
            },
            headers=_headers(mallory_tok),
        )
        # M8 oracle contract: no-access yields the identical 404 as a
        # missing message (mirrors message-GET), never 403.
        assert resp.status_code == 404, resp.text
        body = resp.json()
        assert body["error"]["message"] == "Message not found"

    def test_message_conversation_mismatch_is_rejected(
        self, test_client, auth_manager, messaging_manager, trio
    ):
        alice, _ = trio["alice"]
        bob, bob_tok = trio["bob"]
        mallory, mallory_tok = trio["mallory"]

        dm1, msg = self._setup_dm_with_message(messaging_manager, alice, bob)
        other = messaging_manager.create_dm(alice.id, mallory.id)

        resp = test_client.post(
            BOOKMARK_URL,
            json={
                "message_id": str(msg.id),
                "conversation_id": str(other.id),
            },
            headers=_headers(bob_tok),
        )
        # M8: conversation mismatch is indistinguishable from missing.
        assert resp.status_code == 404, resp.text

    def test_missing_message_is_bad_request(
        self, test_client, auth_manager, messaging_manager, trio
    ):
        alice, _ = trio["alice"]
        bob, bob_tok = trio["bob"]
        dm, _msg = self._setup_dm_with_message(messaging_manager, alice, bob)

        resp = test_client.post(
            BOOKMARK_URL,
            json={"message_id": "999999999", "conversation_id": str(dm.id)},
            headers=_headers(bob_tok),
        )
        # M8: missing message yields the same 404 as no-access (no oracle).
        assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# H3: report detail field projection
# ---------------------------------------------------------------------------


class TestReportFieldProjection:
    def test_reporter_cannot_see_admin_fields(self, test_client, db, trio):
        import time as _time

        mallory, mallory_tok = trio["mallory"]
        bob, bob_tok = trio["bob"]

        resp = test_client.post(
            f"{REPORTS_URL}/enhanced",
            json={
                "target_type": "user",
                "target_id": str(bob.id),
                "reason": "spam",
                "category": "other",
            },
            headers=_headers(mallory_tok),
        )
        assert resp.status_code == 200, resp.text
        rid = int(resp.json()["report_id"])

        now = int(_time.time() * 1000)
        db.execute(
            """UPDATE reports SET admin_notes = 'TOPSECRET', assigned_to = 424242,
               resolution = 'resolved quietly', reviewed_by = 424242,
               escalated_at = ?
               WHERE id = ?""",
            (now, rid),
        )

        got = test_client.get(f"{REPORTS_URL}/{rid}", headers=_headers(mallory_tok))
        assert got.status_code == 200, got.text
        report = got.json()["report"]
        for field in (
            "admin_notes",
            "assigned_to",
            "resolution",
            "reviewed_by",
            "escalated_at",
            "action_taken",
        ):
            assert field not in report, field
        # Reporter-safe fields survive.
        assert report.get("reason") == "spam"


# ---------------------------------------------------------------------------
# M3: voice-message JSON metadata validation (+ M6 403 mapping)
# ---------------------------------------------------------------------------


def _valid_voice_payload(conversation_id):
    return {
        "conversation_id": conversation_id,
        "duration_ms": 5000,
        "filename": "voice.webm",
        "content_type": "audio/webm",
        "size": 1024,
        "url": "/api/v1/media/attachments/voice_1.webm",
    }


class TestVoiceMessageValidation:
    @pytest.mark.parametrize(
        "override",
        [
            {"url": "javascript:alert(1)"},
            {"url": "data:audio/webm;base64,AAAA"},
            {"content_type": "text/html"},
            {"filename": "voice.exe"},
            {"size": 11 * 1024 * 1024},
            {"duration_ms": 700000},
        ],
    )
    def test_invalid_metadata_rejected(self, test_client, trio, override):
        _, tok = trio["alice"]
        payload = _valid_voice_payload("123")
        payload.update(override)
        resp = test_client.post(VOICE_URL, json=payload, headers=_headers(tok))
        assert resp.status_code == 400, resp.text

    def test_non_participant_maps_to_403(self, test_client, messaging_manager, trio):
        alice, _ = trio["alice"]
        bob, _ = trio["bob"]
        mallory, mallory_tok = trio["mallory"]
        dm = messaging_manager.create_dm(alice.id, bob.id)

        payload = _valid_voice_payload(str(dm.id))
        resp = test_client.post(VOICE_URL, json=payload, headers=_headers(mallory_tok))
        assert resp.status_code == 403, resp.text

    def test_malformed_conversation_id_is_400(self, test_client, trio):
        _, tok = trio["alice"]
        payload = _valid_voice_payload("not-a-number")
        resp = test_client.post(VOICE_URL, json=payload, headers=_headers(tok))
        assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# M4: slowmode read gating
# ---------------------------------------------------------------------------


class TestSlowmodeReadGating:
    def test_cross_server_slowmode_read_forbidden(
        self, test_client, server_manager, auth_manager
    ):
        owner_a, tok_a = _register_and_login(auth_manager, "srva")
        owner_b, tok_b = _register_and_login(auth_manager, "srvb")
        server_a = server_manager.create_server(
            owner_id=owner_a.id, name=f"SrvA {uuid.uuid4().hex[:6]}"
        )
        channel_a = server_manager.get_channels(owner_a.id, server_a.id)[0]

        url = CHANNEL_SLOWMODE_URL.format(channel_id=channel_a.id)
        resp = test_client.get(url, headers=_headers(tok_b))
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["message"] == "Channel access denied"

        # Owner still reads fine.
        resp = test_client.get(url, headers=_headers(tok_a))
        assert resp.status_code == 200, resp.text

    def test_unknown_channel_slowmode_is_404(self, test_client, auth_manager):
        _, tok = _register_and_login(auth_manager, "ghost")
        url = CHANNEL_SLOWMODE_URL.format(channel_id=9999999999)
        resp = test_client.get(url, headers=_headers(tok))
        assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# M5: self-DM rejection
# ---------------------------------------------------------------------------


class TestSelfDMRejected:
    def test_self_dm_is_rejected_cleanly(self, test_client, trio):
        bob, bob_tok = trio["bob"]
        resp = test_client.post(
            USERS_DM_URL,
            json={"recipient_id": str(bob.id)},
            headers=_headers(bob_tok),
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["message"] == "Cannot create a DM with yourself"
