"""Regression tests for version-negotiation input/error hardening.

Two classes of weakness are locked down here:

1. CWE-209 -- the 500 handler used to return ``str(e)`` to unauthenticated
   clients, leaking internal exception detail (paths, connection strings...).
2. Log injection / unbounded reflected response -- ``client_version`` was an
   unbounded, control-character-bearing string that was logged verbatim and
   echoed back in 400/426 error bodies.
"""

import pytest
from fastapi import HTTPException

from src.api.routes import version as version_routes
from src.api.schemas.version import VersionNegotiateRequest

pytestmark = pytest.mark.api


NEGOTIATE_URL = "/api/v1/version/negotiate"


def test_negotiate_500_does_not_leak_internal_exception_detail(
    test_client, monkeypatch
):
    """An internal exception message must never reach the client."""
    canary = "SECRET_INTERNAL_DETAIL_/srv/plexichat/db_password=hunter2"

    def explode():
        raise RuntimeError(canary)

    monkeypatch.setattr(version_routes.version_util, "min_supported", explode)

    response = test_client.post(NEGOTIATE_URL, json={"client_version": "1.0.0"})

    assert response.status_code == 500
    # The canary is present in the server log path, but must not appear
    # anywhere in the response body.
    assert canary not in response.text
    assert response.json()["error"]["message"] == "Internal server error"


def test_negotiate_handler_does_not_put_exception_text_in_detail(monkeypatch):
    """Lock the source fix directly, independent of the global error handler.

    The old handler built ``Failed to negotiate version: {str(e)}`` so the
    ``HTTPException.detail`` itself carried the raw exception text.
    """
    canary = "SECRET_INTERNAL_DETAIL_/srv/plexichat/db_password=hunter2"

    def explode():
        raise RuntimeError(canary)

    monkeypatch.setattr(version_routes.version_util, "min_supported", explode)

    with pytest.raises(HTTPException) as exc_info:
        version_routes.negotiate_version(
            VersionNegotiateRequest(client_version="1.0.0")
        )

    assert exc_info.value.status_code == 500
    assert canary not in str(exc_info.value.detail)
    assert exc_info.value.detail["error"]["message"] == "Internal server error"


def test_negotiate_bounds_client_version_length(test_client):
    """Overlong version strings are rejected before any work is done with them."""
    oversized = "9" * (version_routes.MAX_CLIENT_VERSION_LENGTH + 1)

    response = test_client.post(NEGOTIATE_URL, json={"client_version": oversized})

    # The app maps request-validation failures to 400 (docs/errors.md).
    assert response.status_code == 400
    # The unbounded value must not be reflected back in the error response.
    assert oversized not in response.text


def test_negotiate_strips_control_chars_before_logging_and_echoing(
    test_client, monkeypatch
):
    """A newline in client_version must not forge a log line or response line."""
    logged = []
    monkeypatch.setattr(
        version_routes.logger, "warning", lambda *args, **kwargs: logged.append(args)
    )

    forged = "1.0.0\nFAKE-ADMIN-LOGIN user=root"
    response = test_client.post(NEGOTIATE_URL, json={"client_version": forged})

    assert response.status_code == 400
    # No captured log call may contain a raw newline (log forging).
    for args in logged:
        rendered = " ".join(str(arg) for arg in args)
        assert "\n" not in rendered
        assert "\r" not in rendered
    # The echoed value in the error body is newline-free too.
    echoed = response.json()["error"]["client_version"]
    assert "\n" not in echoed and "\r" not in echoed
