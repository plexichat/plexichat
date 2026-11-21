"""Advisory-lock namespace isolation between unrelated quota guards.

The per-server role quota and the per-server scheduled-event quota each take a
PostgreSQL advisory xact lock to serialise their guarded INSERT under READ
COMMITTED. Both key that lock by ``server_id``. If the two quota guards use the
same advisory-lock *namespace*, creating a role and creating a scheduled event
on one server serialise against each other even though the quotas are entirely
unrelated -- safe for correctness (over-serialisation, never under) but wasteful
and a maintenance trap. Each quota must own a distinct namespace.

These tests ratchet the source so the namespace separation and the stable,
resource-derived key cannot silently regress.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SRC_ROOT = Path(__file__).resolve().parents[2]
_ROLE_SRC = _SRC_ROOT / "core/servers/handlers/roles/crud.py"
_EVENT_SRC = _SRC_ROOT / "core/servers/manager/scheduled_event_ops.py"


def _advisory_lock_calls(path):
    """Return ``(namespace_expr, key_expr)`` for each xact-lock call in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        sql = node.args[0]
        if not (isinstance(sql, ast.Constant) and isinstance(sql.value, str)):
            continue
        if "pg_advisory_xact_lock" not in sql.value:
            continue
        params = node.args[1]
        if not isinstance(params, (ast.Tuple, ast.List)) or len(params.elts) < 2:
            continue
        found.append((params.elts[0], params.elts[1]))
    return found


def _namespaces(path):
    namespaces = set()
    for namespace, _key in _advisory_lock_calls(path):
        assert isinstance(namespace, ast.Constant) and isinstance(
            namespace.value, int
        ), f"{path.name}: advisory-lock namespace must be an integer literal"
        namespaces.add(namespace.value)
    return namespaces


def test_unrelated_quota_locks_use_distinct_namespaces():
    """Role and scheduled-event quotas must not share a lock namespace."""
    role_ns = _namespaces(_ROLE_SRC)
    event_ns = _namespaces(_EVENT_SRC)
    assert role_ns, "no role advisory-lock namespace found"
    assert event_ns, "no scheduled-event advisory-lock namespace found"

    shared = role_ns & event_ns
    assert not shared, (
        f"role and scheduled-event quota locks share namespace(s) {sorted(shared)}; "
        "unrelated quotas must serialise on distinct namespaces"
    )


def test_quota_lock_keys_are_derived_from_server_id():
    """Each quota lock must key on the server resource, never a constant."""
    for path in (_ROLE_SRC, _EVENT_SRC):
        calls = _advisory_lock_calls(path)
        assert calls, f"{path.name}: no advisory-lock call found"
        for _namespace, key in calls:
            assert "server_id" in ast.dump(key), (
                f"{path.name}: advisory-lock key must derive from server_id, "
                f"got {ast.dump(key)}"
            )
            assert not (isinstance(key, ast.Constant) and isinstance(key.value, int)), (
                f"{path.name}: advisory-lock key must not be a constant"
            )
