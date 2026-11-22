"""Static guard: private-event dispatches must fail closed on audience lookup failure.

Campaign context
----------------
A family of fixes (artifacts 5525788b, interactions 9a6ef8df, plus the DM-mention
notification fix) closed the same fail-open shape::

    user_ids = None                       # authoritative audience is a lookup
    try:
        user_ids = get_participant_ids(...)   # / fetch_one("SELECT bot_id ...")
    except Exception:
        user_ids = None                   # <-- FAIL OPEN
    events.dispatch(event, user_ids=user_ids, server_id=...)

``EventRouter.get_recipients`` treats ``user_ids=None`` as "route by scope", so a
server-scoped event carrying private content (artifact title, message content,
interaction token/options) is widened to *every* member of the server, including
non-participants. The fix is to resolve an unverifiable audience to ``[]``::

    if user_ids is None:
        user_ids = []

What this guard does
--------------------
A whole-tree AST sweep over committed ``HEAD`` blobs (never the contested working
tree) finds every ``.dispatch(...)`` call whose event is server-scoped and whose
payload is private or whose audience comes from a fallible lookup, and classifies
each as ``fail_closed`` or ``fail_open``. Only ``fail_closed`` is acceptable; a
``fail_open`` site fails the suite unless it is in
:data:`DOCUMENTED_PUBLIC_DISPATCHES` with a written reason (public events / events
with no private content, where scope widening is intended). A NEW private-event
dispatch that can hand ``None`` to ``dispatch`` is seen by the same sweep and
cannot pass just because it is not named in a per-site marker list.

Non-vacuity
-----------
The sweep enumerates >100 committed production modules, and the detector must see
the four documented private emitters (each fixed at ``HEAD``) as ``fail_closed``.
Injected synthetic sources prove that a bare ``dispatch(event)`` on a lookup-
failure branch, a literal ``user_ids=None``, and an audience variable set to
``None`` on failure are all flagged, while ``user_ids=[]``, an explicit list, and
an ``if user_ids is None: user_ids = []`` normalization are not.
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]
REPO_ROOT = SRC.parent

pytestmark = [pytest.mark.unit, pytest.mark.security]

# Call names whose result is an authoritative audience lookup. A dispatch whose
# audience is derived from one of these must resolve to an explicit list on
# failure, never to ``None``.
_LOOKUP_NAME_SUBSTRINGS = (
    "participant",
    "recipient",
    "member_user_ids",
    "fetch_one",
    "fetchrow",
    "fetchone",
    "bot_id",
    "owner",
    "_event_user_ids",
)

# Private payload markers. Presence of one in an emitter means widening the
# audience leaks content a non-participant must never receive.
_PRIVATE_DICT_KEYS = frozenset(
    {
        "title",
        "content",
        "token",
        "body",
        "text",
        "message",
        "options",
        "resolved",
        "summary",
        "transcript",
        "description",
        "call_id",
        "note",
        "comment",
    }
)
_PRIVATE_CALL_KWARGS = frozenset({"content", "token", "title", "body", "text", "data"})


@dataclass(frozen=True)
class PrivateDispatch:
    """One server-scoped, private dispatch call found at ``HEAD``."""

    rel_path: str
    func: str
    line: int
    user_ids_mode: str  # bare | literal-none | var | helper-call | explicit
    evidence: str  # why this call is in scope (private payload / lookup)
    outcome: str  # fail_closed | fail_open
    detail: str  # classification reason plus a source snippet


@dataclass(frozen=True)
class DocumentedPublicDispatch:
    """A reviewed dispatch where scope widening is safe, with a written reason."""

    rel_path: str
    func: str
    why: str


# Empty at HEAD: every private audience lookup currently fails closed. Add an
# entry ONLY for a dispatch whose event is public or carries no private content,
# so widening to the server's members is intended. An unregistered fail-open
# private dispatch is a failure by design -- there is no catch-all allowlist.
DOCUMENTED_PUBLIC_DISPATCHES: tuple[DocumentedPublicDispatch, ...] = ()

# The committed-tree fixes this guard finalizes, with the reason each event is
# private. Used for enumeration and non-vacuity, NOT as a bypass: a brand-new
# private dispatch is found by the whole-tree sweep and stands or falls on its
# own structure.
KNOWN_PRIVATE_EMITTERS = {
    ("src/api/routes/artifacts_helpers.py", "_emit_artifact_message"): (
        "inline artifact MESSAGE_CREATE carries the private artifact title/content"
    ),
    ("src/core/artifacts/transcription/worker.py", "_emit_artifact_update"): (
        "ARTIFACT_UPDATE carries private voice-call artifact identifiers"
    ),
    ("src/core/artifacts/manager.py", "_emit_lifecycle_event"): (
        "artifact lifecycle event carries the private artifact title"
    ),
    ("src/core/applications/interactions/handler.py", "dispatch_interaction"): (
        "INTERACTION_CREATE carries the private interaction token and option body"
    ),
}


# --- Git / committed-tree plumbing -----------------------------------------


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, stderr=subprocess.DEVNULL
    ).decode("utf-8")


@lru_cache(maxsize=1)
def _head() -> str:
    return _git("rev-parse", "HEAD").strip()


@lru_cache(maxsize=1)
def _committed_python_paths() -> tuple[str, ...]:
    """Every committed production Python module (scope / coverage evidence)."""
    paths: list[str] = []
    for raw in _git("ls-tree", "-r", "--name-only", _head()).splitlines():
        if not raw.endswith(".py") or not raw.startswith("src/"):
            continue
        if "/tests/" in raw or "/selftest/" in raw:
            continue
        paths.append(raw)
    return tuple(paths)


@lru_cache(maxsize=1)
def _committed_dispatch_paths() -> tuple[str, ...]:
    """Committed production modules that actually contain a ``.dispatch(`` call."""
    out: list[str] = []
    for raw in _git(
        "grep", "-l", "-E", r"\.dispatch[[:space:]]*\(", _head(), "--", "src"
    ).splitlines():
        rel = raw.split(":", 1)[1] if ":" in raw else raw
        if not rel.endswith(".py"):
            continue
        if "/tests/" in rel or "/selftest/" in rel:
            continue
        out.append(rel)
    return tuple(out)


@lru_cache(maxsize=None)
def _committed_source(rel_path: str) -> str:
    return _git("show", f"{_head()}:{rel_path}")


# --- AST helpers -----------------------------------------------------------


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return ""


def _reachable_nodes(root: ast.AST):
    """Yield ``root`` and its execution-path descendants, skipping nested defs."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)


def _kw(call: ast.Call, name: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _is_none_const(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_empty_list(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    return False


def _is_lookup_call(call: ast.Call) -> bool:
    name = _call_name(call.func).lower()
    return any(token in name for token in _LOOKUP_NAME_SUBSTRINGS)


def _is_private_context(func: ast.AST, rel_path: str) -> tuple[bool, str]:
    """Whether an emitter's event is private (or has a restricted audience)."""
    if (rel_path, getattr(func, "name", "")) in KNOWN_PRIVATE_EMITTERS:
        return True, KNOWN_PRIVATE_EMITTERS[(rel_path, func.name)]
    for node in _reachable_nodes(func):
        if isinstance(node, ast.Call) and _is_lookup_call(node):
            return True, f"audience lookup {_call_name(node.func)}"
    for node in _reachable_nodes(func):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value in _PRIVATE_DICT_KEYS
                ):
                    return True, f"private payload key {key.value!r}"
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in _PRIVATE_CALL_KWARGS:
                    return True, f"private payload kwarg {kw.arg!r}"
    return False, ""


def _event_server_scoped(func: ast.AST, call: ast.Call) -> bool:
    """The widening precondition: the event can be routed by server scope."""
    if _kw(call, "server_id") is not None:
        return True
    event_node = call.args[0] if call.args else _kw(call, "event")
    if event_node is None:
        return False
    if isinstance(event_node, ast.Call) and _kw(event_node, "server_id") is not None:
        return True
    if isinstance(event_node, ast.Name):
        for node in _reachable_nodes(func):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == event_node.id for t in node.targets
            ):
                value = node.value
                if isinstance(value, ast.Call) and _kw(value, "server_id") is not None:
                    return True
    return False


def _build_parents(root: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _branch_checks_lookup_failure(test: ast.AST) -> bool:
    """``if not x`` / ``if x is None`` / ``if x == None`` => failure check."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return True
    if isinstance(test, ast.Compare):
        for op, comparator in zip(test.ops, test.comparators):
            if (
                isinstance(op, (ast.Is, ast.Eq))
                and isinstance(comparator, ast.Constant)
                and comparator.value is None
            ):
                return True
    return False


def _in_failure_branch(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Whether ``node`` sits on an except / lookup-failure branch."""
    cur = node
    while cur in parents:
        parent = parents[cur]
        if isinstance(parent, ast.ExceptHandler):
            return True
        if isinstance(parent, ast.If):
            in_body = any(sub is cur for stmt in parent.body for sub in ast.walk(stmt))
            in_orelse = any(
                sub is cur for stmt in parent.orelse for sub in ast.walk(stmt)
            )
            failure_check = _branch_checks_lookup_failure(parent.test)
            if in_orelse and not failure_check:
                return True  # `if <lookup succeeded>: ... else: <failure>`
            if in_body and failure_check:
                return True  # `if <lookup> is None: <failure>`
        cur = parent
    return False


def _assignments_to(var: str, func: ast.AST) -> list[ast.AST]:
    out: list[ast.AST] = []
    for node in _reachable_nodes(func):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == var for t in node.targets
        ):
            out.append(node)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == var
            and node.value is not None
        ):
            out.append(node)
    return out


def _has_normalization(var: str, func: ast.AST, before_line: int) -> bool:
    """An ``if <var> is None: <var> = []`` guard before the dispatch."""
    for node in _reachable_nodes(func):
        if not isinstance(node, ast.If) or node.lineno >= before_line:
            continue
        test = node.test
        matches = False
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == var
        ):
            for op, comparator in zip(test.ops, test.comparators):
                if isinstance(op, ast.Is) and _is_none_const(comparator):
                    matches = True
        elif (
            isinstance(test, ast.UnaryOp)
            and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Name)
            and test.operand.id == var
        ):
            matches = True
        if not matches:
            continue
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == var for t in sub.targets
                ):
                    if _is_empty_list(sub.value):
                        return True
    return False


def _nullable_local_helpers(tree: ast.AST) -> set[str]:
    """Functions that ``return None`` from an except handler (nullable lookups)."""
    names: set[str] = set()
    for func in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        parents = _build_parents(func)
        for node in ast.walk(func):
            if not (isinstance(node, ast.Return) and _is_none_const(node.value)):
                continue
            cur = node
            while cur in parents:
                if isinstance(parents[cur], ast.ExceptHandler):
                    names.add(func.name)
                    break
                cur = parents[cur]
    return names


def _classify(
    mode: str,
    uid: ast.AST | None,
    func: ast.AST,
    call: ast.Call,
    parents: dict[ast.AST, ast.AST],
    nullable_helpers: set[str],
) -> tuple[str, str]:
    if mode == "bare":
        return "fail_open", "bare dispatch: user_ids defaults to None"
    if mode == "literal-none":
        return "fail_open", "explicit user_ids=None at a private dispatch"
    if mode == "helper-call":
        return (
            "fail_open",
            f"user_ids is a direct call to nullable helper {_call_name(uid.func)}",
        )
    if mode == "explicit":
        return "fail_closed", "explicit user_ids expression"
    # mode == "var": trace the audience variable's failure paths.
    var = uid.id  # type: ignore[union-attr]
    if _has_normalization(var, func, call.lineno):
        return "fail_closed", f"{var} normalized to [] before dispatch"
    assigns = _assignments_to(var, func)
    failure_none = any(
        _is_none_const(a.value) and _in_failure_branch(a, parents) for a in assigns
    )
    if failure_none:
        return (
            "fail_open",
            f"{var} set to None on a lookup-failure path (no normalization)",
        )
    any_none = any(_is_none_const(a.value) for a in assigns)
    failure_empty = any(
        _is_empty_list(a.value) and _in_failure_branch(a, parents) for a in assigns
    )
    if any_none and not failure_empty:
        return (
            "fail_open",
            f"{var} can be None at dispatch (no fail-closed failure assignment)",
        )
    return "fail_closed", f"{var} resolves to an explicit audience on failure"


def find_private_dispatch_risks(
    source: str, rel_path: str, tree: ast.Module | None = None
) -> list[PrivateDispatch]:
    """Find every server-scoped private dispatch in one module source string."""
    tree = tree if tree is not None else ast.parse(source)
    nullable_helpers = _nullable_local_helpers(tree)
    found: list[PrivateDispatch] = []
    for func in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        is_private, why = _is_private_context(func, rel_path)
        if not is_private:
            continue
        parents = _build_parents(func)
        for call in _reachable_nodes(func):
            if not isinstance(call, ast.Call) or _call_name(call.func) != "dispatch":
                continue
            if not _event_server_scoped(func, call):
                continue
            uid = _kw(call, "user_ids")
            if uid is None:
                mode = "bare"
            elif _is_none_const(uid):
                mode = "literal-none"
            elif isinstance(uid, ast.Name):
                mode = "var"
            elif isinstance(uid, ast.Call) and _call_name(uid.func) in nullable_helpers:
                mode = "helper-call"
            else:
                mode = "explicit"
            outcome, detail = _classify(
                mode, uid, func, call, parents, nullable_helpers
            )
            snippet = (ast.get_source_segment(source, call) or "").strip()[:200]
            found.append(
                PrivateDispatch(
                    rel_path,
                    func.name,
                    call.lineno,
                    mode,
                    why,
                    outcome,
                    f"{detail} | {snippet}",
                )
            )
    return found


def _is_documented(
    risk: PrivateDispatch, allowlist=DOCUMENTED_PUBLIC_DISPATCHES
) -> bool:
    return any(
        entry.rel_path == risk.rel_path and entry.func == risk.func
        for entry in allowlist
    )


def _unexpected(risks, allowlist=DOCUMENTED_PUBLIC_DISPATCHES):
    return [
        r for r in risks if r.outcome == "fail_open" and not _is_documented(r, allowlist)
    ]


@lru_cache(maxsize=1)
def _whole_tree_risks() -> tuple[tuple[PrivateDispatch, ...], tuple[str, ...]]:
    risks: list[PrivateDispatch] = []
    files: list[str] = []
    for rel_path in _committed_dispatch_paths():
        source = _committed_source(rel_path)
        files.append(rel_path)
        risks.extend(find_private_dispatch_risks(source, rel_path, ast.parse(source)))
    return tuple(risks), tuple(files)


# --- Tests -----------------------------------------------------------------


def test_committed_tree_sweep_is_not_vacuous():
    """The sweep covers >100 modules and sees all four documented emitters."""
    assert len(_committed_python_paths()) > 100, "committed-tree scope regressed"
    risks, files = _whole_tree_risks()
    known_files = {path for path, _ in KNOWN_PRIVATE_EMITTERS}
    assert known_files <= set(files), f"dispatch-bearing files missed: {known_files - set(files)}"
    seen = {(r.rel_path, r.func) for r in risks}
    assert set(KNOWN_PRIVATE_EMITTERS) <= seen, (
        "detector went blind on documented private emitters: "
        f"{set(KNOWN_PRIVATE_EMITTERS) - seen}"
    )


def test_every_private_dispatch_fails_closed():
    """Core assertion: no private audience lookup may widen to server scope."""
    risks, _ = _whole_tree_risks()
    bad = _unexpected(risks)
    assert not bad, (
        "Private-event dispatch(es) can pass user_ids=None to the widening "
        "EventRouter:\n  "
        + "\n  ".join(
            f"{r.rel_path}:{r.line} {r.func} [{r.user_ids_mode}] {r.detail}"
            for r in bad
        )
        + "\nResolve an unverifiable audience to [] (or an explicit list), or "
        "record a documented public/no-content exception in "
        "DOCUMENTED_PUBLIC_DISPATCHES with a reason."
    )


@pytest.mark.parametrize(
    "rel_path,func",
    sorted(KNOWN_PRIVATE_EMITTERS),
)
def test_known_private_dispatch_is_enumerated_and_fails_closed(rel_path, func):
    """Each documented private emitter is seen, in scope, and fail-closed."""
    risks, _ = _whole_tree_risks()
    matches = [r for r in risks if r.rel_path == rel_path and r.func == func]
    assert matches, f"{rel_path}:{func} not enumerated by the detector"
    assert all(r.outcome == "fail_closed" for r in matches), (
        f"{rel_path}:{func} has a fail-open dispatch: "
        + "; ".join(r.detail for r in matches if r.outcome != "fail_closed")
    )


def test_documented_allowlist_entries_are_concrete():
    """Hygiene: every allowlist entry names a real file, function and reason."""
    seen: set[tuple[str, str]] = set()
    for entry in DOCUMENTED_PUBLIC_DISPATCHES:
        assert entry.rel_path in _committed_dispatch_paths() or entry.rel_path == "synthetic.py"
        assert isinstance(entry.func, str) and entry.func
        assert len(entry.why.strip()) > 25, entry.why
        key = (entry.rel_path, entry.func)
        assert key not in seen, f"duplicate allowlist entry: {key}"
        seen.add(key)


# --- Self-tests: the guard is non-vacuous ----------------------------------


def _synthetic_var_source(failure_assign: str, normalization: str = "") -> str:
    return (
        "def _emit_new_private(event, conv):\n"
        "    data = {'token': PRIVATE_TOKEN}\n"
        "    user_ids = None\n"
        "    try:\n"
        "        user_ids = get_participant_ids(conv)\n"
        "    except Exception:\n"
        f"        {failure_assign}\n"
        f"    {normalization}\n"
        "    events.dispatch(event, user_ids=user_ids, server_id=940001)\n"
    )


def test_detector_flags_bare_dispatch_on_lookup_failure():
    """A fresh bare `dispatch(event)` in a lookup-failure branch is flagged."""
    source = (
        "def _emit_new_private(event, conv):\n"
        "    data = {'token': PRIVATE_TOKEN}\n"
        "    try:\n"
        "        uid = get_participant_ids(conv)\n"
        "        events.dispatch(event, user_ids=[uid], server_id=940001)\n"
        "    except Exception:\n"
        "        events.dispatch(event, server_id=940001)\n"
    )
    risks = find_private_dispatch_risks(source, "synthetic.py")
    # The success-path call has an explicit list (fail-closed); only the
    # failure-path bare dispatch is flagged.
    flagged = [r for r in risks if r.outcome == "fail_open"]
    assert len(flagged) == 1, risks
    assert flagged[0].user_ids_mode == "bare"
    assert _unexpected(risks) == flagged


@pytest.mark.parametrize(
    "source",
    [
        (
            "def _emit_new_private(event, conv):\n"
            "    data = {'token': PRIVATE_TOKEN}\n"
            "    uid = get_participant_ids(conv)\n"
            "    events.dispatch(event, user_ids=None, server_id=940001)\n"
        ),
        _synthetic_var_source("user_ids = None"),
    ],
)
def test_detector_flags_none_at_dispatch(source):
    """Literal None and a failure-branch None assignment are both flagged."""
    risks = find_private_dispatch_risks(source, "synthetic.py")
    assert len(risks) == 1, risks
    assert risks[0].outcome == "fail_open", risks[0].detail


@pytest.mark.parametrize(
    "source",
    [
        _synthetic_var_source("user_ids = []"),
        _synthetic_var_source("pass", "if user_ids is None:\n        user_ids = []"),
        (
            "def _emit_new_private(event, conv):\n"
            "    data = {'token': PRIVATE_TOKEN}\n"
            "    uid = get_participant_ids(conv) or 0\n"
            "    events.dispatch(event, user_ids=[uid], server_id=940001)\n"
        ),
    ],
)
def test_detector_accepts_fail_closed_cases(source):
    """Non-vacuity: explicit []/lists and a None->[] normalization pass."""
    risks = find_private_dispatch_risks(source, "synthetic.py")
    assert len(risks) == 1, risks
    assert risks[0].outcome == "fail_closed", risks[0].detail
    assert _unexpected(risks) == []


def test_new_private_dispatch_fails_closed_by_default():
    """A brand-new private emitter with a fail-open audience is caught."""
    source = _synthetic_var_source("user_ids = None")
    risks = find_private_dispatch_risks(source, "src/core/brand_new_module.py")
    assert len(risks) == 1 and risks[0].outcome == "fail_open"
    # The whole-tree path would surface it through the same `_unexpected` check.
    assert _unexpected(risks) == risks


def test_public_no_content_dispatch_is_out_of_scope():
    """Public/no-content dispatches are excluded by construction, not by name."""
    source = (
        "def _emit_public_heartbeat(event):\n"
        "    events.dispatch(event, server_id=940001)\n"
    )
    assert find_private_dispatch_risks(source, "synthetic.py") == []


def test_allowlist_suppresses_only_its_own_pair():
    """A documented public exception hides only its own (path, function)."""
    entry = DocumentedPublicDispatch(
        "synthetic.py",
        "_emit_new_private",
        "Public heartbeat with no private payload; scope widening is intended.",
    )
    risks = find_private_dispatch_risks(
        _synthetic_var_source("user_ids = None"), entry.rel_path
    )
    assert risks and all(_is_documented(r, (entry,)) for r in risks)
    assert _unexpected(risks, (entry,)) == []
    assert _unexpected(risks, ()) == risks
