"""A 4xx refusal must name the refusal, not mask it as a server error.

Routes map specific failures (not found, denied, validation, conflict) to a
4xx status. The accompanying message must be the canonical per-status text the
error-handling middleware already defines; shipping the generic 500 text on a
4xx tells the client nothing actionable and mislabels a client error as a bug.

The source ratchet below stops a new route from reintroducing the mismatch.
"""

import re
from pathlib import Path

_ROUTES = Path(__file__).resolve().parents[2] / "api" / "routes"

# The generic 500 detail dict, e.g. {"code": 403, "message": "Internal server error"}
_GENERIC_MESSAGE = re.compile(r'\{"code": (\d+), "message": "Internal server error"\}')


def test_no_4xx_response_reuses_the_500_message():
    offenders: list[str] = []
    for path in sorted(_ROUTES.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in _GENERIC_MESSAGE.finditer(text):
            if int(match.group(1)) != 500:
                offenders.append(
                    f"{path.relative_to(_ROUTES.parent.parent)}: {match.group(0)}"
                )
    assert not offenders, (
        "4xx responses must use their status's canonical message, not the "
        "generic 500 text:\n" + "\n".join(offenders)
    )


def test_password_reset_confirm_reports_a_bad_token_as_unauthorized(test_client):
    """A refused reset says why instead of an opaque 'Internal server error'."""
    response = test_client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "not-a-real-reset-token", "new_password": "NewPass123!"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Authentication required"
