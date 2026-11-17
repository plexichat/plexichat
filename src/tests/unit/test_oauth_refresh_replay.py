"""Regression: a refresh token is single-use even under concurrent refresh.

``refresh_token`` used to read ``revoked = 0``, verify the secret, and then run
an unconditional ``UPDATE ... SET revoked = 1`` with no rowcount guard. Two
refresh requests that interleaved around that read could both pass the check
and mint a fresh token pair from a single refresh token — a classic refresh
token replay. The fix consumes the refresh token with a conditional UPDATE and
a fail-closed rowcount guard, mirroring authorization-code exchange.

These tests simulate the exact interleaving (both callers observe an
unrevoked row) with a DB proxy that forces a stale read while writes still go
to the real database, so the race is reproduced deterministically rather than
by flaky threading.
"""

import pytest

from src.core.applications.exceptions import InvalidGrantError, TokenRevokedError
from src.core.applications.oauth.flows import OAuth2Flow
from src.core.applications.oauth.tokens import generate_client_secret

REDIRECT = "https://client.example.com/oauth/callback"


def _app(app_manager, test_user):
    return app_manager.create_application(
        owner_id=test_user.id,
        name="Refresh Replay App",
        redirect_uris=[REDIRECT],
    )


def _with_secret(app_manager, app) -> str:
    secret, secret_hash = generate_client_secret()
    app_manager._db.execute(
        "UPDATE app_applications SET client_secret_hash = ? WHERE id = ?",
        (secret_hash, app.id),
    )
    return secret


def _flow(db, *, refresh_enabled=True) -> OAuth2Flow:
    return OAuth2Flow(
        db,
        {
            "code_expiry_seconds": 600,
            "token_expiry_seconds": 604800,
            "refresh_enabled": refresh_enabled,
        },
    )


def _issue_token(app_manager, test_user):
    app = _app(app_manager, test_user)
    secret = _with_secret(app_manager, app)
    flow = _flow(app_manager._db)
    code = flow.create_authorization_code(
        app.id, test_user.id, REDIRECT, ["identify"]
    )
    token = flow.exchange_code(app.id, secret, code.code, REDIRECT)
    return app, secret, token


class _StaleReadDB:
    """Force the token lookup to see a stale, unrevoked row.

    Every other query and every write is delegated to the real database. This
    models two refresh calls that both read ``revoked = 0`` before either
    performs its consume write.
    """

    def __init__(self, db):
        self._db = db
        self._stale = None

    def arm(self, token_id):
        row = self._db.fetch_one(
            "SELECT id, application_id, user_id, refresh_token_hash, scopes, revoked "
            "FROM app_oauth_tokens WHERE id = ?",
            (token_id,),
        )
        assert row is not None and row["revoked"] == 0
        self._stale = dict(row)

    def fetch_one(self, query, params=()):
        if "FROM app_oauth_tokens" in query and self._stale is not None:
            return dict(self._stale)
        return self._db.fetch_one(query, params)

    def execute(self, query, params=(), **kwargs):
        return self._db.execute(query, params, **kwargs)


class _NoRowCountCursor:
    rowcount = None


class _NoRowCountDB:
    """Simulate a driver that does not expose rowcount on writes."""

    def __init__(self, db):
        self._db = db

    def fetch_one(self, query, params=()):
        return self._db.fetch_one(query, params)

    def execute(self, query, params=(), **kwargs):
        result = self._db.execute(query, params, **kwargs)
        if "SET revoked = 1" in query:
            return _NoRowCountCursor()
        return result


@pytest.mark.unit
class TestRefreshTokenReplay:
    def test_sequential_reuse_is_rejected(self, app_manager, test_user):
        app, secret, token = _issue_token(app_manager, test_user)
        flow = _flow(app_manager._db)

        first = flow.refresh_token(app.id, secret, token.refresh_token)
        assert first.access_token

        with pytest.raises(TokenRevokedError):
            flow.refresh_token(app.id, secret, token.refresh_token)

    def test_concurrent_refresh_cannot_double_spend(self, app_manager, test_user):
        """Two callers that both read revoked=0 must not both mint tokens."""
        app, secret, token = _issue_token(app_manager, test_user)

        proxy = _StaleReadDB(app_manager._db)
        proxy.arm(token.id)
        flow = _flow(proxy)

        # First caller wins the atomic consume.
        assert flow.refresh_token(app.id, secret, token.refresh_token).access_token

        # Second caller still sees a stale unrevoked row, but the conditional
        # consume reports zero affected rows and the exchange fails closed.
        with pytest.raises(InvalidGrantError, match="already used"):
            flow.refresh_token(app.id, secret, token.refresh_token)

        # Only one refresh token pair exists beyond the original record.
        rows = app_manager._db.fetch_one(
            "SELECT COUNT(*) AS n FROM app_oauth_tokens WHERE application_id = ?",
            (app.id,),
        )
        assert rows["n"] == 2  # original + the single successful refresh

    def test_missing_rowcount_fails_closed(self, app_manager, test_user):
        """A driver without rowcount must never bypass the single-use guard."""
        app, secret, token = _issue_token(app_manager, test_user)

        proxy = _NoRowCountDB(app_manager._db)
        with pytest.raises(InvalidGrantError, match="atomically consumed"):
            _flow(proxy).refresh_token(app.id, secret, token.refresh_token)
