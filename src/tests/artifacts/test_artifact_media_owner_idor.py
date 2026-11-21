"""Regression: a non-author must not weaponize artifact media cleanup.

``ArtifactManager._purge_artifact_media`` soft-deletes every media file id in an
artifact payload using ``artifact.author_id`` as the "owner". Any conversation
member or server manager may PATCH and DELETE another user's artifact
(``_authorize_scope`` grants edit/delete by membership or server permission),
so a non-author can inject the author's private file id into the payload and
have it deleted while bypassing the owner check.

This test drives the real API + DB-backed managers end to end.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from src.tests.media.test_access_control import MINI_PNG
from src.utils import encryption

pytestmark = [pytest.mark.integration, pytest.mark.security]


def _register_and_login(auth_manager, label):
    suffix = uuid.uuid4().hex[:8]
    username = f"{label}_{suffix}"
    email = f"{label}_{suffix}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username, email=email, password="TestPass123!"
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    return result.token, user


def test_non_author_cannot_delete_authors_media_via_payload_injection(
    test_client, auth_manager, media_manager, messaging_manager, db
):
    victim_token, victim = _register_and_login(auth_manager, "victim")
    attacker_token, attacker = _register_and_login(auth_manager, "attacker")
    victim_headers = {"Authorization": f"Bearer {victim_token}"}
    attacker_headers = {"Authorization": f"Bearer {attacker_token}"}

    # Attacker and victim share a conversation, which makes the attacker a
    # legitimate editor/deleter of the victim's conversation-scoped artifact.
    dm = messaging_manager.create_dm(victim.id, attacker.id)

    # Victim uploads a private file.
    upload = media_manager.upload_file(
        user_id=victim.id,
        file_data=MINI_PNG,
        filename="author_private_secret.png",
        content_type="image/png",
    )
    file_id = upload.file_id
    assert media_manager.get_file(file_id) is not None

    # Victim creates an artifact (personal scope at creation time).
    create = test_client.post(
        "/api/v1/artifacts",
        json={
            "artifact_type": "file",
            "title": "shared doc",
            "payload": {"content": "hello"},
        },
        headers=victim_headers,
    )
    assert create.status_code == 200, create.text
    artifact_id = create.json()["id"]

    # Bind it to the shared conversation so the attacker is a member with
    # artifact.edit/artifact.delete rights on the artifact.
    db.execute(
        "UPDATE artifacts SET conversation_id = ? WHERE id = ?", (dm.id, artifact_id)
    )

    # Attacker injects the victim's private file id into the payload.
    patch_resp = test_client.patch(
        f"/api/v1/artifacts/{artifact_id}",
        json={"payload": {"recording_file_id": file_id}},
        headers=attacker_headers,
    )
    assert patch_resp.status_code == 200, patch_resp.text

    # Attacker deletes the artifact; purge runs with the author's identity.
    delete = test_client.delete(
        f"/api/v1/artifacts/{artifact_id}", headers=attacker_headers
    )
    assert delete.status_code == 200, delete.text

    # Baseline exploit proof: before the fix the victim's file is soft-deleted.
    # The regression assertion is that the author's private file survives.
    assert media_manager.get_file(file_id) is not None, (
        "a non-author injected a file id into an artifact payload and had the "
        "author's private media deleted"
    )
