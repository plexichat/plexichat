"""Regression: no legacy non-PKCE code exchange downgrade.

The PKCE wave bound an S256 challenge to authorization codes and required a
matching verifier at exchange time. A deployment that mandates PKCE must not
redeem a challenge-less code (for example one created before the migration or
through a path that bypassed challenge binding); doing so would let anyone
holding the code skip PKCE entirely.
"""

import base64
import hashlib

import pytest

from src.core.applications.exceptions import InvalidPKCEError
from src.core.applications.oauth.flows import OAuth2Flow
from src.core.applications.oauth.tokens import generate_client_secret

REDIRECT = "https://client.example.com/oauth/callback"


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _app(app_manager, test_user):
    return app_manager.create_application(
        owner_id=test_user.id,
        name="No Downgrade App",
        redirect_uris=[REDIRECT],
    )


def _with_secret(app_manager, app) -> str:
    secret, secret_hash = generate_client_secret()
    app_manager._db.execute(
        "UPDATE app_applications SET client_secret_hash = ? WHERE id = ?",
        (secret_hash, app.id),
    )
    return secret


def _flow(app_manager, *, pkce_enabled: bool) -> OAuth2Flow:
    return OAuth2Flow(
        app_manager._db,
        {
            "code_expiry_seconds": 600,
            "token_expiry_seconds": 604800,
            "pkce_enabled": pkce_enabled,
        },
    )


def test_legacy_challenge_less_code_cannot_be_exchanged_when_pkce_required(
    app_manager, test_user
):
    app = _app(app_manager, test_user)
    secret = _with_secret(app_manager, app)

    # Issue a challenge-less code the way a pre-PKCE deployment would.
    legacy = _flow(app_manager, pkce_enabled=False).create_authorization_code(
        app.id, test_user.id, REDIRECT, ["identify"]
    )
    assert legacy.code_challenge is None

    strict = _flow(app_manager, pkce_enabled=True)
    with pytest.raises(InvalidPKCEError, match="PKCE"):
        strict.exchange_code(app.id, secret, legacy.code, REDIRECT, "v" * 43)  # pyright: ignore[reportArgumentType]


def test_pkce_bound_code_still_exchanges_normally(app_manager, test_user):
    app = _app(app_manager, test_user)
    secret = _with_secret(app_manager, app)
    verifier = "v" * 43
    flow = _flow(app_manager, pkce_enabled=True)
    code = flow.create_authorization_code(
        app.id,
        test_user.id,
        REDIRECT,
        ["identify"],
        code_challenge=_s256(verifier),
        code_challenge_method="S256",
    )
    token = flow.exchange_code(app.id, secret, code.code, REDIRECT, verifier)  # pyright: ignore[reportArgumentType]
    assert token.access_token


def test_non_pkce_deployment_still_exchanges_challenge_less_code(
    app_manager, test_user
):
    """The strict check is scoped to deployments that require PKCE."""
    app = _app(app_manager, test_user)
    secret = _with_secret(app_manager, app)
    flow = _flow(app_manager, pkce_enabled=False)
    code = flow.create_authorization_code(app.id, test_user.id, REDIRECT, ["identify"])
    token = flow.exchange_code(app.id, secret, code.code, REDIRECT)  # pyright: ignore[reportArgumentType]
    assert token.access_token
