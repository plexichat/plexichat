"""SEC100 - end-to-end invariant: malformed public input is rejected, not crashed.

A sampler of unauthenticated (public) routes is fed hostile values in path
segments, query parameters and JSON bodies.  The invariant is that the app
answers with a deterministic client error (4xx) and never:

* a 5xx (an unhandled exception / fail-open crash), or
* a raw framework error marker leaked into the response body.

All requests go through the in-process ``TestClient``.  Assertions compare
status codes and literal substrings only - no timing, no wall-clock checks.
"""

from __future__ import annotations

import pytest

# Distinctive markers that must never appear in a client-facing body.
_ERROR_MARKERS = (
    "traceback",
    "internal server error",
)

# (label, method, url, json_body)
MALICIOUS_REQUESTS = [
    # Oversized numeric identifiers (far beyond the snowflake bound).
    ("oversized avatar user id", "GET", "/api/v1/avatars/users/" + "9" * 120, None),
    ("oversized avatar server id", "GET", "/api/v1/avatars/servers/" + "9" * 120, None),
    ("oversized bot application id", "GET", "/api/v1/bots/profiles/" + "9" * 120, None),
    # Control characters injected into path segments (NUL, CR/LF header break).
    ("nul byte in path id", "GET", "/api/v1/avatars/users/%00abc", None),
    (
        "crlf in path id",
        "GET",
        "/api/v1/avatars/users/1%0d%0aX-Injected:%20yes",
        None,
    ),
    (
        "control char in path id",
        "GET",
        "/api/v1/bots/profiles/%01%02%03",
        None,
    ),
    # Oversized query payload.
    ("oversized query value", "GET", "/api/v1/qr?data=" + "A" * 20000, None),
    # Control character in a JSON body field on a public auth route.
    (
        "control char in registration body",
        "POST",
        "/api/v1/auth/register",
        {
            "username": "bad\x00user",
            "email": "baduser@example.com",
            "password": "TestPass123!",
        },
    ),
]


@pytest.mark.parametrize(
    ("label", "method", "url", "json_body"),
    MALICIOUS_REQUESTS,
    ids=[case[0] for case in MALICIOUS_REQUESTS],
)
def test_malicious_public_input_is_rejected(test_client, label, method, url, json_body):
    kwargs = {"json": json_body} if json_body is not None else {}
    response = test_client.request(method, url, **kwargs)

    assert 400 <= response.status_code < 500, (
        f"{label}: expected a 4xx, got {response.status_code}: {response.text}"
    )
    body_lower = response.text.lower()
    for marker in _ERROR_MARKERS:
        assert marker not in body_lower, f"{label}: leaked error marker {marker!r}"


def test_public_route_sample_is_non_vacuous(test_client):
    """Guard against an empty/skipped malicious-request table."""
    assert len(MALICIOUS_REQUESTS) >= 8
    methods = {case[1] for case in MALICIOUS_REQUESTS}
    assert "GET" in methods and "POST" in methods
