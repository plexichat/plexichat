"""OAuth attack regression tests for the applications authorization flow.

The three classic attacks against an authorization-code flow are covered end
to end at the core boundary:

* code interception — PKCE binds the code to a verifier only the client holds;
* redirect bypass — the callback URI is matched literally, never as a pattern;
* scope escalation — a token cannot carry a scope the application lacks.

The ID-token claim gate is covered too: a token with ``alg: none`` or an
unbound audience/expiry must never drive a login.
"""

import base64
import hashlib
import json
import time

import pytest
from fastapi import HTTPException

from src.api.routes.auth.oauth import _decode_id_token, _validate_id_token_claims
from src.core.applications.exceptions import (
    InvalidGrantError,
    InvalidPKCEError,
    InvalidRedirectUriError,
    InvalidScopeError,
)
from src.core.applications.oauth.flows import OAuth2Flow
from src.core.applications.oauth.tokens import (
    generate_client_secret,
    verify_pkce_challenge,
)

REDIRECT = "https://client.example.com/oauth/callback"


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _make_flow(app_manager, *, pkce_enabled: bool = False) -> OAuth2Flow:
    return OAuth2Flow(
        app_manager._db,
        {
            "code_expiry_seconds": 600,
            "token_expiry_seconds": 604800,
            "pkce_enabled": pkce_enabled,
        },
    )


def _app(app_manager, test_user, **kwargs):
    params = {
        "owner_id": test_user.id,
        "name": "Attack Regression App",
        "redirect_uris": [REDIRECT],
    }
    params.update(kwargs)
    return app_manager.create_application(**params)


def _with_secret(app_manager, app) -> str:
    secret, secret_hash = generate_client_secret()
    app_manager._db.execute(
        "UPDATE app_applications SET client_secret_hash = ? WHERE id = ?",
        (secret_hash, app.id),
    )
    return secret


class TestAuthorizationCodeInterception:
    def test_intercepted_code_without_verifier_is_rejected(
        self, app_manager, test_user
    ):
        """An attacker who steals ``code`` still cannot redeem it."""
        app = _app(app_manager, test_user)
        secret = _with_secret(app_manager, app)
        flow = _make_flow(app_manager)
        verifier = "v" * 43
        code = flow.create_authorization_code(
            app.id,
            test_user.id,
            REDIRECT,
            ["identify"],
            code_challenge=_s256(verifier),
            code_challenge_method="S256",
        )

        with pytest.raises(InvalidGrantError, match="verifier"):
            flow.exchange_code(app.id, secret, code.code, REDIRECT)  # pyright: ignore[reportArgumentType]

        # A wrong verifier is equally useless (constant-time comparison path).
        with pytest.raises(InvalidGrantError):
            flow.exchange_code(app.id, secret, code.code, REDIRECT, "x" * 43)  # pyright: ignore[reportArgumentType]

        # The legitimate client can still redeem it, and only once.
        token = flow.exchange_code(app.id, secret, code.code, REDIRECT, verifier)  # pyright: ignore[reportArgumentType]
        assert token.access_token
        with pytest.raises(InvalidGrantError, match="already used"):
            flow.exchange_code(app.id, secret, code.code, REDIRECT, verifier)  # pyright: ignore[reportArgumentType]

    def test_pkce_verifier_is_bounded_and_compared_in_constant_time(self):
        challenge = _s256("a" * 43)
        assert verify_pkce_challenge("a" * 43, challenge) is True
        assert verify_pkce_challenge("a" * 42, challenge) is False  # too short
        assert verify_pkce_challenge("a" * 129, challenge) is False  # too long
        assert verify_pkce_challenge("!" * 43, challenge) is False  # bad charset
        assert verify_pkce_challenge("a" * 43, "short") is False  # bad stored value

    def test_plain_method_and_malformed_challenge_rejected(
        self, app_manager, test_user
    ):
        app = _app(app_manager, test_user)
        flow = _make_flow(app_manager)
        with pytest.raises(InvalidPKCEError):
            flow.create_authorization_code(
                app.id,
                test_user.id,
                REDIRECT,
                ["identify"],
                code_challenge=_s256("a" * 43),
                code_challenge_method="plain",
            )
        with pytest.raises(InvalidPKCEError):
            flow.create_authorization_code(
                app.id,
                test_user.id,
                REDIRECT,
                ["identify"],
                code_challenge="not-a-challenge",
                code_challenge_method="S256",
            )

    def test_required_pkce_cannot_be_skipped(self, app_manager, test_user):
        app = _app(app_manager, test_user)
        flow = _make_flow(app_manager, pkce_enabled=True)
        with pytest.raises(InvalidPKCEError):
            flow.create_authorization_code(app.id, test_user.id, REDIRECT, ["identify"])


class TestRedirectBypass:
    def test_unregistered_and_lookalike_redirects_rejected(
        self, app_manager, test_user
    ):
        app = _app(app_manager, test_user)
        flow = _make_flow(app_manager)
        for candidate in (
            REDIRECT + "/",
            REDIRECT.replace("client.", "evil-client."),
            REDIRECT.replace("client.example.com", "example.com.evil.test"),
            "https://client.example.com/*",
        ):
            with pytest.raises(InvalidRedirectUriError):
                flow.validate_authorization_request(app.id, candidate, ["identify"])

    def test_code_cannot_be_exchanged_to_a_different_redirect(
        self, app_manager, test_user
    ):
        app = _app(app_manager, test_user)
        secret = _with_secret(app_manager, app)
        flow = _make_flow(app_manager)
        code = flow.create_authorization_code(
            app.id, test_user.id, REDIRECT, ["identify"]
        )
        with pytest.raises(InvalidGrantError, match="Redirect URI mismatch"):
            flow.exchange_code(
                app.id,
                secret,
                code.code,
                "https://attacker.example.com/cb",  # pyright: ignore[reportArgumentType]
            )
        # The code was not consumed by the failed attempt.
        assert flow.exchange_code(app.id, secret, code.code, REDIRECT).access_token  # pyright: ignore[reportArgumentType]

    def test_registration_rejects_wildcard_redirects(self, app_manager, test_user):
        with pytest.raises(ValueError):
            _app(app_manager, test_user, redirect_uris=["https://client.example.com/*"])


class TestScopeEscalation:
    def test_cannot_request_or_tokenize_scopes_the_app_lacks(
        self, app_manager, test_user
    ):
        app = _app(app_manager, test_user, allowed_scopes=["identify"])
        flow = _make_flow(app_manager)
        assert app.allowed_scopes == ["identify"]

        with pytest.raises(InvalidScopeError):
            flow.generate_authorization_url(app.id, REDIRECT, ["identify", "email"])
        with pytest.raises(InvalidScopeError):
            flow.validate_authorization_request(app.id, REDIRECT, ["email"])
        with pytest.raises(InvalidScopeError):
            flow.create_authorization_code(app.id, test_user.id, REDIRECT, ["email"])
        # Even a direct call to the token helper is gated.
        with pytest.raises(InvalidScopeError):
            flow._create_token(app.id, test_user.id, ["guilds.members.read"])

        code = flow.create_authorization_code(
            app.id, test_user.id, REDIRECT, ["identify"]
        )
        assert code.scopes == ["identify"]

    def test_explicit_scope_ceiling_cannot_contain_unknown_scopes(
        self, app_manager, test_user
    ):
        with pytest.raises(ValueError):
            _app(app_manager, test_user, allowed_scopes=["identify", "admin.all"])

    def test_ceiling_can_be_tightened_but_not_bypassed(self, app_manager, test_user):
        app = _app(app_manager, test_user, allowed_scopes=["identify", "email"])
        flow = _make_flow(app_manager)
        assert flow.create_authorization_code(
            app.id, test_user.id, REDIRECT, ["email"]
        ).scopes == ["email"]

        app_manager.update_application(
            user_id=test_user.id, application_id=app.id, allowed_scopes=["identify"]
        )
        with pytest.raises(InvalidScopeError):
            flow.create_authorization_code(app.id, test_user.id, REDIRECT, ["email"])


class TestIdTokenConfusion:
    @staticmethod
    def _jwt(header, claims):
        h = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
        p = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        return f"{h}.{p}.sig"

    @staticmethod
    def _claims(**overrides):
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": "client-id",
            "sub": "user-123",
            "iat": now,
            "exp": now + 600,
        }
        claims.update(overrides)
        return claims

    def _validate(self, jwt):
        header, claims = _decode_id_token(jwt)
        _validate_id_token_claims(header, claims, "google", "client-id")

    @pytest.mark.parametrize("alg", ["none", "HS256", None])
    def test_algorithm_confusion_rejected(self, alg):
        with pytest.raises(HTTPException) as exc:
            self._validate(self._jwt({"alg": alg}, self._claims()))
        assert "nonce" not in str(exc.value.detail).lower()

    @pytest.mark.parametrize(
        "overrides",
        [
            {"aud": "attacker-client"},
            {"iss": "https://evil.example.com"},
            {"exp": int(time.time()) - 60},
            {"iat": int(time.time()) + 600},
            {"sub": ""},
        ],
    )
    def test_unbound_or_expired_claims_rejected(self, overrides):
        with pytest.raises(HTTPException):
            self._validate(self._jwt({"alg": "RS256"}, self._claims(**overrides)))

    def test_well_formed_asymmetric_token_accepted(self):
        self._validate(self._jwt({"alg": "RS256", "typ": "JWT"}, self._claims()))
