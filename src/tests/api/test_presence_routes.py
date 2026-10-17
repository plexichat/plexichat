"""Tests for presence routes.

Covers the real endpoints:
- PUT /api/v1/users/@me/presence (update own status/custom status)
- GET /api/v1/users/{user_id}/presence (gated read)
"""

import uuid
from unittest.mock import patch

from src.utils import encryption


def _register_user(auth_manager):
    unique = uuid.uuid4().hex[:8]
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=f"presroute_{unique}",
            email=f"presroute_{unique}@test.local",
            password="TestPass123!",
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(f"presroute_{unique}", "TestPass123!")
    return user, {"Authorization": f"Bearer {result.token}"}


def test_set_presence_updates_status(test_client, auth_manager):
    """PUT /users/@me/presence sets the persisted status and echoes it back."""
    user, headers = _register_user(auth_manager)

    response = test_client.put(
        "/api/v1/users/@me/presence",
        json={"status": "dnd"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "dnd"
    assert body["user_id"] is not None

    follow_up = test_client.get(f"/api/v1/users/{user.id}/presence", headers=headers)
    assert follow_up.status_code == 200, follow_up.text
    assert follow_up.json()["status"] == "dnd"


def test_set_presence_with_custom_status(test_client, auth_manager):
    """A text custom status is persisted and returned."""
    user, headers = _register_user(auth_manager)

    response = test_client.put(
        "/api/v1/users/@me/presence",
        json={"status": "online", "custom_status": "brb", "custom_emoji": "☕"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["custom_status"] == "brb"
    assert body["custom_emoji"] == "☕"

    follow_up = test_client.get(f"/api/v1/users/{user.id}/presence", headers=headers)
    assert follow_up.status_code == 200
    assert follow_up.json()["custom_status"] == "brb"
    assert follow_up.json()["custom_emoji"] == "☕"


def test_set_presence_emoji_only(test_client, auth_manager):
    """An emoji-only custom status (empty text) must survive the API path."""
    user, headers = _register_user(auth_manager)

    response = test_client.put(
        "/api/v1/users/@me/presence",
        json={"status": "online", "custom_status": "", "custom_emoji": "🔥"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["custom_emoji"] == "🔥"

    follow_up = test_client.get(f"/api/v1/users/{user.id}/presence", headers=headers)
    assert follow_up.status_code == 200
    assert follow_up.json()["custom_emoji"] == "🔥"


def test_set_presence_invalid_status_rejected(test_client, auth_manager):
    """An unknown status value is a 400, not a silent accept or 500."""
    _, headers = _register_user(auth_manager)

    response = test_client.put(
        "/api/v1/users/@me/presence",
        json={"status": "party"},
        headers=headers,
    )
    assert response.status_code == 400


def test_set_presence_without_auth(test_client):
    """Setting presence without authentication fails with 401."""
    response = test_client.put(
        "/api/v1/users/@me/presence",
        json={"status": "online"},
    )
    assert response.status_code == 401


def test_get_presence_of_stranger_is_forbidden(
    test_client, auth_manager, second_test_user
):
    """Users without friendship/shared server cannot read others' presence."""
    _, headers = _register_user(auth_manager)
    stranger_id = second_test_user["user"].id

    response = test_client.get(f"/api/v1/users/{stranger_id}/presence", headers=headers)
    assert response.status_code == 404
