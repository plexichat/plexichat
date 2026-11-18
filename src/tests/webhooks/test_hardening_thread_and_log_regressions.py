"""Regressions for webhook thread scoping and log redaction hardening."""

import logging
import uuid

import pytest

from src.core.webhooks.exceptions import InvalidWebhookContentError


def test_execute_rejects_thread_from_foreign_server(webhook_with_token):
    """A token holder must not redirect a delivery into another guild.

    ``thread_id`` is caller-controlled. Resolving it against an arbitrary
    ``srv_channels`` row and adopting that row's ``conversation_id`` let a
    webhook bound to guild A post into a channel belonging to guild B.
    """
    setup = webhook_with_token

    other_owner = setup["auth"].register(
        username=f"other_owner_{uuid.uuid4().hex[:8]}",
        email=f"other_owner_{uuid.uuid4().hex[:8]}@example.com",
        password="TestPass123!",
    )
    other_server = setup["servers"].create_server(other_owner.id, "Foreign Guild")
    other_channels = setup["servers"].get_channels(other_owner.id, other_server.id)
    assert other_channels, "expected the foreign guild to have a default channel"
    foreign_channel_id = other_channels[0].id

    with pytest.raises(InvalidWebhookContentError):
        setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="cross-guild delivery",
            thread_id=foreign_channel_id,
            wait=True,
        )


def test_avatar_rejection_log_does_not_leak_url_secrets(
    webhook_manager, monkeypatch, caplog
):
    """The unsafe-URL warning must not log attacker-controlled query data.

    Avatar URLs are untrusted; presigned query strings and embedded
    credentials must never reach the logs, and embedded newlines must not
    be able to forge extra log lines.
    """
    from src.core.webhooks.exceptions import WebhookAvatarError

    class _RejectingValidator:
        def validate_url_for_request(self, url):
            raise ValueError("host resolves to a forbidden address")

    monkeypatch.setattr(
        "src.utils.security.URLValidator", lambda: _RejectingValidator()
    )

    secret_url = (
        "https://cdn.example.com/a.png?X-Amz-Signature=deadbeef"
        "\ninjected-log-line"
    )

    with caplog.at_level(logging.WARNING, logger="utils.logger"):
        with pytest.raises(WebhookAvatarError):
            webhook_manager._validate_avatar_url(secret_url)

    rendered = caplog.text
    assert "X-Amz-Signature" not in rendered
    assert "deadbeef" not in rendered
    assert "injected-log-line" not in rendered
