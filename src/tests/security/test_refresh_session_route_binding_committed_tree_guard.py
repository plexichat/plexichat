"""Committed-tree guard: the refresh ROUTE must thread trusted IP/UA into rotation.

The N644 token-binding fix re-checks the *presented* refresh credential's IP
and User-Agent against the session's stored blind indexes inside
``LifecycleMixin.refresh_session``. That manager-side gate is already pinned in
the shared committed-tree marker guard (``ded43f8e-refresh-session-ip-ua-
context-binding``). The manager cannot enforce anything, though, if the HTTP
route stops passing the request context: a stale whole-file stage of
``src/api/routes/auth/sessions.py`` restoring ``auth.refresh_session(token)``
silently drops the binding gate while leaving the manager pin green.

This standalone guard pins the ROUTE-level half of the fix. It reads
``sessions.py`` straight out of ``HEAD`` with ``git show`` -- never the
working tree -- so a stale staged copy cannot hide a committed revert. It is
deliberately standalone: the shared committed-tree marker registry is
concurrently edited by other agents, and adding to it would be lost in a
whole-file stage.

Two things are pinned structurally (AST, not just literals):

1. the call is ``auth.refresh_session(token, ip_address, user_agent)`` -- exactly
   three positional name arguments, so the single-argument form is rejected;
2. those two names come from the trusted resolvers in the route itself:
   ``ip_address = get_client_ip(request)`` (the proxy-aware resolver from
   ``src.utils.net``) and ``user_agent = request.headers.get("User-Agent")``.
   Passing a forged header, a literal or a missing value instead is rejected.

Non-vacuity: each pin has a syntactically valid synthetic revert that the same
evaluator must reject, plus a fully gutted blob.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET = "src/api/routes/auth/sessions.py"

FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    """Resolve HEAD once per run; every blob read is pinned to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _committed(commit: str, path: str) -> str:
    proc = _git("show", f"{commit}:{path}")
    assert proc.returncode == 0, (
        f"cannot read committed {path!r} from {commit}: {proc.stderr.strip()}"
    )
    return proc.stdout


def _find_route(tree: ast.AST) -> ast.AST | None:
    return next(
        (
            node
            for node in tree.body
            if isinstance(node, FUNC_TYPES) and node.name == "refresh_session"
        ),
        None,
    )


def _assigned_value(route: ast.AST, name: str) -> ast.AST | None:
    """Value expression of the (unique) assignment to ``name`` in the route."""
    found: list[ast.AST] = []
    for node in ast.walk(route):
        if isinstance(node, ast.Assign) and any(
            isinstance(tgt, ast.Name) and tgt.id == name for tgt in node.targets
        ):
            found.append(node.value)
    if len(found) != 1:
        return None
    return found[0]


def _is_trusted_ip_resolver(expr: ast.AST | None) -> bool:
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id == "get_client_ip"
        and len(expr.args) == 1
        and isinstance(expr.args[0], ast.Name)
        and expr.args[0].id == "request"
    )


def _is_trusted_ua_resolver(expr: ast.AST | None) -> bool:
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr == "get"
        and isinstance(expr.func.value, ast.Attribute)
        and expr.func.value.attr == "headers"
        and isinstance(expr.func.value.value, ast.Name)
        and expr.func.value.value.id == "request"
        and len(expr.args) == 1
        and isinstance(expr.args[0], ast.Constant)
        and expr.args[0].value == "User-Agent"
    )


def _refresh_calls(route: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(route)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "refresh_session"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "auth"
    ]


def _evaluate(source: str) -> tuple[bool, str]:
    """Return ``(ok, detail)`` for the committed route source."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"committed sessions.py does not parse: {exc}"

    has_resolver_import = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "src.utils.net"
        and any(alias.name == "get_client_ip" for alias in node.names)
        for node in tree.body
    )
    if not has_resolver_import:
        return False, "trusted get_client_ip import missing from committed blob"

    route = _find_route(tree)
    if route is None:
        return False, "refresh_session route missing from committed blob"

    if not _is_trusted_ip_resolver(_assigned_value(route, "ip_address")):
        return False, "ip_address is not resolved via get_client_ip(request)"
    if not _is_trusted_ua_resolver(_assigned_value(route, "user_agent")):
        return False, 'user_agent is not resolved via request.headers.get("User-Agent")'

    calls = _refresh_calls(route)
    if len(calls) != 1:
        return False, (
            f"expected exactly one auth.refresh_session call in the route, "
            f"found {len(calls)}"
        )
    args = calls[0].args
    expected = ("token", "ip_address", "user_agent")
    if len(args) != len(expected) or not all(
        isinstance(arg, ast.Name) and arg.id == name
        for arg, name in zip(args, expected)
    ):
        got = [
            arg.id if isinstance(arg, ast.Name) else type(arg).__name__ for arg in args
        ]
        return False, (
            f"route-level auth.refresh_session must pass {expected}, got {got}"
        )

    return True, "route threads trusted get_client_ip/User-Agent context into rotation"


def test_route_refresh_binding_pinned_at_head(commit_sha: str) -> None:
    """HEAD's refresh route passes trusted IP/UA context into rotation."""
    ok, detail = _evaluate(_committed(commit_sha, _TARGET))
    assert ok, detail


_BOUND_CALL = "new_token = auth.refresh_session(token, ip_address, user_agent)"
_IP_RESOLVER = "ip_address = get_client_ip(request)"
_UA_RESOLVER = 'user_agent = request.headers.get("User-Agent")'


# Each fixture is a syntactically valid revert of one part of the route fix;
# the same evaluator used for the live HEAD check must reject all of them.
_REVERTS: tuple[tuple[str, str, str], ...] = (
    (
        "unbound-single-arg-call",
        _BOUND_CALL,
        "new_token = auth.refresh_session(token)",
    ),
    (
        "literal-context-call",
        _BOUND_CALL,
        'new_token = auth.refresh_session(token, "1.2.3.4", "attacker")',
    ),
    (
        "forged-ip-header",
        _IP_RESOLVER,
        'ip_address = request.headers.get("X-Forwarded-For")',
    ),
    (
        "forged-ua-literal",
        _UA_RESOLVER,
        'user_agent = "attacker"',
    ),
)


@pytest.mark.parametrize("name,old,new", _REVERTS, ids=[r[0] for r in _REVERTS])
def test_each_route_pin_is_falsifiable(
    commit_sha: str, name: str, old: str, new: str
) -> None:
    """A synthetic revert of any pinned route component is caught."""
    source = _committed(commit_sha, _TARGET)
    assert old in source, f"revert fixture {name!r} no longer matches committed source"
    ok, detail = _evaluate(source.replace(old, new, 1))
    assert not ok, f"synthetic route revert {name!r} was not caught: {detail}"


def test_gutted_blob_is_rejected() -> None:
    """A blob with no refresh route at all cannot pass."""
    ok, detail = _evaluate("# gutted sessions route\n")
    assert not ok
    assert "refresh_session" in detail or "import" in detail
