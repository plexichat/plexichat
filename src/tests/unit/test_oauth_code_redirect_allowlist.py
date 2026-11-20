"""Authorization codes must be bound to a registered redirect URI.

``validate_authorization_request`` checks the callback against the app's
registered allow-list, but ``create_authorization_code`` is a separate entry
point. A caller that reaches code creation with an attacker-supplied,
well-formed callback must not be able to mint a code bound to a URI the app
owner never registered — that is the open-redirect / code-interception
weakness this module exists to prevent.
"""

import pytest

from src.core.applications.exceptions import InvalidRedirectUriError
from src.core.applications.oauth.flows import OAuth2Flow

REGISTERED = "https://client.example.com/oauth/callback"
ATTACKER = "https://attacker.example/steal"


def _flow(app_manager):
    return OAuth2Flow(
        app_manager._db,
        {"code_expiry_seconds": 600, "token_expiry_seconds": 604800},
    )


def _app(app_manager, test_user):
    return app_manager.create_application(
        owner_id=test_user.id,
        name="Redirect Bound App",
        redirect_uris=[REGISTERED],
    )


def test_code_rejects_unregistered_redirect(app_manager, test_user):
    app = _app(app_manager, test_user)
    flow = _flow(app_manager)

    with pytest.raises(InvalidRedirectUriError):
        flow.create_authorization_code(app.id, test_user.id, ATTACKER, ["identify"])


def test_code_still_accepts_registered_redirect(app_manager, test_user):
    app = _app(app_manager, test_user)
    flow = _flow(app_manager)

    code = flow.create_authorization_code(
        app.id, test_user.id, REGISTERED, ["identify"]
    )
    assert code.code
    assert code.redirect_uri == REGISTERED


def test_code_fails_closed_on_malformed_allowlist(app_manager, test_user):
    app = _app(app_manager, test_user)
    app_manager._db.execute(
        "UPDATE app_applications SET redirect_uris = ? WHERE id = ?",
        ("not-json", app.id),
    )
    flow = _flow(app_manager)

    with pytest.raises(InvalidRedirectUriError):
        flow.create_authorization_code(app.id, test_user.id, REGISTERED, ["identify"])
