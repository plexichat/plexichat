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

    assert calls == [("artifacts.force_delete", {"target_context": (555, 100)})], (
        "artifact force-delete must consume an approval bound to the exact artifact/admin"
    )
    assert manager.deleted == [(555, 100)]


def _gate_call_names(tree: ast.AST) -> set[str]:
    """Every local name that can refer to ``require_approved_request``.

    Resolves ``from ... import require_approved_request as <alias>`` renames and
    simple ``gate = require_approved_request`` assignments, so a caller cannot
    dodge the guard by importing the gate under a different name.  Attribute
    calls (``module.require_approved_request``) are matched by their ``.attr``
    in the scanner below and need no per-module alias here.
    """
    names = {"require_approved_request"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "require_approved_request":
                    names.add(alias.asname or alias.name)
    # Second pass (twice) resolves chains such as ``gate = require_approved_request``
    # and ``fn = gate`` regardless of source order within the module.
    for _ in range(2):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
                if node.value.id in names:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            names.add(target.id)
    return names


def _scan_source(
    source: str, path: str, actions: frozenset[str] | set[str] = TARGET_BEARING_ACTIONS
) -> tuple[int, list[str]]:
    """Return ``(checked, offenders)`` for one source string.

    ``checked`` counts target-bearing gate calls that were actually inspected;
    ``offenders`` names any such call missing the ``target_context`` binding.
    The action set is injectable so non-vacuity can be proven with a novel
    target-bearing action as well as the three real call sites.
    """
    tree = ast.parse(source, filename=str(path))
    names = _gate_call_names(tree)
    offenders: list[str] = []
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            continue
        if name not in names:
            continue
        if (
            not node.args
            or not isinstance(node.args[0], ast.Constant)
            or not isinstance(node.args[0].value, str)
        ):
            continue
        action = node.args[0].value
        if action not in actions:
            continue
        checked += 1
        if not any(kw.arg == "target_context" for kw in node.keywords):
            offenders.append(f"{path}:{node.lineno} {action} (via {name})")
    return checked, offenders


def test_no_target_bearing_gate_caller_drops_target_context():
    """Static guard: no target-bearing action may be gated action-only.

    Parses every admin route module so a future target-bearing caller cannot
    reintroduce the unbound-approval gap by forgetting ``target_context``.
    """
    admin_routes = Path(artifacts_routes.__file__).resolve().parent
    offenders: list[str] = []
    checked = 0
    for path in sorted(admin_routes.rglob("*.py")):
        c, o = _scan_source(path.read_text(encoding="utf-8"), path.name)
        checked += c
        offenders.extend(o)

    assert checked >= 3, "guard must actually inspect the bound call sites"
    assert not offenders, (
        "target-bearing approval callers missing target_context: "
        + ", ".join(offenders)
    )


def test_detector_recognizes_import_alias_and_assignment_rename():
    """A gate imported/renamed under another name is still inspected."""
    source = (
        "from src.api.routes.admin.utils import "
        "require_approved_request as _req\n"
        "gate = _req\n"
        "def handler():\n"
        '    gate("artifacts.force_delete")\n'
    )
    checked, offenders = _scan_source(source, "alias.py")
    assert checked == 1, "the aliased call must be recognized as a gate call"
    assert offenders, (
        "an aliased target-bearing call without the binding must be flagged"
    )

    bound = source.replace(
        'gate("artifacts.force_delete")',
        'gate("artifacts.force_delete", target_context=(555, 100))',
    )
    checked, offenders = _scan_source(bound, "alias.py")
    assert checked == 1 and not offenders, "the bound aliased call must pass"


def test_detector_non_vacuous_on_novel_target_bearing_action():
    """A newly added target-bearing action is checked, not silently skipped."""
    actions = TARGET_BEARING_ACTIONS | {"messages.delete"}
    source = (
        "from src.api.routes.admin.utils import "
        "require_approved_request as gate\n"
        "def handler():\n"
        '    gate("messages.delete")\n'
    )
    checked, offenders = _scan_source(source, "novel.py", actions)
    assert checked == 1 and offenders, (
        "novel action missing the binding must be flagged"
    )

    bound = source.replace(
        'gate("messages.delete")',
        'gate("messages.delete", target_context=(7, 9))',
    )
    checked, offenders = _scan_source(bound, "novel.py", actions)
    assert checked == 1 and not offenders, "the bound novel call must pass"
