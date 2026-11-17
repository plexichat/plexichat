"""Security invariant: bad auth credentials deny as 401, never 500.

A malformed or oversized token must be rejected on the authentication *denial*
path.  Historically two bugs turned that denial into a 500:

* an oversized/non-string credential reached the cached verify-token key
  builder and raised deep inside ``hashlib``/``str`` handling, and
* a permissive ``str.split`` + ``int`` token parser raised ``ValueError`` on
  signs / non-canonical ids / garbage expiries.

Both are AuthError (``TokenInvalidError``) denials now, which the auth
middleware maps to ``scope['state']['auth_error']`` and ``get_current_user``
turns into HTTP 401.  Only a genuine system error maps to ``system_error`` ->
HTTP 500.

This suite locks that in at three levels:

1. the token primitives deny cleanly (never raise a generic exception),
2. the middleware source still implements the 401-vs-500 mapping and the
   restrictive Bearer grammar, and
3. EVERY auth route that requires ``get_current_user`` returns 401 (never 500)
   for a malformed header and for an oversized Bearer token.

The route inventory is derived from the source with the ``ast`` module, so it
stays in sync with the router.  Everything here is deterministic: no clock, no
randomness, no external services.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from src.core.auth import tokens
from src.core.auth.exceptions import AuthError, TokenInvalidError

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AUTH_DIR = _REPO_ROOT / "src" / "api" / "routes" / "auth"
_MIDDLEWARE = _REPO_ROOT / "src" / "api" / "middleware" / "authentication.py"

_METHODS = {"get", "post", "put", "patch", "delete"}


def _protected_auth_routes() -> tuple[tuple[str, str], ...]:
    """(method, path) of every auth route depending on get_current_user."""
    found: list[tuple[str, str]] = []
    for src in sorted(_AUTH_DIR.glob("*.py")):
        if src.name == "__init__.py":
            continue
        tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            defaults = list(node.args.defaults) + [
                d for d in node.args.kw_defaults if d is not None
            ]
            requires_user = any(
                isinstance(d, ast.Call)
                and getattr(d.func, "id", None) == "Depends"
                and d.args
                and getattr(d.args[0], "id", None) == "get_current_user"
                for default in defaults
                for d in ast.walk(default)
            )
            if not requires_user:
                continue
            for dec in node.decorator_list:
                if not (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in _METHODS
                    and dec.args
                    and isinstance(dec.args[0], ast.Constant)
                ):
                    continue
                found.append((dec.func.attr.upper(), "/api/v1/auth" + dec.args[0].value))
    return tuple(sorted(set(found)))


_PROTECTED_AUTH_ROUTES = _protected_auth_routes()

# Two independent denial shapes that used to cause a 500:
#  - a header with characters outside the Bearer grammar (regex rejects it), and
#  - an oversized all-base64url Bearer token (parser/hashing rejects it).
_BAD_HEADERS = {
    "malformed-chars": "Bearer @@@not-a-token@@@",
    "oversized": "Bearer " + ("a" * (tokens.MAX_TOKEN_LENGTH + 64)),
}

# Fixed, valid base64url secret used in parametrized inputs.  Parametrization
# must be identical on every xdist worker, so no randomness here.
_SECRET = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-"


@pytest.mark.security
@pytest.mark.unit
def test_protected_auth_route_inventory_is_nonvacuous():
    """Guard against a source-layout change silently emptying the parametrization."""
    assert len(_PROTECTED_AUTH_ROUTES) >= 15, _PROTECTED_AUTH_ROUTES
    paths = {path for _, path in _PROTECTED_AUTH_ROUTES}
    for expected in (
        "/api/v1/auth/logout",
        "/api/v1/auth/ws-ticket",
        "/api/v1/auth/sessions",
        "/api/v1/auth/devices",
        "/api/v1/auth/passkeys",
        "/api/v1/auth/2fa/status",
    ):
        assert expected in paths


@pytest.mark.security
@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        None,
        b"123456789.abcdefghijklmnopqrstuvwxyzabcdefg",
        12345,
        "",
        "x" * (tokens.MAX_TOKEN_LENGTH + 1),
        "1.short",
        "1." + "!" * tokens.MIN_TOKEN_SECRET_LENGTH,
        "0." + _SECRET,
        "-1." + _SECRET,
        "01." + _SECRET,
        "1." + _SECRET + ".extra",
    ],
)
def test_token_primitives_deny_malformed_input_as_autherror(auth_manager, bad):
    """No malformed credential may escape as a generic exception (-> 500)."""
    assert tokens.parse_token(bad) is None
    with pytest.raises(TokenInvalidError) as exc:
        auth_manager.verify_token(bad)
    assert isinstance(exc.value, AuthError)  # the type the middleware catches


@pytest.mark.security
@pytest.mark.unit
@pytest.mark.parametrize("bad", [None, b"token", 12345, "", "a" * (tokens.MAX_TOKEN_LENGTH + 1)])
def test_malformed_context_is_flagged_before_hashing(bad):
    assert tokens.is_malformed_token_input(bad) is True


@pytest.mark.security
@pytest.mark.unit
def test_middleware_keeps_401_vs_500_mapping():
    """Static guard: the denial mapping and restrictive Bearer grammar survive."""
    source = _MIDDLEWARE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_MIDDLEWARE))

    def segment(name: str) -> str:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                text = ast.get_source_segment(source, node)
                assert text is not None
                return text
        raise AssertionError(f"function {name} not found")

    extract = segment("_extract_token")
    assert "fullmatch" in extract
    assert "[A-Za-z0-9._-]" in extract

    current_user = segment("get_current_user")
    assert "status_code=401" in current_user
    assert "status_code=500" in current_user
    assert "system_error" in current_user

    process = segment("_process")
    assert "isinstance(e, AuthError)" in process
    assert '"auth_error"' in process or "'auth_error'" in process
    assert '"system_error"' in process or "'system_error'" in process
    assert "status_code=401" in process
    assert "status_code=500" in process


@pytest.mark.security
@pytest.mark.api
@pytest.mark.parametrize("bad_name,bad_header", sorted(_BAD_HEADERS.items()))
@pytest.mark.parametrize("method,path", _PROTECTED_AUTH_ROUTES)
def test_every_protected_auth_route_returns_401_not_500(test_client, method, path, bad_name, bad_header):
    """A bad credential on any protected auth route is a 401, never a 500."""
    route = re.sub(r"\{[^}]+\}", "1", path)
    response = getattr(test_client, method.lower())(route, headers={"Authorization": bad_header})
    assert response.status_code == 401, (
        f"{method} {route} with {bad_name} returned {response.status_code}: {response.text}"
    )
    assert response.status_code != 500
