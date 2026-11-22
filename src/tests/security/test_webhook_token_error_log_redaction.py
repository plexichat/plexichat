"""Regression: webhook execution errors must not log the live token.

``POST /api/v1/webhooks/{webhook_id}/{token}`` carries a live webhook secret in
the URL's final path segment.  The access-log middleware already redacts it,
but the FastAPI exception handlers used to log ``request.url.path`` verbatim.
A rejected webhook delivery (e.g. an oversized body that raises
``RequestValidationError``) therefore wrote the credential to ``latest.log``
on every failure, bypassing the access-log redactor entirely.

This test drives the real in-process client and inspects the emitted log
records.  It fails before the fix (canary present) and passes after.
"""

from __future__ import annotations

import logging
import uuid

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

# A distinctive canary placed in the final (token) path segment.  The webhook
# row's token_hash is updated to match so the request is well-formed apart from
# the deliberately oversized body that triggers the validation error.
CANARY = "CANARY_WEBHOOK_TOKEN_" + uuid.uuid4().hex


def test_webhook_token_absent_from_validation_error_log(
    test_client, modules, db, test_user_with_token, caplog
) -> None:
    import hashlib

    owner = test_user_with_token["user"]
    servers = modules.servers
    server = servers.create_server(owner.id, "Error Log Server")
    channel = servers.create_channel(owner.id, server.id, "error-log-general")
    webhook = modules.webhooks.create_webhook(
        user_id=owner.id, channel_id=channel.id, name="Error Log Webhook"
    )

    # Make the canary a valid token for this webhook so validation fails only
    # because of the oversized body, not because of the token itself.
    full_token = f"webhook.{webhook.id}.{CANARY}"
    db.execute(
        "UPDATE webhook_webhooks SET token_hash = ? WHERE id = ?",
        (hashlib.sha256(full_token.encode()).hexdigest(), webhook.id),
    )

    url = f"/api/v1/webhooks/{webhook.id}/{CANARY}"

    with caplog.at_level(logging.WARNING, logger="AppLogger"):
        response = test_client.post(url, json={"content": "x" * 5000})

    # The malformed body must actually be rejected, otherwise the test is
    # vacuous: no validation-error handler runs and nothing is logged.
    assert response.status_code in (400, 422), response.text

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    validation_lines = [
        line for line in rendered.splitlines() if "Validation error on" in line
    ]
    assert validation_lines, (
        "expected a validation-error log line so the assertion is non-vacuous; "
        f"got: {rendered}"
    )
    assert CANARY not in rendered, (
        "live webhook token leaked into the validation-error log: " + rendered
    )
    # The redacted form must be present so we know the path was logged through
    # the redactor rather than simply dropped.
    assert "{token}" in "\n".join(validation_lines), (
        "validation-error log should show a redacted webhook path: "
        + "\n".join(validation_lines)
    )
