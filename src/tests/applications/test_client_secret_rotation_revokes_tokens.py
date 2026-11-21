"""Regression: regenerating a client secret must revoke that app's tokens.

A client secret is rotated when the old one may have leaked. Every OAuth
access/refresh token in ``app_oauth_tokens`` was minted on the strength of
that secret, so leaving those rows live lets a party holding a stolen secret
and a stolen bearer token keep acting as the application's users after the
rotation. Rotating the secret now consumes all outstanding tokens for the
application before installing the new secret hash.
"""

from typing import Any, cast

import pytest

from src.core.applications.exceptions import (
    InvalidClientError,
    TokenRevokedError,
)
from src.core.applications.oauth.flows import OAuth2Flow
from src.core.applications.oauth.tokens import generate_client_secret

REDIRECT = "https://client.example.com/oauth/callback"


def _flow(db) -> OAuth2Flow:
    return OAuth2Flow(
        db,
        {
            "code_expiry_seconds": 600,
            "token_expiry_seconds": 604800,
            "refresh_enabled": True,
        },
    )


@pytest.mark.applications
class TestClientSecretRotationRevokesTokens:
    def test_regeneration_revokes_all_tokens_for_the_application(
        self, app_manager, test_user
    ):
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="Rotation App",
            redirect_uris=[REDIRECT],
        )
        old_secret, old_hash = generate_client_secret()
        app_manager._db.execute(
            "UPDATE app_applications SET client_secret_hash = ? WHERE id = ?",
            (old_hash, app.id),
        )
        flow = _flow(app_manager._db)

        code = flow.create_authorization_code(
            app.id, test_user.id, REDIRECT, ["identify"]
        )
        token = flow.exchange_code(app.id, old_secret, cast(Any, code.code), REDIRECT)
        assert token.access_token and token.refresh_token

        # Sanity: the token is genuinely live before the rotation. Without
        # this the assertions below would pass against an already-dead token
        # and the test would be vacuous.
        info = flow.verify_access_token(token.access_token)
        assert info["user_id"] == test_user.id
        assert info["application_id"] == app.id
        row = app_manager._db.fetch_one(
            "SELECT revoked FROM app_oauth_tokens WHERE id = ?", (token.id,)
        )
        assert row["revoked"] == 0

        # Rotate the application's client secret.
        new_secret = app_manager.regenerate_client_secret(test_user.id, app.id)
        assert new_secret and new_secret != old_secret

        # The previously issued access token is dead.
        with pytest.raises(TokenRevokedError):
            flow.verify_access_token(token.access_token)
        row = app_manager._db.fetch_one(
            "SELECT revoked FROM app_oauth_tokens WHERE id = ?", (token.id,)
        )
        assert row["revoked"] == 1

        # An attacker holding the stolen *old* secret can no longer refresh
        # (its hash was replaced) ...
        with pytest.raises(InvalidClientError):
            flow.refresh_token(app.id, old_secret, token.refresh_token)
        # ... and even the legitimate new secret cannot resurrect the now
        # revoked refresh token.
        with pytest.raises(TokenRevokedError):
            flow.refresh_token(app.id, new_secret, token.refresh_token)

    def test_regeneration_only_revokes_tokens_of_the_target_application(
        self, app_manager, test_user
    ):
        app_a = app_manager.create_application(
            owner_id=test_user.id, name="Rotation Target", redirect_uris=[REDIRECT]
        )
        app_b = app_manager.create_application(
            owner_id=test_user.id, name="Other App", redirect_uris=[REDIRECT]
        )
        flow = _flow(app_manager._db)

        def issue(app):
            secret, digest = generate_client_secret()
            app_manager._db.execute(
                "UPDATE app_applications SET client_secret_hash = ? WHERE id = ?",
                (digest, app.id),
            )
            code = flow.create_authorization_code(
                app.id, test_user.id, REDIRECT, ["identify"]
            )
            return flow.exchange_code(app.id, secret, cast(Any, code.code), REDIRECT)

        token_a = issue(app_a)
        token_b = issue(app_b)

        app_manager.regenerate_client_secret(test_user.id, app_a.id)

        with pytest.raises(TokenRevokedError):
            flow.verify_access_token(token_a.access_token)
        # Unrelated application's tokens must remain untouched.
        assert flow.verify_access_token(token_b.access_token)["user_id"] == test_user.id
