"""
End-to-end tests for the artifact PATCH rev guard.

The code/text editor stamps a monotonic ``rev`` into the payload on every
autosave. The PATCH route must reject writes whose ``rev`` is not strictly
newer than the persisted one (409 Conflict) so a stale tab cannot silently
overwrite a newer concurrent write — the lost-update race that the L45
client-side fix (persist-before-broadcast) guards against from the writer's
side and this guard closes server-side.
"""

from unittest.mock import patch

from src.utils import encryption


def _register_and_login(auth_manager):
    import uuid

    unique = uuid.uuid4().hex[:8]
    username = f"revuser_{unique}"
    email = f"rev_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username, email=email, password="TestPass123!"
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    return result.token, user


def _create_artifact(test_client, headers):
    resp = test_client.post(
        "/api/v1/artifacts",
        json={
            "artifact_type": "file",
            "title": "rev guard doc",
            "payload": {"content": "hello", "language": "plaintext"},
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


class TestPatchRevGuard:
    def test_stale_revision_rejected_with_409(self, test_client, auth_manager):
        token, _ = _register_and_login(auth_manager)
        headers = {"Authorization": f"Bearer {token}"}
        artifact_id = _create_artifact(test_client, headers)

        # First editor write: rev 1 (no persisted rev yet => allowed).
        first = test_client.patch(
            f"/api/v1/artifacts/{artifact_id}",
            json={"payload": {"content": "v1", "rev": 1}},
            headers=headers,
        )
        assert first.status_code == 200, first.text

        # A stale write (rev 1 again) must be rejected, not merged.
        stale = test_client.patch(
            f"/api/v1/artifacts/{artifact_id}",
            json={"payload": {"content": "stale overwrite", "rev": 1}},
            headers=headers,
        )
        assert stale.status_code == 409, stale.text
        body = stale.json()
        assert body["error"]["code"] == 409
        assert "revision" in body["error"]["message"]

        # The persisted content is untouched by the rejected write.
        current = test_client.get(f"/api/v1/artifacts/{artifact_id}", headers=headers)
        assert current.status_code == 200
        assert current.json()["payload"]["content"] == "v1"
        assert current.json()["payload"]["rev"] == 1

        # An older rev than the persisted one is also stale.
        older = test_client.patch(
            f"/api/v1/artifacts/{artifact_id}",
            json={"payload": {"content": "too old", "rev": 0}},
            headers=headers,
        )
        assert older.status_code == 409

    def test_newer_revision_accepted_and_persisted(self, test_client, auth_manager):
        token, _ = _register_and_login(auth_manager)
        headers = {"Authorization": f"Bearer {token}"}
        artifact_id = _create_artifact(test_client, headers)

        assert (
            test_client.patch(
                f"/api/v1/artifacts/{artifact_id}",
                json={"payload": {"content": "a", "rev": 1}},
                headers=headers,
            ).status_code
            == 200
        )
        ok = test_client.patch(
            f"/api/v1/artifacts/{artifact_id}",
            json={"payload": {"content": "b", "rev": 2}},
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["payload"]["content"] == "b"
        assert ok.json()["payload"]["rev"] == 2

    def test_writes_without_rev_not_guarded(self, test_client, auth_manager):
        """Non-editor updates (no ``rev``) are never considered stale."""
        token, _ = _register_and_login(auth_manager)
        headers = {"Authorization": f"Bearer {token}"}
        artifact_id = _create_artifact(test_client, headers)

        assert (
            test_client.patch(
                f"/api/v1/artifacts/{artifact_id}",
                json={"payload": {"content": "x", "rev": 3}},
                headers=headers,
            ).status_code
            == 200
        )
        # No rev in the write => guard skipped, write lands.
        no_rev = test_client.patch(
            f"/api/v1/artifacts/{artifact_id}",
            json={"payload": {"content": "y", "language": "python"}},
            headers=headers,
        )
        assert no_rev.status_code == 200, no_rev.text
        # Title-only update is likewise unaffected.
        title = test_client.patch(
            f"/api/v1/artifacts/{artifact_id}",
            json={"title": "renamed"},
            headers=headers,
        )
        assert title.status_code == 200, title.text
        assert title.json()["title"] == "renamed"

    def test_guard_ignores_non_numeric_rev(self, test_client, auth_manager):
        """A payload whose ``rev`` key is not a number bypasses the guard."""
        token, _ = _register_and_login(auth_manager)
        headers = {"Authorization": f"Bearer {token}"}
        artifact_id = _create_artifact(test_client, headers)

        # Persist a non-numeric rev (other artifact type using the key).
        assert (
            test_client.patch(
                f"/api/v1/artifacts/{artifact_id}",
                json={"payload": {"content": "a", "rev": "draft"}},
                headers=headers,
            ).status_code
            == 200
        )
        # Incoming numeric rev vs non-numeric persisted rev => no comparison.
        resp = test_client.patch(
            f"/api/v1/artifacts/{artifact_id}",
            json={"payload": {"content": "b", "rev": 1}},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

    def test_rejects_after_unauthorized(self, test_client, auth_manager):
        """Guard ordering: stale check happens after scope authorization."""
        token_a, _ = _register_and_login(auth_manager)
        token_b, _ = _register_and_login(auth_manager)
        headers_a = {"Authorization": f"Bearer {token_a}"}
        artifact_id = _create_artifact(test_client, headers_a)

        # A non-author has no permission to update a personal-scope artifact;
        # they must get 403, not a 409 rev error.
        intruder = test_client.patch(
            f"/api/v1/artifacts/{artifact_id}",
            json={"payload": {"content": "hax", "rev": 1}},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert intruder.status_code == 403, intruder.text
