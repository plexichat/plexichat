"""Regression: request-validation errors must not log raw credentials.

``exc.errors()`` embeds the rejected value under ``input``.  On auth routes
that value is the password / reset token / TOTP code the client just sent, so
writing the error list verbatim to the log persists a live credential in
cleartext.  Pydantic names the key ``input`` (not ``password``/``token``), so
the shared ``sanitize_log_message`` keyword patterns do not catch it.

The test drives the real FastAPI exception handler through the in-process
client with a deliberately short canary password and inspects the emitted log
record.  It fails before the fix (canary present) and passes after.
"""

from __future__ import annotations

import logging


# Register's password field caps input at 1024 chars; an oversized value is a
# realistic malformed-credential case that Pydantic rejects and reflects in
# ``exc.errors()['input']``.
CANARY_PASSWORD = "CanaryPw-" + "x" * 1030


def test_validation_error_log_does_not_contain_submitted_password(
    test_client, caplog
) -> None:
    with caplog.at_level(logging.WARNING, logger="AppLogger"):
        response = test_client.post(
            "/api/v1/auth/register",
            json={
                "username": "leak_probe",
                "email": "leak_probe@example.com",
                "password": CANARY_PASSWORD,
            },
        )

    assert response.status_code in (400, 422), response.text
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert CANARY_PASSWORD not in rendered, (
        "raw password from exc.errors()['input'] leaked into the log: " + rendered
    )


def test_validation_error_log_does_not_contain_submitted_reset_token(
    test_client, caplog
) -> None:
    # A non-string token fails Pydantic's str validation and is reflected in
    # the error's ``input``.  It must not survive into the log either.
    canary = {"canary": "RESET_TOKEN_SHOULD_NOT_BE_LOGGED"}
    with caplog.at_level(logging.WARNING, logger="AppLogger"):
        response = test_client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": canary, "new_password": "ValidPass123!"},
        )

    assert response.status_code in (400, 422), response.text
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "RESET_TOKEN_SHOULD_NOT_BE_LOGGED" not in rendered, (
        "raw reset-token input leaked into the log: " + rendered
    )
