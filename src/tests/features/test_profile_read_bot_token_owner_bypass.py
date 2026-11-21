"""A bot credential must not act as its owner on the private-profile READ.

A bot token's ``user_id`` is its *human owner* (the bot id lives in
``account_id``). ``GET /features/users/{user_id}/profile`` projects the profile
using the viewer's friendships / shared-server memberships when the owner set
``profile_visibility`` to ``friends`` or ``servers``. The route hands the raw
``current_user.user_id`` to that decision, so a leaked bot token is treated as
its owner and can read the private fields (location, timezone, pronouns,
social links) of the owner's friends / server-mates. Every other owner-scoped
feature route rejects bot tokens; this public read was explicitly exempted
from the bot-token guard scan, but the projection is *not* public when the
owner chose a restricted visibility.

The fix downgrades bot credentials to an anonymous viewer for the projection
decision (public profiles stay readable). This test proves the leak and that a
real user token keeps its owner identity.
"""

import uuid
from unittest.mock import patch

import pytest

from src.core import settings as settings_module
from src.core.profiles.manager import ProfileManager
from src.utils import encryption

PROFILE_URL = "/api/v1/features/users/{user_id}/profile"


def _register_and_login(auth_manager, label):
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
    return {
        label: _register_and_login(auth_manager, label) for label in ("alice", "bob")
    }


def _set_private_fields(db, owner_id):
    ProfileManager(db).update_profile(
        owner_id,
        location="Secret Location",
        timezone="Secret/Zone",
        pronouns="they/them",
        social_links=[{"platform": "website", "url": "https://example.com/secret"}],
    )


def test_bot_token_cannot_use_owners_friendship_to_read_private_profile(
    test_client, auth_manager, db, rel_manager, trio
):
    alice, alice_tok = trio["alice"]
    bob, _ = trio["bob"]
    _set_private_fields(db, bob.id)
    settings_module.set_setting(bob.id, "profile_visibility", "friends")

    request = rel_manager.send_friend_request(alice.id, bob.id)
    rel_manager.accept_friend_request(bob.id, request.id)

    bot = auth_manager.create_bot(alice.id, "probe_bot", "Probe Bot")

    # A real user token that IS a friend still gets the full profile.
    resp = test_client.get(
        PROFILE_URL.format(user_id=bob.id), headers=_headers(alice_tok)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["profile"]["location"] == "Secret Location"

    # A bot token owned by Alice must NOT inherit Alice's friendship. It is an
    # anonymous viewer, so the friends-only projection strips the private
    # fields even though the leaked token still resolves to Alice's user_id.
    resp = test_client.get(
        PROFILE_URL.format(user_id=bob.id), headers=_headers(bot.token)
    )
    assert resp.status_code == 200, resp.text
    profile = resp.json()["profile"]
    for field in ("location", "timezone", "pronouns", "social_links"):
        assert field not in profile, f"bot token leaked {field}: {profile}"


def test_bot_token_public_profile_still_readable(test_client, auth_manager, db, trio):
    alice, _ = trio["alice"]
    bob, _ = trio["bob"]
    _set_private_fields(db, bob.id)
    settings_module.set_setting(bob.id, "profile_visibility", "everyone")

    # Bot owned by Alice (not Bob); Bob's profile is public, so the anonymous
    # bot viewer must still be allowed to read it.
    bot = auth_manager.create_bot(alice.id, "public_bot", "Public Bot")

    resp = test_client.get(
        PROFILE_URL.format(user_id=bob.id), headers=_headers(bot.token)
    )
    assert resp.status_code == 200, resp.text
    # Public profiles are public for every credential, including bots.
    assert resp.json()["profile"]["location"] == "Secret Location"
