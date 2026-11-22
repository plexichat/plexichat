"""Committed-tree guard: channel-override ``channels.manage`` is SERVER-scoped.

``aa19a5a2`` (fix(servers): require server-scoped channels.manage on channel
overrides) closed a privilege escalation in
``src/core/servers/handlers/roles/overrides.py``. ``channels.manage`` is a
server-scoped permission: the resolver applies per-channel overrides whenever a
channel id is supplied, so resolving it with ``channel_id`` let a member holding
a channel-only ``channels.manage`` override rewrite that channel's (or another
channel's) role/member overrides -- a channel grant authorising a server-level
write. The fix resolves the gate at server scope at both sites
(``set_channel_override`` and ``delete_channel_override``):

    self.manager.require_permission(user_id, channel.server_id, "channels.manage")

This module reads the blob straight out of HEAD via ``git show`` (never the
working tree) and fails if either gate is missing, if a gate is resolved against
anything other than ``channel.server_id``, or if a ``channels.manage`` gate
regresses to the channel-scoped form (a 4th positional ``channel_id`` argument,
or a ``channel_id=`` / ``cid=`` keyword). It also co-pins the ``79e83f54``
strict-int owner-id guard so both halves of the escalation fix stay pinned
together.

Non-vacuity is proven two ways: the real committed blob must contain exactly
the two expected gates (an empty/gutted scan fails the count assertion), and
synthetic reverted blobs fed through the exact same analyser -- 4-arg positional,
``channel_id=``/``cid=`` keyword, wrong scope argument, and gate removed -- must
all be rejected.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_OVERRIDES_PATH = "src/core/servers/handlers/roles/overrides.py"
_PERMISSION = "channels.manage"
_EXPECTED_SITES = 2
_SERVER_SCOPE_LINE = (
    'self.manager.require_permission(user_id, channel.server_id, "channels.manage")'
)
# 79e83f54: strict-int owner/target ids in the override hierarchy gate.
_STRICT_INT_MARKER = 'raise RoleHierarchyError("Cannot verify server owner identity")'
_CHANNEL_KWARGS = frozenset({"channel_id", "cid"})


def _git_show(commit: str, path: str) -> str:
    proc = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=_REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, (
        f"cannot read {path!r} from {commit}: {proc.stderr.strip()}"
    )
    return proc.stdout


@pytest.fixture(scope="module")
def commit_sha() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha
    return sha


def _is_channel_server_id(expr: ast.expr | None) -> bool:
    """True only for the authoritative ``channel.server_id`` scope expression."""
    return (
        isinstance(expr, ast.Attribute)
        and expr.attr == "server_id"
        and isinstance(expr.value, ast.Name)
        and expr.value.id == "channel"
    )


def _permission_literal(call: ast.Call) -> str | None:
    """Resolve a literal permission argument (3rd positional or ``permission=``)."""
    if len(call.args) >= 3:
        arg = call.args[2]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    for kw in call.keywords:
        if kw.arg == "permission" and isinstance(kw.value, ast.Constant):
            if isinstance(kw.value.value, str):
                return kw.value.value
    return None


def _scope_expr(call: ast.Call) -> ast.expr | None:
    if len(call.args) >= 2:
        return call.args[1]
    for kw in call.keywords:
        if kw.arg == "server_id":
            return kw.value
    return None


def _channel_scope_args(call: ast.Call) -> list[str]:
    """Return the channel-scoping arguments present on a gate call."""
    found: list[str] = []
    if len(call.args) >= 4:
        found.append("4th positional channel_id")
    for kw in call.keywords:
        if kw.arg in _CHANNEL_KWARGS:
            found.append(f"{kw.arg}=")
    return found


def _gate_calls(text: str) -> list[ast.Call]:
    tree = ast.parse(text)
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name == "require_permission" and _permission_literal(node) == _PERMISSION:
            calls.append(node)
    return calls


def _analyze(text: str) -> list[str]:
    """Return the list of invariant violations for a candidate overrides blob."""
    problems: list[str] = []
    gates = _gate_calls(text)
    if len(gates) != _EXPECTED_SITES:
        problems.append(
            f"expected {_EXPECTED_SITES} {_PERMISSION!r} gates in overrides.py, "
            f"found {len(gates)}"
        )
    for index, call in enumerate(gates):
        bad = _channel_scope_args(call)
        if bad:
            problems.append(
                f"gate {index}: channel-scoped {_PERMISSION} (regressed form: "
                f"{', '.join(bad)}) -- a channel-only grant could authorize a "
                "server-level override write"
            )
        if not _is_channel_server_id(_scope_expr(call)):
            problems.append(
                f"gate {index}: not resolved at channel.server_id (server scope)"
            )
        if len(call.args) + len(call.keywords) != 3:
            problems.append(
                f"gate {index}: expected exactly the 3-arg user/server/permission "
                "form, got "
                f"{len(call.args) + len(call.keywords)} arguments"
            )
    if _STRICT_INT_MARKER not in text:
        problems.append(
            f"79e83f54 strict-int owner-id guard missing: {_STRICT_INT_MARKER}"
        )
    return problems


def _good_blob() -> str:
    return (
        "class RoleOverrideMixin:\n"
        "    def set_channel_override(self, user_id, channel_id):\n"
        f"        {_SERVER_SCOPE_LINE}\n"
        f"        {_STRICT_INT_MARKER}\n"
        "    def delete_channel_override(self, user_id, channel_id):\n"
        f"        {_SERVER_SCOPE_LINE}\n"
    )


def test_server_scope_gate_pinned_in_committed_tree(commit_sha: str) -> None:
    """Both aa19a5a2 invariants are present in HEAD's overrides.py blob."""
    blob = _git_show(commit_sha, _OVERRIDES_PATH)
    problems = _analyze(blob)
    assert not problems, "; ".join(problems)
    # Anchor the exact textual 3-arg form at both call sites as well.
    assert blob.count(_SERVER_SCOPE_LINE) == _EXPECTED_SITES
    assert _STRICT_INT_MARKER in blob


@pytest.mark.parametrize(
    "reverted_line, reason",
    [
        (
            "self.manager.require_permission(user_id, channel.server_id, "
            '"channels.manage", channel_id)',
            "4th positional channel_id",
        ),
        (
            "self.manager.require_permission(user_id, channel.server_id, "
            '"channels.manage", channel_id=channel_id)',
            "channel_id= keyword",
        ),
        (
            "self.manager.require_permission(user_id, channel.server_id, "
            '"channels.manage", cid=channel_id)',
            "cid= keyword",
        ),
        (
            'self.manager.require_permission(user_id, channel_id, "channels.manage")',
            "channel_id used as the scope argument",
        ),
    ],
)
def test_channel_scoped_regression_is_flagged(reverted_line: str, reason: str) -> None:
    """Non-vacuity: a revert to the channel-scoped form is caught by name."""
    blob = _good_blob().replace(_SERVER_SCOPE_LINE, reverted_line, 1)
    problems = _analyze(blob)
    assert problems, f"synthetic {reason} revert was not caught"
    assert any("channel-scoped" in p or "server scope" in p for p in problems), problems


def test_missing_gate_is_flagged() -> None:
    """Non-vacuity: dropping a gate (empty scan) cannot pass vacuously."""
    blob = _good_blob().replace(f"        {_SERVER_SCOPE_LINE}\n", "", 1)
    problems = _analyze(blob)
    assert problems
    assert any(f"expected {_EXPECTED_SITES}" in p for p in problems), problems


def test_strict_int_regression_is_flagged() -> None:
    """Non-vacuity: removing the 79e83f54 strict-int guard is caught."""
    blob = _good_blob().replace(f"        {_STRICT_INT_MARKER}\n", "", 1)
    problems = _analyze(blob)
    assert problems
    assert any("79e83f54 strict-int" in p for p in problems), problems
