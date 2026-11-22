"""Regression: a failed auth check must not log a secret-bearing request path.

``get_current_user`` used to log ``request.url.path`` verbatim at DEBUG when no
user was resolved.  For an authenticated route whose path carries a credential
-- e.g. ``DELETE /features/push/tokens/{token_value}`` -- every unauthenticated
or failed-auth request therefore wrote the live push token to the log sink,
bypassing the access-log redactor (the same bug class as dfb27bf5's exception
handlers).  The fix routes that log line through ``_safe_log_path``, which emits
the matched route template (``.../{token_value}``) and fails closed.

Before the fix this test FAILS: the canary appears verbatim in the
``get_current_user`` debug line.  After the fix it passes.
"""

from __future__ import annotations

import logging
import uuid

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

CANARY = "CANARY_PUSH_TOKEN_" + uuid.uuid4().hex


def test_failed_auth_does_not_log_raw_secret_path(test_client, caplog) -> None:
    url = f"/api/v1/features/push/tokens/{CANARY}"

    with caplog.at_level(logging.DEBUG, logger="AppLogger"):
        response = test_client.delete(url)

    # Non-vacuity: the request must actually reach the auth gate, otherwise no
    # get_current_user log line is emitted and the assertion below is vacuous.
    assert response.status_code == 401, response.text

    lines = [record.getMessage() for record in caplog.records]
    auth_lines = [
        line for line in lines if "get_current_user: No user in state for path" in line
    ]
    assert auth_lines, (
        "expected a get_current_user failed-auth log line so the assertion is "
        "non-vacuous; got: " + "\n".join(lines)
    )

    rendered = "\n".join(lines)
    assert CANARY not in rendered, (
        "push token leaked into the failed-auth log: " + "\n".join(auth_lines)
    )
    assert "{token_value}" in "\n".join(auth_lines), (
        "failed-auth log should show the redacted route template rather than "
        "the resolved path: " + "\n".join(auth_lines)
    )
