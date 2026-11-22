"""Follow-up to N646: every target-bearing approval gate must bind its target.

N646 taught ``require_approved_request`` to pin an approval to an exact
``(target_id, requesting_admin)`` pair, and wired that binding into the
``users.delete`` and ``users.force_purge`` routes.  The artifact force-delete
route destroys one *specific* artifact (``/admin/artifacts/{artifact_id}``)
but was left calling the gate action-only.  That means an approval granted for
artifact A -- or a global/unbound approval with ``target_id IS NULL`` -- can be
consumed through the ``? IS NULL OR col = ?`` disjunct to delete artifact B.

These tests fail against the action-only call site and pass only once the
delete route passes ``target_context=(artifact_id, acting_admin_id)``.  A
static guard makes sure no other target-bearing caller silently drops the
binding again.
"""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.api.routes.admin import artifacts as artifacts_routes


pytestmark = [pytest.mark.security, pytest.mark.unit]

# Gate actions that operate on one concrete object rather than the whole
# instance.  Any call site for these MUST supply the target_context binding.
TARGET_BEARING_ACTIONS = {
    "users.delete",
    "users.force_purge",
    "artifacts.force_delete",
}


class _Manager:
    def __init__(self):
        self.deleted = []

    def delete(self, aid, purging_user_id=None):
        self.deleted.append((aid, purging_user_id))
        return True

    def get(self, aid):
        return None


def test_artifact_delete_route_binds_target_and_actor(monkeypatch):
    """The destructive route must pin the approval to the artifact + admin."""
    calls = []

    def _spy(action, **kwargs):
        calls.append((action, kwargs))
        return 4242

    monkeypatch.setattr(
        artifacts_routes, "check_host_restriction", lambda request: None
    )
    monkeypatch.setattr(
        artifacts_routes, "require_admin_permission", lambda request, perm: 100
    )
    monkeypatch.setattr(artifacts_routes, "require_approved_request", _spy)
    manager = _Manager()
    monkeypatch.setattr(artifacts_routes, "_get_manager", lambda: manager)

    asyncio.run(
        artifacts_routes.admin_delete_artifact("555", SimpleNamespace(client=None))
    )

    assert calls == [
        ("artifacts.force_delete", {"target_context": (555, 100)})
    ], "artifact force-delete must consume an approval bound to the exact artifact/admin"
    assert manager.deleted == [(555, 100)]


def test_no_target_bearing_gate_caller_drops_target_context():
    """Static guard: no target-bearing action may be gated action-only.

    Parses every admin route module so a future target-bearing caller cannot
    reintroduce the unbound-approval gap by forgetting ``target_context``.
    """
    admin_routes = Path(artifacts_routes.__file__).resolve().parent
    offenders = []
    checked = 0
    for path in sorted(admin_routes.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "require_approved_request":
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            action = node.args[0].value
            if action not in TARGET_BEARING_ACTIONS:
                continue
            checked += 1
            if not any(kw.arg == "target_context" for kw in node.keywords):
                offenders.append(f"{path.name}:{node.lineno} {action}")

    assert checked >= 3, "guard must actually inspect the bound call sites"
    assert not offenders, "target-bearing approval callers missing target_context: " + ", ".join(
        offenders
    )
