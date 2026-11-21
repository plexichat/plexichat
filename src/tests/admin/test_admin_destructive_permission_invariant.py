"""Security invariant: destructive admin routes require elevated permission.

Destructive/irreversible admin operations (migration apply/rollback, forced
reindex, artifact deletion, ...) are gated by a stack of three checks:

* ``require_admin_permission(request, <action scope>)`` - authenticated admin
  with the action-specific permission,
* ``require_admin_permission(request, "maintenance")`` - the elevated
  maintenance scope (for the irreversible confirmation-gated operations), and
* ``require_approved_request(...)`` / ``require_destructive_confirmation(...)``
  - approval ticket and exact-phrase confirmation.

This suite reads the admin route SOURCE with ``ast`` (no imports, no DB, no
app) and proves:

* every approval-gated handler also calls the permission gate,
* every confirmation-gated handler additionally holds the elevated
  ``maintenance`` scope,
* no approval gate is attached to a read-only permission, and
* the shared helpers actually enforce 401/403 rather than failing open.

Deterministic by construction: it is a pure static check over checked-in
source files.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ADMIN_DIR = _REPO_ROOT / "src" / "api" / "routes" / "admin"
_UTILS = _ADMIN_DIR / "utils.py"

_ROUTE_DECORATORS = {"get", "post", "put", "patch", "delete"}
_APPROVAL_GATES = {"require_approved_request", "require_destructive_confirmation"}
_READ_ONLY_SCOPES = {"admin.read"}


def _own_body_calls(node: ast.AST) -> list[ast.Call]:
    """Calls on ``node``'s own execution path, skipping nested callables.

    Descend through statement-level nesting (``if``/``for``/``with``/``try``)
    but do not descend into a nested function or lambda: *defining* a helper
    that calls a gate does not run it. Crediting such a dead call as protection
    lets an unguarded destructive handler silence this invariant with evidence
    that never executes (the same vacuity fixed in the sibling route guards).
    """
    hits: list[ast.Call] = []
    stack = [node]
    while stack:
        parent = stack.pop()
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call):
                hits.append(child)
            stack.append(child)
    return hits


def _call_names(node: ast.AST) -> set[str]:
    return {ast.unparse(call.func) for call in _own_body_calls(node)}


def _permission_scopes(node: ast.AST) -> list[str]:
    scopes: list[str] = []
    for call in _own_body_calls(node):
        if ast.unparse(call.func) == "require_admin_permission":
            for arg in call.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    scopes.append(arg.value)
    return scopes


def _handlers() -> list[tuple[str, ast.AST]]:
    handlers: list[tuple[str, ast.AST]] = []
    for src in sorted(_ADMIN_DIR.rglob("*.py")):
        tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            is_route = any(
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr in _ROUTE_DECORATORS
                for dec in node.decorator_list
            )
            if is_route:
                handlers.append((f"{src.name}::{node.name}", node))
    return handlers


_ALL_HANDLERS = _handlers()
_DESTRUCTIVE = [
    (name, node) for name, node in _ALL_HANDLERS if _APPROVAL_GATES & _call_names(node)
]
_CONFIRMATION_GATED = [
    (name, node)
    for name, node in _DESTRUCTIVE
    if "require_destructive_confirmation" in _call_names(node)
]


@pytest.mark.security
@pytest.mark.unit
def test_destructive_handler_inventory_is_nonvacuous():
    assert len(_ALL_HANDLERS) >= 40
    assert len(_DESTRUCTIVE) >= 8, [n for n, _ in _DESTRUCTIVE]
    assert len(_CONFIRMATION_GATED) >= 5, [n for n, _ in _CONFIRMATION_GATED]
    # Irreversible operations we specifically intend to lock in.
    names = " ".join(n for n, _ in _DESTRUCTIVE)
    for token in ("rollback", "reindex", "apply_migration", "delete_artifact"):
        assert token in names, (token, names)


@pytest.mark.security
@pytest.mark.unit
def test_dead_nested_gate_does_not_protect_destructive_handler():
    """A gate only inside a never-called nested callable is not protection.

    Regression: the old detector used ``ast.walk(node)``, which descends into
    nested functions and lambdas. A destructive handler whose only
    ``require_admin_permission``/``maintenance`` call lived in an uninvoked
    helper passed both invariant checks with dead evidence.
    """
    source = """
@router.post("/dead")
async def dead(request: Request):
    require_approved_request(request, "ticket")
    def never_called():
        require_admin_permission(request, "maintenance")
    return delete_thing()

@router.post("/live")
async def live(request: Request):
    require_approved_request(request, "ticket")
    require_admin_permission(request, "maintenance")
    return delete_thing()
"""
    tree = ast.parse(source)
    fns = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    dead, live = fns["dead"], fns["live"]
    assert "require_admin_permission" not in _call_names(dead)
    assert "maintenance" not in _permission_scopes(dead)
    assert "require_admin_permission" in _call_names(live)
    assert "maintenance" in _permission_scopes(live)


@pytest.mark.security
@pytest.mark.unit
def test_every_destructive_handler_requires_admin_permission():
    """An approval/confirmation gate alone must never authorize a destructive op."""
    offenders = [
        n
        for n, node in _DESTRUCTIVE
        if "require_admin_permission" not in _call_names(node)
    ]
    assert offenders == [], offenders


@pytest.mark.security
@pytest.mark.unit
def test_confirmation_gated_ops_require_elevated_maintenance_scope():
    """Irreversible operations need the elevated 'maintenance' scope on top."""
    offenders = [
        n
        for n, node in _CONFIRMATION_GATED
        if "maintenance" not in _permission_scopes(node)
    ]
    assert offenders == [], offenders


@pytest.mark.security
@pytest.mark.unit
def test_approval_gates_never_sit_on_read_only_permissions():
    """A read-only admin cannot reach an approval/confirmation-gated mutation."""
    offenders: list[str] = []
    for name, node in _DESTRUCTIVE:
        scopes = _permission_scopes(node)
        assert scopes, name
        elevated = [
            s for s in scopes if s not in _READ_ONLY_SCOPES and not s.endswith(".read")
        ]
        if not elevated:
            offenders.append(f"{name}: {scopes}")
    assert offenders == [], offenders


def _utils_function_source(name: str) -> str:
    source = _UTILS.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_UTILS))
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            text = ast.get_source_segment(source, node)
            assert text is not None
            return text
    raise AssertionError(f"helper {name} not found")


@pytest.mark.security
@pytest.mark.unit
def test_permission_helpers_fail_closed():
    """The shared gates deny (401/403) rather than fail open."""
    require_perm = _utils_function_source("require_admin_permission")
    assert "get_admin_from_token(request)" in require_perm
    assert "check_admin_permission" in require_perm
    assert "status_code=403" in require_perm

    from_token = _utils_function_source("get_admin_from_token")
    assert "admin.validate_session(token)" in from_token
    assert "status_code=401" in from_token

    confirmation = _utils_function_source("require_destructive_confirmation")
    assert "confirmation" in confirmation
    assert "status_code=400" in confirmation
