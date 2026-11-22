"""Static guard: every @everyone-equivalent detection excludes the author.

N783 established that the permission gate (``validate_mentions``) must treat a
role held by every member EXCEPT the pinger as an ``@everyone`` equivalent -- the
notification fan-out always skips the author, so that role reaches precisely
the ``@everyone`` audience.  N670/N791 route recipients of such mass pings
through ``suppress_everyone``/``suppress_roles``.  Both decisions must use the
SAME author-excluding coverage verdict.  An ad-hoc "role covers N of N members"
count (which includes the author) misses the all-except-author shape and leaks
a mass ping to opted-out users.

This guard reads source instead of executing it, and fails closed on new
detection shapes:

  1. ``role_covers_all_members`` must keep its ``exclude_user_id`` parameter.
  2. Every call to that helper under ``src/core/notifications`` must pass the
     author/user id (4th positional arg or ``exclude_user_id=`` keyword, never a
     literal ``None``), and both decision sites (the gate and the fan-out) must
     delegate to it.  A floor of two call sites keeps the sweep non-vacuous.
  3. No ad-hoc comparison of a role-member count against a full-roster count may
     appear in the notification manager outside the shared helper: new mass-ping
     detection shapes must go through the helper and fail closed.
  4. Non-vacuity self-tests feed synthetic sources through the same detector and
     require that each regression shape is actually flagged.

No files are mutated; all self-test inputs are in-memory strings.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]
NOTIF_DIR = SRC / "core" / "notifications"
MANAGER_DIR = NOTIF_DIR / "manager"
HELPERS_PATH = MANAGER_DIR / "helpers.py"
VALIDATOR_PATH = MANAGER_DIR / "mention_validator.py"
CREATOR_PATH = MANAGER_DIR / "creator.py"

COVERAGE_HELPER = "role_covers_all_members"
# Names that resolutely identify the message author / acting user.  The gate's
# parameter is ``user_id`` (the pinger whose mentions are validated); the
# fan-out's is ``author_id``.
AUTHOR_ID_NAMES = frozenset(
    {"user_id", "author_id", "actual_author_id", "sender_id", "actor_id"}
)


def _dotted_name(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_coverage_call(node) -> bool:
    return (
        isinstance(node, ast.Call)
        and _dotted_name(node.func) == COVERAGE_HELPER
    )


def _exclude_arg(call: ast.Call):
    """Return the ``exclude_user_id`` argument of a coverage call, or None."""
    if len(call.args) >= 4:
        return call.args[3]
    for kw in call.keywords:
        if kw.arg == "exclude_user_id":
            return kw.value
    return None


def _exclude_arg_is_author(arg) -> bool:
    """Whether the exclusion argument is a real author/user id, not a literal."""
    if arg is None or isinstance(arg, ast.Constant):
        return False
    name = _dotted_name(arg)
    if name is not None:
        return name in AUTHOR_ID_NAMES or name.endswith("user_id") or "author" in name
    # Allow an attribute/expression only if it visibly references an author id.
    dumped = ast.dump(arg).lower()
    return "author" in dumped or "user_id" in dumped


def _calls_in_source(source: str):
    return [n for n in ast.walk(ast.parse(source)) if _is_coverage_call(n)]


def _production_calls():
    found = []
    for path in sorted(NOTIF_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if _is_coverage_call(node):
                found.append((path, node))
    return found


def _adhoc_coverage_lines(source: str):
    """Lines comparing a role-member count against a full-roster count.

    ``len(set(role_members)) >= server_member_count`` is the canonical bypass.
    Any ``>=``/``>``/``==`` whose operands mention role members and a roster
    count is treated as an unauthorised, author-unaware detection shape.
    """
    tree = ast.parse(source)

    def _helper_range():
        for node in tree.body:
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == COVERAGE_HELPER
            ):
                return node.lineno, node.end_lineno
        return None

    skip = _helper_range()
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if skip and skip[0] <= node.lineno <= skip[1]:
            continue
        if not any(
            isinstance(op, (ast.GtE, ast.Gt, ast.Eq)) for op in node.ops
        ):
            continue
        texts = [ast.dump(x).lower() for x in [node.left, *node.comparators]]
        role_side = any("role_member" in t for t in texts)
        roster_side = any(
            "member_count" in t or "server_member" in t for t in texts
        )
        if role_side and roster_side:
            findings.append(node.lineno)
    return findings


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == name
    )


# --- Live-tree invariants ---------------------------------------------------


def test_coverage_helper_keeps_exclude_user_id():
    fn = _function(ast.parse(HELPERS_PATH.read_text(encoding="utf-8")), COVERAGE_HELPER)
    params = [a.arg for a in fn.args.args]
    assert "exclude_user_id" in params, (
        "role_covers_all_members lost its author-exclusion parameter; N783 "
        "can no longer align the gate with the fan-out"
    )


def test_every_production_coverage_call_excludes_author():
    calls = _production_calls()
    assert len(calls) >= 2, (
        "expected at least the gate and suppression call sites to delegate "
        "to role_covers_all_members; a smaller set makes this sweep vacuous"
    )
    bad = [
        f"{path.relative_to(SRC)}:{call.lineno}"
        for path, call in calls
        if not _exclude_arg_is_author(_exclude_arg(call))
    ]
    assert not bad, (
        "everyone-equivalent coverage call(s) do not exclude the author: "
        + ", ".join(bad)
    )


@pytest.mark.parametrize(
    "path,func",
    [
        (VALIDATOR_PATH, "validate_mentions"),
        (CREATOR_PATH, "create_notifications"),
    ],
)
def test_both_decision_sites_delegate_with_author(path, func):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = _function(tree, func)
    calls = [n for n in ast.walk(fn) if _is_coverage_call(n)]
    assert calls, (
        f"{func} must route its everyone-equivalent verdict through "
        f"{COVERAGE_HELPER} (which excludes the author)"
    )
    assert all(_exclude_arg_is_author(_exclude_arg(c)) for c in calls), (
        f"{func} delegates coverage detection without excluding the author"
    )


def test_no_adhoc_coverage_detection_in_manager():
    bad = []
    for path in sorted(MANAGER_DIR.glob("*.py")):
        for line in _adhoc_coverage_lines(path.read_text(encoding="utf-8")):
            bad.append(f"{path.relative_to(SRC)}:{line}")
    assert not bad, (
        "author-unaware role/full-roster coverage comparison(s) found: "
        + ", ".join(bad)
        + "; route every @everyone-equivalent detection through "
        + COVERAGE_HELPER
    )


# --- Non-vacuity: the detector must flag each regression shape -------------


def test_detector_flags_missing_author_argument():
    source = """
def detect(db, role_id, server_id, author_id):
    return role_covers_all_members(db, role_id, server_id)
"""
    calls = _calls_in_source(source)
    assert len(calls) == 1
    assert not _exclude_arg_is_author(_exclude_arg(calls[0])), (
        "dropping the author argument must be flagged"
    )


def test_detector_flags_none_author_argument():
    source = """
def detect(db, role_id, server_id):
    return role_covers_all_members(db, role_id, server_id, None)
"""
    calls = _calls_in_source(source)
    assert len(calls) == 1
    assert not _exclude_arg_is_author(_exclude_arg(calls[0])), (
        "an explicit None exclusion must be flagged"
    )


def test_detector_flags_adhoc_coverage_comparison():
    source = """
def detect(role_members, _server_member_count):
    return len(set(role_members)) >= _server_member_count
"""
    assert _adhoc_coverage_lines(source), (
        "the author-unaware count-based detection must be flagged"
    )


def test_detector_accepts_correct_shapes():
    source = """
def gate(db, role_id, server_id, user_id):
    return role_covers_all_members(db, role_id, server_id, user_id)

def fanout(db, role_id, server_id, author_id):
    return role_covers_all_members(db, role_id, server_id, author_id)
"""
    calls = _calls_in_source(source)
    assert len(calls) == 2
    assert all(_exclude_arg_is_author(_exclude_arg(c)) for c in calls)
    assert not _adhoc_coverage_lines(source)
