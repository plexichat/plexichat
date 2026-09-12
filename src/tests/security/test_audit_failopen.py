"""Tests for ``ci-tooling/audit_failopen.py``.

The audit tool is the executable replacement for the hand-written findings
annexes. Its value depends on it actually flagging the bug shapes it claims to
flag, so these tests feed it synthetic modules and assert on the hits, then
assert the real repository is clean.

Two properties are covered:

1. Detection -- an unauthenticated route, a mutating route that ignores the
   caller identity, a fail-open manager guard, and a fail-open client helper
   are each reported.
2. Non-detection -- a route that authenticates and authorizes, and an
   auth-establishing route that verifies a credential, are not reported.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = REPO_ROOT / "ci-tooling" / "audit_failopen.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("audit_failopen", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_failopen"] = module
    spec.loader.exec_module(module)
    return module


af = _load_tool()


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def synthetic(tmp_path: Path) -> Path:
    """A miniature server tree containing one of each bug shape."""
    _write(
        tmp_path,
        "src/core/example/manager.py",
        """
from typing import TYPE_CHECKING


class ExampleManager:
    def __init__(self):
        self._servers = None

    if TYPE_CHECKING:
        def update_thing(self, user_id, thing_id) -> None: ...

    def can_act(self):
        # Fail open: the guard grants instead of denying.
        if not self._servers:
            return True
        return self._servers.check()

    def can_act_safely(self):
        # Fail closed: the reference shape.
        if not self._servers:
            return False
        return self._servers.check()

    def delete_thing(self, user_id, thing_id):
        # Manager write that never uses the caller identity.
        self._db.execute("DELETE FROM things WHERE id = ?", (thing_id,))

    def delete_own_thing(self, user_id, thing_id):
        row = self._db.fetch_one("SELECT * FROM things WHERE id = ?", (thing_id,))
        if row and row["owner_id"] != user_id:
            raise PermissionDeniedError("not yours")
        self._db.execute("DELETE FROM things WHERE id = ?", (thing_id,))

    def delete_thing_via_delegate(self, user_id, thing_id):
        self._things.remove(user_id, thing_id)
""",
    )
    _write(
        tmp_path,
        "src/api/routes/example.py",
        """
from fastapi import APIRouter, Depends, Request

router = APIRouter()


@router.post("/no-auth")
async def no_auth(body: dict) -> dict:
    return {"ok": True}


@router.post("/ignores-caller")
async def ignores_caller(
    user=Depends(get_current_user), body: dict = None
) -> dict:
    return do_the_thing(body)


@router.post("/authorized")
async def authorized(request: Request, body: dict = None) -> dict:
    check_host_restriction(request)
    require_admin_permission(request, "example.manage")
    return {"ok": True}


@router.post("/self-scoped")
async def self_scoped(current_user=Depends(get_current_user)) -> dict:
    return {"id": current_user.user_id}


@router.delete("/@me")
async def delete_me(current_user=Depends(get_current_user)) -> dict:
    return {"ok": True}


@router.post("/verify")
async def verify_credential(body: dict) -> dict:
    return {"ok": auth.verify_email(body["token"])}
""",
    )
    _write(
        tmp_path,
        "src/api/websocket/handlers/example.py",
        """
class Handlers:
    async def handle_unauthenticated(self, connection, data):
        return None, None, None

    async def handle_authenticated(self, connection, data):
        if not connection.is_authenticated:
            return None, None, 4004
        return None, None, None
""",
    )
    _write(
        tmp_path,
        "src/js/example.js",
        """
function canEdit(artifact) {
    if (!window.AppState.currentUser) return true;
    return artifact.author_id === window.AppState.currentUser.id;
}
""",
    )
    return tmp_path


def test_flags_fail_open_manager_guard(synthetic: Path) -> None:
    kinds = {hit.symbol: hit.kind for hit in af.sweep_core(synthetic)}
    assert kinds["_servers"] == af.GRANT
    # The sibling helper that denies must not be reported.
    grants = [h for h in af.sweep_core(synthetic) if h.kind == af.GRANT]
    assert len(grants) == 1


def test_flags_route_without_authentication(synthetic: Path) -> None:
    hits = af.sweep_routes(synthetic)
    no_auth = {h.symbol for h in hits if h.kind == af.NO_AUTH}
    assert "POST /no-auth" in no_auth


def test_flags_mutating_route_that_ignores_caller(synthetic: Path) -> None:
    hits = af.sweep_routes(synthetic)
    unauthorized = {h.symbol for h in hits if h.kind == af.UNAUTHZ}
    assert "POST /ignores-caller" in unauthorized


def test_does_not_flag_authorized_or_self_scoped_routes(synthetic: Path) -> None:
    flagged = {
        h.symbol
        for h in af.sweep_routes(synthetic)
        if h.kind in (af.NO_AUTH, af.UNAUTHZ)
    }
    assert "POST /authorized" not in flagged
    assert "POST /self-scoped" not in flagged
    assert "DELETE /@me" not in flagged
    # A route that verifies a credential is itself the auth step.
    assert "POST /verify" not in flagged


def test_flags_ws_handler_without_authentication(synthetic: Path) -> None:
    hits = af.sweep_ws_handlers(synthetic)
    no_auth = {h.symbol for h in hits if h.kind == af.NO_AUTH}
    assert "handle_unauthenticated" in no_auth
    assert "handle_authenticated" not in no_auth


def test_flags_manager_write_ignoring_its_caller(synthetic: Path) -> None:
    hits = af.sweep_managers(synthetic)
    unchecked = {h.symbol for h in hits if h.kind == af.UNCHECKED}
    assert "delete_thing" in unchecked
    # Ownership check, denial, delegation, and a TYPE_CHECKING stub are clear.
    assert "delete_own_thing" not in unchecked
    assert "delete_thing_via_delegate" not in unchecked
    assert "update_thing" not in unchecked


def test_real_repository_manager_writes_all_resolve_the_caller() -> None:
    hits = af.sweep_managers(REPO_ROOT)
    assert [h.path for h in hits if h.kind == af.UNCHECKED] == []


def test_flags_fail_open_client_helper(synthetic: Path) -> None:
    hits = af.sweep_client(synthetic)
    grants = {h.symbol for h in hits if h.kind == af.GRANT}
    assert "canEdit" in grants


def test_real_repository_has_no_fail_open_grants() -> None:
    assert [h for h in af.sweep_core(REPO_ROOT) if h.kind == af.GRANT] == []


def test_real_repository_routes_all_authenticate_and_authorize() -> None:
    hits = af.sweep_routes(REPO_ROOT)
    assert [h.path for h in hits if h.kind == af.NO_AUTH] == []
    assert [h.path for h in hits if h.kind == af.UNAUTHZ] == []


def test_real_repository_ws_handlers_all_authenticate() -> None:
    hits = af.sweep_ws_handlers(REPO_ROOT)
    assert [h.symbol for h in hits if h.kind == af.NO_AUTH] == []


def test_real_repository_client_helpers_do_not_fail_open() -> None:
    client_root = REPO_ROOT.parent / "plexichat-client"
    if not client_root.is_dir():
        pytest.skip("plexichat-client checkout not present")
    assert [h for h in af.sweep_client(client_root) if h.kind == af.GRANT] == []
