"""Membership revocation must evict every per-viewer read cache.

Kick/ban/leave all call ``_invalidate_member_access_caches``, but that helper
used to clear only the roster, joined-server, channel, sticker and soundboard
namespaces. The role listing (300s TTL) and emoji list/detail reads (10s TTL)
are filled behind a membership check, so a removed member kept receiving a
cached 200 -- including role permission data -- long after the revocation
committed. These are static AST regressions so they hold even when the app
package cannot be imported in a worker.
"""

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[3]
ROUTES = ROOT / "src" / "api" / "routes" / "servers"


def _func(tree, name):
    return next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
        ),
        None,
    )


def test_revocation_helper_clears_role_and_emoji_caches():
    """The shared revocation helper must cover the gated role/emoji caches."""
    tree = ast.parse((ROUTES / "members.py").read_text(encoding="utf-8"))
    fn = _func(tree, "_invalidate_member_access_caches")
    assert fn is not None

    patterns = set()
    calls = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            calls.add(node.func.id)
        if isinstance(node.func, ast.Name) and node.func.id == "invalidate_pattern":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    patterns.add(arg.value)

    # Delegate to the canonical roles helper so this cannot drift from the
    # role/permission mutation paths that use it too.
    assert "_invalidate_roles_listing_cache" in calls
    # Emoji list/detail keys are `<prefix>:sid:<id>:...` per credential; both
    # namespaces must be cleared on a kick/ban/leave.
    assert "emoji_list_api:*" in patterns
    assert "emoji_detail_api:*" in patterns

    roles_tree = ast.parse((ROUTES / "roles.py").read_text(encoding="utf-8"))
    assert '"server_roles_api"' in (ROUTES / "roles.py").read_text(encoding="utf-8")
    assert _func(roles_tree, "_invalidate_roles_listing_cache") is not None


def test_every_revocation_path_uses_the_invalidation_helper():
    """A new kick/ban/leave route must not forget the shared eviction call."""
    paths = {
        "kick": ("members.py", "kick_member"),
        "ban": ("bans.py", "ban_member"),
        "leave": ("misc.py", "leave_server"),
    }
    for label, (filename, funcname) in paths.items():
        fn = _func(ast.parse((ROUTES / filename).read_text(encoding="utf-8")), funcname)
        assert fn is not None, f"{label}: {funcname} not found"
        calls = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_invalidate_member_access_caches"
        ]
        assert calls, f"{label}: {funcname} does not invalidate access caches"
