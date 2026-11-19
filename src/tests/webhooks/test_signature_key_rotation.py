"""Regression: rotating a webhook signing key leaves no acceptance window.

A rotation must replace the public key used to verify inbound deliveries and
the encrypted private key used to sign outbound ones together. There is no
grace period during which a signature produced with the retired key is still
valid, and the two key columns are never updated in separate statements.
"""

import base64

import pytest

from src.core.webhooks.exceptions import PermissionDeniedError, WebhookNotFoundError


class _RecordingDB:
    """Delegating database wrapper that records the SQL it is asked to run.

    Recording at the handle the manager actually holds keeps the assertion
    independent of which concrete database class (or proxy) the fixture
    supplied: patching the class of ``manager._db`` only observes calls when
    the manager happens to use that exact class.
    """

    def __init__(self, inner):
        self._inner = inner
        self.executed = []

    def execute(self, query, *args, **kwargs):
        self.executed.append(query)
        return self._inner.execute(query, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _manager_for(webhooks):
    """Return the manager instance that runs the database work.

    ``setup["webhooks"]`` is either the manager itself or the module facade,
    which lazily exposes the configured manager through ``_get_manager()``.
    """
    getter = getattr(webhooks, "_get_manager", None)
    return getter() if callable(getter) else webhooks


def _verify(manager, webhook_id, payload, headers):
    return manager.verify_payload(
        webhook_id,
        payload,
        headers["X-Plexichat-Signature"],
        headers["X-Plexichat-Timestamp"],
    )


def test_signatures_from_retired_key_are_rejected_immediately(webhook_with_token):
    setup = webhook_with_token
    manager = setup["webhooks"]
    webhook_id = setup["webhook"].id

    # Two payloads signed with the OLD key before the rotation. We verify one
    # to prove the old key works, and keep the other unconsumed in the replay
    # cache so the post-rotation rejection can only be due to the key change.
    pre_rotation = {"event": "rotation.pre"}
    pending = {"event": "rotation.pending"}
    pre_headers = manager.sign_payload(webhook_id, pre_rotation)
    pending_headers = manager.sign_payload(webhook_id, pending)
    assert _verify(manager, webhook_id, pre_rotation, pre_headers) is True

    new_public_key = manager.rotate_signing_key(webhook_id, setup["owner"].id)
    row = setup["db"].fetch_one(
        "SELECT signing_key_public FROM webhook_webhooks WHERE id = ?", (webhook_id,)
    )
    assert bytes(row["signing_key_public"]) == bytes(new_public_key)

    # No grace window: a fresh, never-replayed signature made with the old key
    # is rejected straight after the rotation.
    assert _verify(manager, webhook_id, pending, pending_headers) is False

    # The new key signs verifiable deliveries.
    fresh_headers = manager.sign_payload(webhook_id, pending)
    assert _verify(manager, webhook_id, pending, fresh_headers) is True


def test_rotation_updates_both_key_columns_in_one_statement(
    webhook_with_token, monkeypatch
):
    setup = webhook_with_token
    manager = _manager_for(setup["webhooks"])
    recorder = _RecordingDB(manager._db)
    monkeypatch.setattr(manager, "_db", recorder)

    manager.rotate_signing_key(setup["webhook"].id, setup["owner"].id)

    updates = [
        sql for sql in recorder.executed if "update webhook_webhooks" in sql.lower()
    ]
    assert len(updates) == 1, "key rotation must be a single atomic UPDATE"
    assert "signing_key_public" in updates[0]
    assert "signing_key_private_encrypted" in updates[0]


def test_module_facade_rotates_signing_key(webhook_with_token, monkeypatch):
    from src.core import webhooks

    setup = webhook_with_token
    monkeypatch.setattr(webhooks, "_manager", setup["webhooks"], raising=False)
    monkeypatch.setattr(webhooks, "_setup_complete", True, raising=False)
    new_key = webhooks.rotate_signing_key(setup["owner"].id, setup["webhook"].id)
    assert len(bytes(new_key)) == 32


def test_rotation_requires_manage_permission(webhook_with_token):
    setup = webhook_with_token
    with pytest.raises(PermissionDeniedError):
        setup["webhooks"].rotate_signing_key(
            setup["webhook"].id, setup["non_member"].id
        )


def test_rotation_of_unknown_webhook_raises(webhook_with_token):
    setup = webhook_with_token
    with pytest.raises(WebhookNotFoundError):
        setup["webhooks"].rotate_signing_key(999999, setup["owner"].id)


def test_verify_rejects_malformed_text_public_key(webhook_with_token):
    setup = webhook_with_token
    manager = setup["webhooks"]
    webhook_id = setup["webhook"].id
    payload = {"event": "malformed-key"}
    headers = manager.sign_payload(webhook_id, payload)

    # The base64 TEXT storage form used to skip the explicit 32-byte length
    # check; make sure a short key is rejected before any crypto operation.
    bad_key = base64.b64encode(b"\x01\x02\x03").decode("ascii")
    setup["db"].execute(
        "UPDATE webhook_webhooks SET signing_key_public = ? WHERE id = ?",
        (bad_key, webhook_id),
    )
    assert _verify(manager, webhook_id, payload, headers) is False
