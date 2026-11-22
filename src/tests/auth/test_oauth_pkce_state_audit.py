"""Runtime verification of the OAuth/OIDC PKCE invariants in the login flow.

This file is deliberately non-vacuous: every assertion is driven through the
real route / real crypto / real database paths. Mocks are limited to the IdP
HTTP boundary and to the global config/state-manager wiring; the security
decisions themselves are executed, not stubbed.

Properties verified:
  * only S256 is accepted at init ("plain" is rejected, omission still forwards
    S256 to the IdP);
  * the PKCE verifier is S256-hashed and the resulting challenge is compared
    with ``secrets.compare_digest`` (constant time), never with ``==``;
  * OAuth state is single-use even when two independent DB connections race,
    and it is bound to the initiating browser via the HttpOnly state cookie;
  * consuming the state (and thus the authorization code bound to it) prevents
    a second callback from reaching the token exchange again.
"""

import base64
import hashlib
import secrets as _secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.auth import oauth as oauth_mod
from src.api.schemas.auth import OAuthCallbackRequest
from src.core.auth.oauth import OAuthStateManager, generate_pkce_pair, verify_pkce
from src.core.auth.oauth import pkce as pkce_mod

REDIRECT = "https://app.example.com/oauth/callback"
VERIFIER = "v" * 43
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode("ascii")).digest())
    .decode("ascii")
    .rstrip("=")
)


def _oauth_config(*, with_secret: bool = False, pkce_enabled: bool = True) -> dict:
    provider = {"client_id": "cid", "allowed_redirect_uris": [REDIRECT]}
    if with_secret:
        provider["client_secret"] = "secret"
    return {
        "github": provider,
        "pkce_enabled": pkce_enabled,
    }


def _config_patch(cfg: dict):
    return patch.object(
        oauth_mod.config_util,
        "get",
        side_effect=lambda key, default=None: cfg if key == "oauth" else default,
    )


def _state_stub(**overrides):
    values = {
        "state_token": "raw-state-token",
        "nonce_value": None,
        "nonce": None,
        "pkce_challenge": None,
        "cookie_issued": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# ---------------------------------------------------------------------------
# 1. S256 only at init (README.md's citation points at this enforcing check)
# ---------------------------------------------------------------------------
@pytest.mark.auth
@pytest.mark.asyncio
async def test_init_rejects_plain_and_only_forwards_s256():
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: (
        _oauth_config() if key == "oauth" else default
    )
    with (
        patch.object(oauth_mod, "config_util", cfg),
        patch.object(oauth_mod, "_oauth_get_client_ip", return_value="1.2.3.4"),
        patch.object(
            oauth_mod, "create_oauth_state", return_value=_state_stub()
        ) as create,
    ):
        # "plain" must be rejected before any state is minted.
        with pytest.raises(HTTPException) as exc:
            await oauth_mod.oauth_login_init(
                MagicMock(),
                MagicMock(),
                "github",
                REDIRECT,
                code_challenge=CHALLENGE,
                code_challenge_method="plain",
            )
        assert exc.value.status_code == 400
        assert "S256" in str(exc.value.detail)
        assert create.call_count == 0

        # An explicit S256 challenge is stored on the state and forwarded.
        resp = await oauth_mod.oauth_login_init(
            MagicMock(),
            MagicMock(),
            "github",
            REDIRECT,
            code_challenge=CHALLENGE,
            code_challenge_method="S256",
        )
        assert create.call_args.kwargs["pkce_challenge"] == CHALLENGE
        assert "code_challenge_method=S256" in resp.auth_url
        assert "code_challenge_method=plain" not in resp.auth_url

        # Omitting the method cannot downgrade to plain: S256 is still sent.
        create.reset_mock()
        resp2 = await oauth_mod.oauth_login_init(
            MagicMock(),
            MagicMock(),
            "github",
            REDIRECT,
            code_challenge=CHALLENGE,
        )
        assert create.call_args.kwargs["pkce_challenge"] == CHALLENGE
        assert "code_challenge_method=S256" in resp2.auth_url
        assert "code_challenge_method=plain" not in resp2.auth_url


# ---------------------------------------------------------------------------
# 2. Constant-time comparison of the hashed verifier
# ---------------------------------------------------------------------------
@pytest.mark.auth
def test_pkce_hashes_verifier_then_uses_compare_digest():
    pair = generate_pkce_pair()
    real_compare = _secrets.compare_digest
    calls = []

    def spy(a, b):
        calls.append((a, b))
        return real_compare(a, b)

    with patch.object(pkce_mod.secrets, "compare_digest", side_effect=spy):
        assert verify_pkce(pair.code_verifier, pair.code_challenge) is True
    assert len(calls) == 1, "comparison bypassed secrets.compare_digest"
    compared_a, compared_b = calls[0]
    # The compared values are challenges (hashes), not the raw verifier.
    assert compared_a == pair.code_challenge
    assert compared_b == pair.code_challenge
    assert compared_a != pair.code_verifier

    # A wrong verifier still flows through the same constant-time primitive.
    wrong = "x" * 43
    calls.clear()
    with patch.object(pkce_mod.secrets, "compare_digest", side_effect=spy):
        assert verify_pkce(wrong, pair.code_challenge) is False
    assert len(calls) == 1
    assert calls[0][0] != pair.code_challenge


# ---------------------------------------------------------------------------
# 3. State single-use across independent DB connections (simulated workers)
# ---------------------------------------------------------------------------
@pytest.mark.auth
def test_state_single_use_across_independent_connections(db):
    manager = OAuthStateManager(db, {"cleanup_on_verify": False})
    state = manager.create_state(
        provider="github",
        redirect_uri=REDIRECT,
        pkce_challenge=CHALLENGE,
        ip_address="203.0.113.7",
        cookie_issued=True,
    )
    assert state is not None and state.state_token

    # Force the real TOCTOU window: both workers must read ``used = 0`` before
    # either issues the conditional UPDATE. Without this barrier a scheduler
    # that finishes one whole verify_state before starting the other would let
    # the cheap ``row['used']`` fast-fail mask a broken atomic claim, making
    # the test vacuous.
    read_barrier = threading.Barrier(2)
    real_fetch_one = manager._db.fetch_one

    def gated_fetch_one(query, params=None):
        result = real_fetch_one(query, params)
        if "FROM auth_oauth_states WHERE state_hash" in query:
            read_barrier.wait(timeout=10)
        return result

    def worker():
        # Separate thread -> separate thread-local DB connection, just like a
        # second worker process racing the same conditional UPDATE.
        return manager.verify_state(state.state_token, "github", REDIRECT)[0]

    with patch.object(manager._db, "fetch_one", side_effect=gated_fetch_one):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]

    assert sorted(results) == [False, True], f"both racers won: {results}"


# ---------------------------------------------------------------------------
# 4. State bound to the initiating browser via the HttpOnly state cookie
# ---------------------------------------------------------------------------
def _token_ctx():
    ctx = MagicMock()
    client = MagicMock()
    client.post = AsyncMock(side_effect=RuntimeError(" reached token exchange "))
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


@pytest.mark.auth
@pytest.mark.asyncio
async def test_callback_requires_matching_state_cookie():
    state = _state_stub(cookie_issued=True)
    request = SimpleNamespace(
        cookies={oauth_mod.OAUTH_STATE_COOKIE: "wrong"}, client=("1.2.3.4", 1)
    )
    body = OAuthCallbackRequest(code="c", state="st", redirect_uri=REDIRECT)
    with (
        _config_patch(_oauth_config(with_secret=True)),
        patch.object(oauth_mod.api, "get_auth", return_value=MagicMock()),
        patch.object(oauth_mod, "_redirect_uri_is_allowed", return_value=True),
        patch.object(oauth_mod, "verify_oauth_state", return_value=(True, state, None)),
        patch.object(oauth_mod, "_oauth_get_client_ip", return_value="1.2.3.4"),
        patch.object(oauth_mod.httpx, "AsyncClient", return_value=_token_ctx()),
    ):
        with pytest.raises(HTTPException) as exc:
            await oauth_mod.oauth_callback(request, MagicMock(), "github", body)
    assert exc.value.status_code == 400
    assert "Security verification failed" in str(exc.value.detail)

    # The matching cookie passes the gate and reaches the (deliberately failing)
    # token exchange, proving the 400 above was caused by the cookie check.
    request.cookies = {oauth_mod.OAUTH_STATE_COOKIE: "st"}
    with (
        _config_patch(_oauth_config(with_secret=True)),
        patch.object(oauth_mod.api, "get_auth", return_value=MagicMock()),
        patch.object(oauth_mod, "_redirect_uri_is_allowed", return_value=True),
        patch.object(oauth_mod, "verify_oauth_state", return_value=(True, state, None)),
        patch.object(oauth_mod, "_oauth_get_client_ip", return_value="1.2.3.4"),
        patch.object(oauth_mod.httpx, "AsyncClient", return_value=_token_ctx()),
    ):
        with pytest.raises(HTTPException) as exc2:
            await oauth_mod.oauth_callback(request, MagicMock(), "github", body)
    assert exc2.value.status_code == 401
    assert "Token exchange failed" in str(exc2.value.detail)


# ---------------------------------------------------------------------------
# 5. Replaying the callback / authorization code after state consumption
# ---------------------------------------------------------------------------
@pytest.mark.auth
@pytest.mark.asyncio
async def test_consumed_state_blocks_second_code_exchange(db):
    manager = OAuthStateManager(db, {"cleanup_on_verify": False})
    state = manager.create_state(
        provider="github",
        redirect_uri=REDIRECT,
        pkce_challenge=CHALLENGE,
        cookie_issued=True,
    )
    assert state is not None and state.state_token
    body = OAuthCallbackRequest(
        code="auth-code",
        state=state.state_token,
        redirect_uri=REDIRECT,
        code_verifier=VERIFIER,
    )
    request = SimpleNamespace(
        cookies={oauth_mod.OAUTH_STATE_COOKIE: state.state_token},
        client=("1.2.3.4", 1),
    )
    patches = (
        _config_patch(_oauth_config(with_secret=True)),
        patch.object(oauth_mod.api, "get_auth", return_value=MagicMock()),
        patch.object(oauth_mod, "_redirect_uri_is_allowed", return_value=True),
        # Real state manager: the first call actually consumes the row.
        patch.object(oauth_mod, "verify_oauth_state", side_effect=manager.verify_state),
        patch.object(oauth_mod, "_oauth_get_client_ip", return_value="1.2.3.4"),
        patch.object(oauth_mod.httpx, "AsyncClient", return_value=_token_ctx()),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        with pytest.raises(HTTPException) as first:
            await oauth_mod.oauth_callback(request, MagicMock(), "github", body)
        assert first.value.status_code == 401  # reached token exchange

        with pytest.raises(HTTPException) as replay:
            await oauth_mod.oauth_callback(request, MagicMock(), "github", body)
        assert replay.value.status_code == 400
        assert "Security verification failed" in str(replay.value.detail)
