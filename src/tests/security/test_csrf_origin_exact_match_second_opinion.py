"""Second-opinion regression guard: CSRF Origin/Referer matching is EXACT.

``AuthenticationMiddleware._origin_allowed`` is the last line of defence for
cookie-authenticated state changes when the browser does not give a trustworthy
``Sec-Fetch-Site``. It must compare the presented Origin/Referer origin against
the configured CORS allowlist by exact equality -- never by substring, prefix,
suffix, suffix-domain, case-fold, or port-insensitive match. A lax match would
let one look-alike origin (e.g. ``https://app.plexichat.com.evil.com``) claim
the authority of a genuinely allowed one and rotate a victim's session.

This module pins that contract behaviourally: every look-alike origin is
REJECTED, while the genuinely exact origin (and only its trailing-slash
normalisation) is ALLOWED. It is intentionally a new, standalone file so it
cannot be silently reverted by staging a stale copy of a contested test file.

The marker-header half of the defence (``X-Plexichat-Request: 1``) is a custom
header: a cross-origin page cannot attach it without a successful CORS
preflight, which only an allowlisted origin can pass. That is a browser
guarantee and is documented here for rationale; the server-side half pinned
below is the exact-match check.
"""

from __future__ import annotations

import pytest

from src.api.middleware.authentication import AuthenticationMiddleware

pytestmark = [pytest.mark.security, pytest.mark.unit]

_ALLOWED_ORIGIN = "https://app.plexichat.com"


class _Req:
    """Minimal header mapping for the static helper (no ASGI scope needed)."""

    def __init__(self, headers: dict[str, str]):
        self.headers = headers


def _origin_allowed(headers: dict[str, str]) -> bool:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "utils.config.get",
            lambda *a, **k: {"cors_origins": [_ALLOWED_ORIGIN]},
        )
        return AuthenticationMiddleware._origin_allowed(_Req(headers))


# Each value is a distinct way an attacker might try to make a foreign origin
# compare equal to an allowlisted one. None may be accepted.
FORGED_ORIGINS = [
    pytest.param("https://app.plexichat.com.evil.com", id="suffix-domain"),
    pytest.param("https://evilapp.plexichat.com", id="prefix-substring"),
    pytest.param("https://evil-app.plexichat.com", id="prefix-dash"),
    pytest.param("https://sub.app.plexichat.com", id="subdomain"),
    pytest.param("https://app.plexichat.com.", id="trailing-dot"),
    pytest.param("https://app.plexichat.com:443", id="explicit-default-port"),
    pytest.param("https://app.plexichat.com:8443", id="nondefault-port"),
    pytest.param("https://APP.PLEXICHAT.COM", id="case-fold"),
    pytest.param("https://app.plexichat.com@evil.com", id="userinfo-host-evil"),
    pytest.param("https://user@app.plexichat.com", id="userinfo-allowed-looking"),
    pytest.param("http://app.plexichat.com", id="scheme-downgrade"),
    pytest.param("https://app.plexichat.com/evil", id="path-suffix"),
    pytest.param("https://app.plexichat.com?next=evil", id="query-suffix"),
    pytest.param("null", id="literal-null"),
]


@pytest.mark.parametrize("origin", FORGED_ORIGINS)
def test_forged_origin_never_matches_exact_allowlist(origin: str) -> None:
    assert _origin_allowed({"Origin": origin}) is False


# A Referer is a full URL; only scheme+netloc are eligible for matching, and
# that origin must still be an exact member of the allowlist.
FORGED_REFERERS = [
    pytest.param("https://app.plexichat.com.evil.com/channel/1", id="suffix-domain"),
    pytest.param("https://sub.app.plexichat.com/channel/1", id="subdomain"),
    pytest.param("https://app.plexichat.com./channel/1", id="trailing-dot"),
    pytest.param("https://app.plexichat.com:8443/channel/1", id="nondefault-port"),
    pytest.param("https://app.plexichat.com@evil.com/channel/1", id="userinfo-host-evil"),
    pytest.param("https://APP.PLEXICHAT.COM/channel/1", id="case-fold"),
]


@pytest.mark.parametrize("referer", FORGED_REFERERS)
def test_forged_referer_never_matches_exact_allowlist(referer: str) -> None:
    assert _origin_allowed({"Referer": referer}) is False


def test_genuine_exact_origin_is_allowed() -> None:
    """Positive control: the guard is not simply rejecting everything."""
    assert _origin_allowed({"Origin": _ALLOWED_ORIGIN}) is True


def test_trailing_slash_is_the_only_normalisation() -> None:
    """Trailing slashes do not change an origin, so they are normalised away.

    Nothing else (dot, port, case, userinfo, subdomain) is, as pinned above.
    """
    assert _origin_allowed({"Origin": _ALLOWED_ORIGIN + "/"}) is True
    assert _origin_allowed({"Referer": _ALLOWED_ORIGIN + "/channel/1"}) is True


def test_either_matching_header_suffices() -> None:
    """Documented contract: one exact-matching candidate is enough."""
    assert (
        _origin_allowed(
            {
                "Origin": "https://evil.example",
                "Referer": _ALLOWED_ORIGIN + "/channel/1",
            }
        )
        is True
    )
    assert (
        _origin_allowed(
            {
                "Origin": _ALLOWED_ORIGIN,
                "Referer": "https://evil.example/channel/1",
            }
        )
        is True
    )
    assert (
        _origin_allowed(
            {
                "Origin": "https://evil.example",
                "Referer": "https://evil.example/channel/1",
            }
        )
        is False
    )


def test_cookie_state_change_route_binds_exact_origin() -> None:
    """A forged look-alike origin cannot pass the refresh route's CSRF helper.

    The marker header is static/guessable, so the exact-match Origin bind is
    what stops a same-site sibling (or a permissive-CORS cross-site page that
    acquired the marker) from rotating a victim's session.
    """
    from src.api.routes.auth.sessions import _cookie_csrf_allowed

    def allowed(headers: dict[str, str]) -> bool:
        req = type("_Req", (), {"headers": headers})()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "utils.config.get",
                lambda key, *a, **k: (
                    {"cors_origins": [_ALLOWED_ORIGIN]} if key == "api" else {}
                ),
            )
            return _cookie_csrf_allowed(req)

    # Cross-site is hard-denied regardless of Origin.
    assert allowed(
        {
            "Sec-Fetch-Site": "cross-site",
            "X-Plexichat-Request": "1",
            "Origin": _ALLOWED_ORIGIN,
        }
    ) is False
    # same-site is not a CSRF boundary: only an EXACT allowlisted Origin passes.
    assert (
        allowed(
            {
                "Sec-Fetch-Site": "same-site",
                "X-Plexichat-Request": "1",
                "Origin": "https://app.plexichat.com.evil.com",
            }
        )
        is False
    )
    assert (
        allowed(
            {
                "Sec-Fetch-Site": "same-site",
                "X-Plexichat-Request": "1",
                "Origin": _ALLOWED_ORIGIN,
            }
        )
        is True
    )
