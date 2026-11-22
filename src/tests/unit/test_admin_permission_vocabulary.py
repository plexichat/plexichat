"""Closed permission vocabulary for delegated admin roles.

Wildcard scopes (``*`` and ``scope.*``) and unknown/unconventional keys must
never be accepted from a role create/update payload.  The vocabulary lives in
``src.core.admin.permissions`` and both mutating routes funnel through it.

The second half of the contract is the inverse direction: every scope that an
admin route actually checks must be declared in ``PERMISSION_SCOPES`` (and thus
land in ``DELEGABLE_PERMISSIONS``).  Otherwise the gate is unreachable for a
non-super-admin -- ``validate_delegable_permissions`` rejects the scope from
every role payload, so only a ``*`` / ``admin.*`` holder could ever pass it.
The committed-tree sweep below locks that direction down and is non-vacuous.
"""

import ast
import asyncio
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.schemas.common import SnowflakeIDParam
from src.core.admin.permissions import (
    DELEGABLE_PERMISSIONS,
    PERMISSION_SCOPES,
    validate_delegable_permissions,
)

SRC = Path(__file__).resolve().parents[2]
REPO_ROOT = SRC.parent

_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
_GATE_FUNCS = frozenset({"require_admin_permission", "check_admin_permission"})


def _request(path: str = "/roles", method: str = "POST") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


class TestDelegableVocabulary:
    def test_wildcards_are_excluded_from_delegable_set(self):
        assert "*" not in DELEGABLE_PERMISSIONS
        assert not any(key.endswith(".*") for key in DELEGABLE_PERMISSIONS)

    def test_vocabulary_is_derived_from_declared_scopes(self):
        assert DELEGABLE_PERMISSIONS == frozenset(
            key for key in PERMISSION_SCOPES if key != "*" and not key.endswith(".*")
        )
        assert "users.read" in DELEGABLE_PERMISSIONS
        assert "admin.roles" in DELEGABLE_PERMISSIONS

    @pytest.mark.parametrize(
        "payload",
        [
            {"*": True},
            {"admin.*": True},
            {"users.*": True},
            {"totally.made.up": True},
            {"users.read": "yes"},
            {"users.read": 1},
            {1: True},
            "users.read",
            None,
        ],
    )
    def test_invalid_maps_are_rejected(self, payload):
        with pytest.raises(ValueError):
            validate_delegable_permissions(payload)

    def test_valid_leaf_map_is_accepted_and_copied(self):
        payload = {"users.read": True, "users.delete": False}
        assert validate_delegable_permissions(payload) == payload


class TestRoleRoutesUseClosedVocabulary:
    def test_create_role_rejects_unknown_scope(self):
        from src.api.routes.admin import roles as role_routes
        from src.api.routes.admin.roles import RoleCreate

        body = RoleCreate(
            name="bad-vocab",
            description="bad",
            permissions={"evil.scope": True},
            position=0,
        )
        with (
            patch.object(role_routes, "check_host_restriction"),
            patch.object(role_routes, "require_admin_permission", return_value=1),
            patch("src.api.get_db"),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(role_routes.create_role(_request(), body))
        assert exc.value.status_code == 400

    def test_update_role_rejects_subscope_wildcard(self):
        from src.api.routes.admin import roles as role_routes
        from src.api.routes.admin.roles import RoleUpdate

        hierarchy = {
            "current_pos": 50,
            "target_pos": 50,
            "admin_exists": True,
            "role_pos": 10,
            "role_is_system": False,
        }
        body = RoleUpdate(description="test", permissions={"reports.*": True})
        with (
            patch.object(role_routes, "check_host_restriction"),
            patch.object(role_routes, "require_admin_permission", return_value=1),
            patch.object(role_routes, "_load_hierarchy", return_value=hierarchy),
            patch("src.api.get_db"),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    role_routes.update_role(_request("PUT"), SnowflakeIDParam(1), body)
                )
        assert exc.value.status_code == 400


# --- Inverse direction: used scopes must be declared -----------------------
#
# The committed tree is scanned (matching the repo's other structural guards):
# peers may hold stale working-tree copies, but only merged code matters. In
# CI HEAD is the checked-out tree, so the two coincide.


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, stderr=subprocess.DEVNULL
    ).decode("utf-8")


@lru_cache(maxsize=1)
def _head() -> str:
    # Resolve HEAD once per process. In this workspace peers commit frequently;
    # re-resolving per call could pair a new declaration blob with an older
    # module snapshot and report a spurious gap.
    return _git("rev-parse", "HEAD").strip()


@lru_cache(maxsize=1)
def _committed_scope_sources() -> dict[str, str]:
    """The scope table and its derived delegable set *at the same HEAD*."""
    import ast as _ast

    source = _git("show", f"{_head()}:src/core/admin/permissions.py")
    tree = _ast.parse(source)
    for node in tree.body:
        if not isinstance(node, _ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, _ast.Name)]
        if "PERMISSION_SCOPES" in targets and isinstance(node.value, _ast.Dict):
            scopes = {
                k.value: v.value
                for k, v in zip(node.value.keys, node.value.values)
                if isinstance(k, _ast.Constant) and isinstance(v, _ast.Constant)
            }
            delegable = frozenset(
                k for k in scopes if k != "*" and not k.endswith(".*")
            )
            return {"scopes": scopes, "delegable": delegable}
    raise AssertionError("PERMISSION_SCOPES dict not found in committed permissions.py")


@lru_cache(maxsize=1)
def _committed_admin_routes() -> tuple[str, ...]:
    return tuple(
        raw
        for raw in _git("ls-tree", "-r", "--name-only", _head()).splitlines()
        if raw.startswith("src/api/routes/admin/") and raw.endswith(".py")
    )


@lru_cache(maxsize=None)
def _committed_source(rel_path: str) -> str:
    return _git("show", f"{_head()}:{rel_path}")


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def scopes_used_in(source: str) -> set[str]:
    """Literal permission scopes passed to an admin gate function in ``source``."""
    tree = ast.parse(source)
    used: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node.func) not in _GATE_FUNCS:
            continue
        args = list(node.args) + [kw.value for kw in node.keywords]
        for arg in args:
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and _SCOPE_RE.match(arg.value)
            ):
                used.add(arg.value)
    return used


@lru_cache(maxsize=1)
def _used_scopes() -> dict[str, frozenset[str]]:
    used: dict[str, set[str]] = {}
    for path in _committed_admin_routes():
        for scope in scopes_used_in(_committed_source(path)):
            used.setdefault(scope, set()).add(path)
    return {scope: frozenset(paths) for scope, paths in used.items()}


class TestUsedScopesAreDeclared:
    def test_sweep_is_not_vacuous(self):
        routes = _committed_admin_routes()
        used = _used_scopes()
        assert len(routes) > 20, f"admin route scan saw only {len(routes)} modules"
        assert len(used) >= 20, f"scope scan found only {len(used)} scopes"
        # The device-trust gate must be seen -- otherwise the guard could pass
        # while silently ignoring the very route it exists to police.
        assert "admin.security.devices" in used
        assert any("device_trust.py" in p for p in used["admin.security.devices"])

    def test_every_used_scope_is_declared_and_delegable(self):
        committed = _committed_scope_sources()
        scopes = committed["scopes"]
        delegable = committed["delegable"]
        undeclared = {
            scope: sorted(paths)
            for scope, paths in _used_scopes().items()
            if scope not in scopes or scope not in delegable
        }
        assert not undeclared, (
            "admin gate scopes used at call sites but not declared/delegable:\n  "
            + "\n  ".join(f"{s} -> {p}" for s, p in sorted(undeclared.items()))
            + "\nDeclare each in PERMISSION_SCOPES (which also makes it delegable) "
            "or fix the call site."
        )

    def test_detector_flags_a_novel_undeclared_scope(self):
        found = scopes_used_in('require_admin_permission(request, "admin.brand.new")')
        assert found == {"admin.brand.new"}
        assert "admin.brand.new" not in PERMISSION_SCOPES

    def test_real_device_scope_gap_would_be_flagged(self):
        # Non-vacuity on the actual finding: replaying the pre-fix vocabulary
        # (declaration removed) must make the same sweep fail on the device
        # scope, so the guard cannot pass by ignoring that call site.
        reduced = {
            k: v for k, v in PERMISSION_SCOPES.items() if k != "admin.security.devices"
        }
        reduced_delegable = frozenset(
            k for k in reduced if k != "*" and not k.endswith(".*")
        )
        undeclared = {
            scope
            for scope in _used_scopes()
            if scope not in reduced or scope not in reduced_delegable
        }
        assert "admin.security.devices" in undeclared
