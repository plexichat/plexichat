"""Route-level contract tests for 2FA rate limiting (Agent 50 F2).

Every ``/2fa`` route that verifies a credential (code, password, or both)
MUST carry the ``rate_limit`` decorator from
``src.core.ratelimit.decorators``. The decorator is applied conditionally
(``if rate_limit else lambda f: f``) so a future refactor that drops the
import guard could silently leave a brute-forceable route unthrottled.
These tests parse the route module's AST and pin the contract.
"""

import ast
from pathlib import Path


def _find_two_factor_routes() -> Path:
    """Locate the 2FA route module independent of the pytest CWD.

    Walks up from this file so the contract holds whether pytest runs
    from the project root or anywhere above it; falls back to the
    historical CWD-relative path so the existence test below still
    reports a clear assertion instead of an import-time error.
    """
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / "src" / "api" / "routes" / "auth" / "two_factor.py"
        if candidate.is_file():
            return candidate
    return Path("src/api/routes/auth/two_factor.py")


TWO_FACTOR_ROUTES = _find_two_factor_routes()

# Routes that verify a credential and therefore must be rate limited.
# /2fa/status is a read-only GET on an already-authenticated session and is
# deliberately excluded.
CREDENTIAL_ROUTES = {
    "complete_2fa",  # verifies TOTP/backup code
    "enable_2fa",  # verifies account password
    "confirm_2fa_setup",  # verifies TOTP code
    "disable_2fa",  # verifies password + code
}


def _route_functions():
    source = TWO_FACTOR_ROUTES.read_text()
    tree = ast.parse(source)
    functions = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node
    return functions


def _has_rate_limit_decorator(func) -> bool:
    for dec in func.decorator_list:
        src = ast.unparse(dec)
        if "rate_limit(" in src:
            return True
    return False


class TestTwoFactorRouteRateLimits:
    def test_two_factor_route_module_exists(self):
        assert TWO_FACTOR_ROUTES.exists(), (
            "two_factor.py moved; update TWO_FACTOR_ROUTES"
        )

    def test_credential_verifying_routes_are_rate_limited(self):
        functions = _route_functions()
        missing = [
            name
            for name in sorted(CREDENTIAL_ROUTES)
            if name not in functions or not _has_rate_limit_decorator(functions[name])
        ]
        assert not missing, (
            f"2FA routes missing rate_limit decorator (Agent 50 F2): {missing}. "
            "Every credential-verifying /2fa route must be throttled."
        )

    def test_rate_limit_import_guard_is_fail_closed(self):
        """The module must hard-fail startup when the rate limiter is
        unavailable unless an explicit opt-out is set (otherwise the
        conditional decorator silently becomes a no-op for ALL routes)."""
        source = TWO_FACTOR_ROUTES.read_text()
        assert "PLEXICHAT_ALLOW_2FA_NO_RATELIMIT" in source
        assert "refusing to start" in source
