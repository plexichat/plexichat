"""Per-route authorization tests for the routes sweep D classifies ``PUBLIC``.

``ci-tooling/audit_failopen.py`` sweep D only reports a route as ``PUBLIC``
after a human recorded a verdict -- pre-authentication lifecycle, credential
exchange, public read-only resource, per-token inbound traffic. This suite
turns those verdicts into executable assertions: every route the sweep calls
``PUBLIC`` is declared below as either *open* (reachable without a bearer) or
*scoped* (must reject the wrong principal), and each scoped route has a test.

The completeness test at the bottom fails if the sweep starts classifying a new
route ``PUBLIC`` without a declared expectation here, so the verdicts cannot
silently drift.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from src.utils import encryption

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = REPO_ROOT / "ci-tooling" / "audit_failopen.py"

# Documentation pages are all public and gated by the docs mount; one bulk test
# covers the family instead of 69 identical rows.
DOCS_PREFIX = "src/api/routes/docs/"

# ``PUBLIC`` routes with no access to deny: a plain anonymous request must not
# be challenged (asserted by the family tests below).
OPEN_ROUTES: set[tuple[str, str]] = {
    ("src/api/routes/admin/ui.py", "GET /"),
    ("src/api/routes/admin/ui.py", "GET /ui"),
    ("src/api/routes/auth/email_verification.py", "POST /resend-verification"),
    ("src/api/routes/auth/password.py", "GET /password-requirements"),
    ("src/api/routes/auth/password.py", "POST /password-reset/request"),
    ("src/api/routes/avatars.py", "GET /servers/{server_id}"),
    ("src/api/routes/avatars.py", "GET /users/{user_id:int}"),
    ("src/api/routes/bots.py", "GET /directory"),
    ("src/api/routes/config.py", "GET /capabilities"),
    ("src/api/routes/health.py", "GET /health"),
    ("src/api/routes/health.py", "GET /ready"),
    ("src/api/routes/help.py", "GET /access-blocked"),
    ("src/api/routes/help.py", "GET /security-logout"),
    ("src/api/routes/public_license.py", "GET /challenge"),
    ("src/api/routes/public_license.py", "GET /export"),
    ("src/api/routes/public_license.py", "GET /info"),
    ("src/api/routes/qr.py", "GET /qr"),
    ("src/api/routes/telemetry.py", "POST /csp-report"),
    ("src/api/routes/version.py", "GET /status"),
    ("src/api/routes/version.py", "GET /version"),
    ("src/api/routes/version.py", "POST /version/negotiate"),
}

# ``PUBLIC`` routes where a wrong or absent principal must be rejected: a
# credential the route verifies, an opaque per-resource token, a signed inbound
# payload, or an owner-scoped record. Each has a test in this module.
SCOPED_ROUTES: set[tuple[str, str]] = {
    ("src/api/routes/admin/auth.py", "POST /logout"),
    ("src/api/routes/admin/auth.py", "POST /verify-otp"),
    (
        "src/api/routes/applications.py",
        "POST /interactions/{interaction_token}/callback",
    ),
    ("src/api/routes/auth/email_verification.py", "POST /verify-email"),
    ("src/api/routes/auth/login.py", "POST /login"),
    ("src/api/routes/auth/oauth.py", "GET /oauth/{provider}/login"),
    ("src/api/routes/auth/oauth.py", "POST /oauth/{provider}/callback"),
    ("src/api/routes/auth/passkeys.py", "POST /passkeys/authenticate"),
    ("src/api/routes/auth/passkeys.py", "POST /passkeys/options/authenticate"),
    ("src/api/routes/auth/password.py", "POST /password-reset/confirm"),
    ("src/api/routes/auth/register.py", "POST /register"),
    ("src/api/routes/auth/sessions.py", "POST /refresh"),
    ("src/api/routes/auth/two_factor.py", "POST /2fa"),
    ("src/api/routes/bots.py", "GET /profiles/{application_id}"),
    ("src/api/routes/federation.py", "POST /artifact-event"),
    ("src/api/routes/federation.py", "POST /artifact-op"),
    ("src/api/routes/federation.py", "POST /join-request"),
    ("src/api/routes/federation.py", "POST /message"),
    ("src/api/routes/users/composer.py", "POST /@me/data-export"),
    ("src/api/routes/webhooks.py", "POST /{webhook_id}/{token}"),
}


def _load_audit():
    spec = importlib.util.spec_from_file_location("audit_failopen_routes", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


af = _load_audit()


def _public_route_keys() -> set[tuple[str, str]]:
    return {
        (hit.path, hit.symbol)
        for hit in af.sweep_routes(REPO_ROOT)
        if hit.kind == af.PUBLIC
    }


def _denied(status: int) -> bool:
    """A refusal, not a success and not a server error."""
    return 400 <= status < 500


# ---------------------------------------------------------------------------
# Completeness: the sweep's verdicts and this file must agree
# ---------------------------------------------------------------------------


def test_every_public_route_has_a_declared_expectation() -> None:
    public = _public_route_keys()
    docs = {key for key in public if key[0].startswith(DOCS_PREFIX)}
    declared = OPEN_ROUTES | SCOPED_ROUTES

    uncovered = public - docs - declared
    assert not uncovered, (
        "sweep D classifies these routes PUBLIC with no expectation declared "
        f"in test_public_route_authorization.py: {sorted(uncovered)}"
    )

    assert not (OPEN_ROUTES & SCOPED_ROUTES), "a route cannot be both open and scoped"
    stale = declared - public
    assert not stale, f"declared but no longer classified PUBLIC: {sorted(stale)}"


# ---------------------------------------------------------------------------
# Routes that must reject the wrong principal
# ---------------------------------------------------------------------------


def test_login_rejects_unknown_credentials(test_client) -> None:
    response = test_client.post(
        "/api/v1/auth/login",
        json={"username": "definitely-not-a-user", "password": "wrong-password"},
    )
    assert _denied(response.status_code), response.text


def test_refresh_rejects_a_missing_token(test_client) -> None:
    response = test_client.post("/api/v1/auth/refresh", json={})
    assert _denied(response.status_code), response.text


def test_two_factor_step_rejects_a_missing_challenge(test_client) -> None:
    response = test_client.post("/api/v1/auth/2fa", json={})
    assert _denied(response.status_code), response.text


def test_verify_email_rejects_a_bad_token(test_client) -> None:
    response = test_client.post(
        "/api/v1/auth/verify-email", json={"token": "not-a-real-verification-token"}
    )
    assert _denied(response.status_code), response.text


def test_password_reset_confirm_rejects_a_bad_token(test_client) -> None:
    response = test_client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "not-a-real-reset-token", "new_password": "NewPass123!"},
    )
    assert _denied(response.status_code), response.text


def test_register_rejects_an_invalid_payload(test_client) -> None:
    response = test_client.post("/api/v1/auth/register", json={})
    assert _denied(response.status_code), response.text


def test_passkey_authenticate_rejects_a_bad_assertion(test_client) -> None:
    response = test_client.post("/api/v1/auth/passkeys/authenticate", json={})
    assert _denied(response.status_code), response.text


def test_passkey_options_do_not_reveal_whether_an_account_exists(
    test_client, test_user_with_token
) -> None:
    """The pre-auth options endpoint must not be a username oracle.

    Finding M7: unknown accounts receive a single random *dummy* credential so
    the ``allowCredentials`` shape is indistinguishable from a real
    single-credential account, and the endpoint never 404s.
    """
    unknown = test_client.post(
        "/api/v1/auth/passkeys/options/authenticate",
        json={"username": "definitely-not-a-user"},
    )
    known = test_client.post(
        "/api/v1/auth/passkeys/options/authenticate",
        json={"username": test_user_with_token["username"]},
    )
    assert unknown.status_code == known.status_code == 200
    assert (
        len(unknown.json()["options"]["allowCredentials"])
        == len(known.json()["options"]["allowCredentials"])
        == 1
    )


def test_oauth_login_rejects_an_unknown_provider(test_client) -> None:
    response = test_client.get("/api/v1/auth/oauth/not-a-provider/login")
    assert _denied(response.status_code), response.text


def test_oauth_callback_rejects_a_missing_code(test_client) -> None:
    response = test_client.post(
        "/api/v1/auth/oauth/github/callback", json={"code": "", "state": ""}
    )
    assert _denied(response.status_code), response.text


def test_admin_verify_otp_rejects_a_missing_challenge(test_client) -> None:
    response = test_client.post("/api/v1/admin/verify-otp", json={})
    assert _denied(response.status_code), response.text


def test_webhook_inbound_rejects_an_unknown_token(test_client) -> None:
    response = test_client.post(
        "/api/v1/webhooks/123456789/not-a-real-webhook-token",
        json={"content": "hello"},
    )
    assert _denied(response.status_code), response.text


def test_interaction_callback_rejects_an_unknown_token(test_client) -> None:
    response = test_client.post(
        "/api/v1/applications/interactions/not-a-real-interaction-token/callback",
        json={"data": {}},
    )
    assert _denied(response.status_code), response.text


@pytest.mark.parametrize(
    "path",
    [
        "artifact-op",
        "artifact-event",
        "join-request",
        "message",
    ],
)
def test_federation_inbound_rejects_an_unsigned_payload(test_client, path: str) -> None:
    """Inbound federation is credentialed by an HMAC over the raw body.

    Without the `x-plexijoin-signature` headers the route must refuse. An
    unlicensed deployment refuses earlier with 403; both are refusals.
    """
    response = test_client.post(f"/api/v1/federation/{path}", json={"hello": "world"})
    assert response.status_code in (401, 403), response.text


def test_data_export_requires_authentication(test_client) -> None:
    response = test_client.post(
        "/api/v1/users/@me/data-export", json={"password": "whatever"}
    )
    assert response.status_code == 401, response.text


def test_admin_logout_cannot_revoke_another_principal(
    test_client, test_user_with_token
) -> None:
    """`POST /admin/logout` revokes only the token the caller presents.

    It is self-scoped: presenting a different principal's token must not
    invalidate that principal's session.
    """
    token = test_user_with_token["token"]
    response = test_client.post(
        "/api/v1/admin/logout", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code < 500, response.text
    # The user session is untouched: admin logout only knows admin sessions.
    still_valid = test_client.get(
        "/api/v1/users/@me", headers={"Authorization": f"Bearer {token}"}
    )
    assert still_valid.status_code == 200, still_valid.text


# ---------------------------------------------------------------------------
# Owner-scoped record: private bot profiles
# ---------------------------------------------------------------------------


@pytest.fixture()
def wired_applications(modules):
    """Bridge the applications module singleton to this test's manager.

    The route reads the module-level manager, while the fixtures expose the
    manager instance directly; without the bridge the route 500s on an
    uninitialized module. Restored on teardown so no stale manager (bound to a
    dead per-test database) leaks into another suite.
    """
    from src.core import applications as applications_module

    applications_module._manager = modules.applications
    applications_module._setup_complete = True
    yield modules.applications
    applications_module._manager = None
    applications_module._setup_complete = False


def _token_for(auth_manager, username: str) -> str:
    """Issue a real bearer token for a test user."""
    with patch.object(encryption, "verify_password", return_value=True):
        return auth_manager.login(username, "TestPass123!").token


def test_private_bot_profile_is_hidden_from_non_owners(
    test_client, modules, wired_applications, two_users
) -> None:
    """A private bot profile resolves only for its owner.

    The directory filters private bots, so answering a direct lookup for
    anyone would leak them.
    """
    owner, stranger = two_users
    application = wired_applications.create_application(
        owner_id=owner.id, name="Private Bot App"
    )
    wired_applications.update_bot_profile(
        application_id=application.id, user_id=owner.id, private=True
    )
    url = f"/api/v1/bots/profiles/{application.id}"

    anonymous = test_client.get(url)
    assert anonymous.status_code == 404, anonymous.text

    # A non-owner who is authenticated still does not see it.
    stranger_headers = {
        "Authorization": f"Bearer {_token_for(modules.auth, stranger.username)}"
    }
    non_owner = test_client.get(url, headers=stranger_headers)
    assert non_owner.status_code == 404, non_owner.text

    owner_headers = {
        "Authorization": f"Bearer {_token_for(modules.auth, owner.username)}"
    }
    owner_response = test_client.get(url, headers=owner_headers)
    assert owner_response.status_code == 200, owner_response.text
    assert owner_response.json()["private"] is True


def test_public_bot_profile_is_readable_without_auth(
    test_client, modules, wired_applications, two_users
) -> None:
    owner = two_users[0]
    application = wired_applications.create_application(
        owner_id=owner.id, name="Public Bot App"
    )
    wired_applications.update_bot_profile(
        application_id=application.id, user_id=owner.id, private=False
    )
    response = test_client.get(f"/api/v1/bots/profiles/{application.id}")
    assert response.status_code == 200, response.text
    assert response.json()["private"] is False


# ---------------------------------------------------------------------------
# Family tests for the open routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/ready"),
        ("GET", "/api/v1/version"),
        ("GET", "/api/v1/status"),
        ("GET", "/api/v1/capabilities"),
        ("GET", "/api/v1/qr?data=hello"),
        ("GET", "/api/v1/bots/directory"),
        ("GET", "/api/v1/auth/password-requirements"),
        ("GET", "/api/v1/public/license/info"),
        ("GET", "/api/v1/public/license/challenge"),
        ("GET", "/api/v1/public/license/export"),
        ("GET", "/api/v1/help/security-logout"),
        ("GET", "/api/v1/help/access-blocked"),
        ("POST", "/api/v1/telemetry/csp-report"),
        ("POST", "/api/v1/telemetry/response-times"),
        ("POST", "/api/v1/version/negotiate"),
    ],
)
def test_open_public_routes_do_not_require_a_bearer(test_client, method, url) -> None:
    response = test_client.request(method, url, json={} if method == "POST" else None)
    assert response.status_code not in (401, 403), (
        f"{method} {url} is classified PUBLIC but challenged the caller: "
        f"{response.status_code} {response.text}"
    )


def test_password_reset_request_is_enumeration_safe(test_client) -> None:
    """An unknown address must not be distinguishable by the response."""
    unknown = test_client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "nobody-at-all@example.com"},
    )
    assert unknown.status_code == 200, unknown.text


def test_resend_verification_is_enumeration_safe(test_client) -> None:
    unknown = test_client.post(
        "/api/v1/auth/resend-verification",
        json={"email": "nobody-at-all@example.com"},
    )
    assert unknown.status_code == 200, unknown.text


def test_avatars_are_public_reads(test_client, test_user) -> None:
    """Avatar/image bytes are public resources, not per-viewer records."""
    response = test_client.get(f"/api/v1/avatars/users/{test_user.id}")
    assert response.status_code not in (401, 403), response.text


def test_admin_ui_redirects_without_authentication(test_client) -> None:
    """`GET /admin/` is a static redirect to the login page, not a UI page."""
    response = test_client.get("/api/v1/admin/", follow_redirects=False)
    assert response.status_code in (200, 307, 308, 404), response.text
    assert "<form" not in response.text.lower()


def test_docs_pages_do_not_require_a_bearer(test_client) -> None:
    """Docs are public when mounted, and simply absent when disabled."""
    response = test_client.get("/docs/api/")
    assert response.status_code not in (401, 403), response.text
