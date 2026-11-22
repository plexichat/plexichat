"""Static guard: unwired-dependency gates in routes/core must fail closed.

The unwired-dependency campaign fixed ~30 sites of the same shape::

    servers_mod = api.get_servers()          # or an injected ``servers`` arg
    if not servers_mod:
        return                               # skip the check -> grant

When the servers module is unwired, a ``srv_channels.conversation_id`` alias can
still exist in the database. Skipping the backing-channel lookup therefore let
a caller act on a private channel whose ``channels.view`` / ``messages.send``
grant was never verified. Peers replaced each skip with a denial (raise / 403 /
404 / ``return False``); the same theme recurs often enough to need a structural
lock rather than one regression test per site.

This guard parses, with :mod:`ast`, every ``_require_*`` / ``_can_*`` /
``_check_*`` gate in ``src/api/routes/**`` and ``src/core/**`` that is wired to
a module getter (``api.get_servers()`` and siblings) or to an injected module
argument, finds every unwired branch (``if not <mod>`` / ``if <mod> is None``),
and classifies its outcome:

  * DENY     - raises, or returns a falsy/empty outcome (``return False``,
               ``return None``, ``_deny()``, ...);
  * ALLOW    - bare ``return``, ``return True``, or any truthy/allow outcome;
  * UNKNOWN  - anything the classifier cannot prove denies.

Only DENY is acceptable. ALLOW/UNKNOWN fails the suite unless the exact
``(rel_path, func)`` pair is in :data:`DOCUMENTED_NON_AUTH_GATES` with a written
reason (documented non-authorization gates only: spam protection, best-effort
delivery/cleanup). A NEW gate under a novel name is seen by the whole-tree
sweep and cannot pass just because nobody added it to a per-site marker list.

Why the committed tree
----------------------
The relevant fixes are committed at ``HEAD`` while some peers still hold stale
working-tree copies (the companion committed-tree guard documents this). A
working-tree scan would report those stale copies as fresh fail-open gates and
make this guard fail spuriously in this workspace, while a committed-tree scan
polices exactly the code that can be merged. ``HEAD`` is resolved once per
session and every blob is read with ``git show``; in CI ``HEAD`` is the checked
out tree, so the two coincide.

Non-vacuity
-----------
The sweep must see the documented gates and scan >100 modules. Injected
sources prove that an unwired ``return`` / ``return True`` gate is flagged,
that an unwired ``raise`` / ``return False`` gate is not, and that a documented
allowlist entry suppresses only its own ``(path, func)`` pair. Nothing is
written to disk; all mutations are in-memory strings.
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

# Gate functions this guard enumerates. A brand-new gate under a novel name
# with one of these prefixes is picked up automatically by the whole-tree sweep.
GATE_PREFIXES = ("_require_", "_can_", "_check_")

# ``api.get_servers()`` and the equivalent injected-facade getters used across
# core services. Suffix/pattern forms catch sibling getters under novel names.
_GETTER_NAMES = frozenset({"get_servers", "get_servers_module", "_get_servers_module"})

# Names used for the injected module argument in fixed gates (and plausible
# names in future ones). Kept module-specific to avoid sweeping unrelated
# ``if not x`` checks in ordinary helpers.
_MODULE_NAMES = frozenset(
    {
        "servers",
        "servers_mod",
        "servers_module",
        "server_module",
        "srv",
        "srv_mod",
        "servers_store",
        "mod",
        "module",
    }
)

# Calls that deny outright even though they are calls, not ``raise`` statements
# (e.g. the routes' local ``_deny()`` helper or ``raise_mapped(exc)``).
_DENY_CALL_TOKENS = ("deny", "forbid", "abort", "reject", "not_found")
_EMPTY_CONSTRUCTORS = frozenset({"set", "dict", "list", "frozenset"})


@dataclass(frozen=True)
class UnwiredGate:
    """One unwired branch inside an in-scope gate function."""

    rel_path: str
    func: str
    line: int
    mod: str
    outcome: str  # "deny" | "allow" | "unknown"
    evidence: str


@dataclass(frozen=True)
class NonAuthGate:
    """A reviewed non-authorization gate allowed to fail open when unwired."""

    rel_path: str
    func: str
    why: str


# Only documented non-auth gates belong here, with an explicit (path, function)
# pair and a reason. None exist today: every in-scope unwired branch at HEAD
# denies. Add an entry ONLY for a gate whose unwired branch is provably not an
# authorization decision (spam protection, best-effort delivery/cleanup) and
# record why an outage there is preferable to a deny-all. An unregistered
# unwired-ALLOW gate is a failure by design -- there is no catch-all allowlist.
DOCUMENTED_NON_AUTH_GATES: tuple[NonAuthGate, ...] = ()


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
    paths: list[str] = []
    for raw in _git("ls-tree", "-r", "--name-only", _head()).splitlines():
        if not raw.endswith(".py"):
            continue
        if not (raw.startswith("src/api/routes/") or raw.startswith("src/core/")):
            continue
        if "/selftest/" in raw:
            continue  # selftest scaffolding is not production code
        paths.append(raw)
    return tuple(paths)


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


def _name_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_getter_name(name: str) -> bool:
    return (
        name in _GETTER_NAMES
        or (name.startswith("get_") and "server" in name)
        or (name.startswith("_get_") and name.endswith("_module"))
    )


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


def _is_attributed_gate(func: ast.AST) -> bool:
    """A gate wired to a module getter or to an injected module argument."""
    for node in _reachable_nodes(func):
        if isinstance(node, ast.Call) and _is_getter_name(_call_name(node.func)):
            return True
    params = {a.arg for a in func.args.args + func.args.kwonlyargs}
    if func.args.vararg and func.args.vararg.arg in _MODULE_NAMES:
        return True
    return bool(params & _MODULE_NAMES)


def _unwired_module(test: ast.AST) -> str | None:
    """Return the module name when ``test`` is ``not <mod>`` / ``<mod> is None``."""
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        for value in test.values:
            name = _unwired_module(value)
            if name is not None:
                return name
        return None
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _name_of(test.operand)
    if isinstance(test, ast.Compare):
        left = _name_of(test.left)
        for op, comparator in zip(test.ops, test.comparators):
            if (
                isinstance(op, (ast.Is, ast.Eq))
                and isinstance(comparator, ast.Constant)
                and comparator.value is None
            ):
                return left
    return None


def _is_nonempty_container(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return bool(node.elts)
    if isinstance(node, ast.Dict):
        return bool(node.keys)
    return False


def _is_deny_value(node: ast.AST) -> bool:
    """True for a falsy/deny return value (``False``, ``None``, ``_deny()``...)."""
    if isinstance(node, ast.Constant) and node.value in (None, False, 0, 0.0, ""):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        return not _is_nonempty_container(node)
    if isinstance(node, ast.Call):
        name = _call_name(node.func).lower()
        return any(token in name for token in _DENY_CALL_TOKENS) or (
            name in _EMPTY_CONSTRUCTORS and not node.args and not node.keywords
        )
    return False


def _is_allow_value(node: ast.AST) -> bool:
    """True for an explicitly truthy/allow return value (``True``, ``1``, ...)."""
    if isinstance(node, ast.Constant):
        return bool(node.value)
    return _is_nonempty_container(node)


def _branch_outcome(branch: ast.If) -> str:
    """Classify an unwired branch: ``deny``, ``allow``, or ``unknown``."""
    allows = 0
    denies = 0
    for node in _reachable_nodes(branch):
        if isinstance(node, ast.Return):
            if node.value is None:
                allows += 1  # bare `return` -> skip the gate -> grant
            elif _is_deny_value(node.value):
                denies += 1
            elif _is_allow_value(node.value):
                allows += 1
            else:
                allows += 1  # unprovable return -> fail closed
        elif isinstance(node, ast.Raise):
            denies += 1
        elif isinstance(node, ast.Call):
            if any(
                token in _call_name(node.func).lower() for token in _DENY_CALL_TOKENS
            ):
                denies += 1
    # Any allow-shaped exit wins: an ambiguous branch is not a proven denial.
    if allows:
        return "allow"
    if denies:
        return "deny"
    return "unknown"


def find_unwired_gates(
    source: str, rel_path: str, tree: ast.Module | None = None
) -> list[UnwiredGate]:
    """Find unwired branches in in-scope gates within one module source string."""
    tree = tree if tree is not None else ast.parse(source)
    found: list[UnwiredGate] = []
    for func in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        if not func.name.startswith(GATE_PREFIXES):
            continue
        if not _is_attributed_gate(func):
            continue
        for node in _reachable_nodes(func):
            if not isinstance(node, ast.If):
                continue
            mod = _unwired_module(node.test)
            if mod not in _MODULE_NAMES:
                continue
            evidence = (ast.get_source_segment(source, node) or "").strip()
            found.append(
                UnwiredGate(
                    rel_path,
                    func.name,
                    node.lineno,
                    mod,
                    _branch_outcome(node),
                    evidence[:160],
                )
            )
    return found


def _is_documented(gate: UnwiredGate, allowlist=DOCUMENTED_NON_AUTH_GATES) -> bool:
    return any(
        entry.rel_path == gate.rel_path and entry.func == gate.func
        for entry in allowlist
    )


def _unexpected(gates, allowlist=DOCUMENTED_NON_AUTH_GATES):
    """Gates the guard would fail on: not proven deny and not documented."""
    return [
        g for g in gates if g.outcome != "deny" and not _is_documented(g, allowlist)
    ]


@lru_cache(maxsize=1)
def _whole_tree_gates() -> tuple[tuple[UnwiredGate, ...], int]:
    """Sweep every committed production module in the two scoped trees."""
    gates: list[UnwiredGate] = []
    scanned = 0
    for rel_path in _committed_python_paths():
        source = _committed_source(rel_path)
        tree = ast.parse(source)
        scanned += 1
        gates.extend(find_unwired_gates(source, rel_path, tree))
    return tuple(gates), scanned


# --- Tests -----------------------------------------------------------------


def test_sweep_is_not_vacuous():
    """The sweep must actually scan the trees and see the fixed gates."""
    gates, scanned = _whole_tree_gates()
    assert scanned > 100, (
        f"committed-tree sweep parsed only {scanned} modules; the git "
        "prefilter/path scope regressed"
    )
    assert len(gates) >= 20, (
        f"sweep found only {len(gates)} unwired gates; the gate/getter "
        "attribution rules have gone blind"
    )
    seen = {(g.rel_path, g.func) for g in gates}
    # A representative fixed gate must still be seen (prevents silent scope loss).
    assert ("src/api/routes/reactions.py", "_require_channel_view") in seen


def test_every_unwired_branch_denies():
    """Core assertion: no unwired branch may skip a gate, unless documented."""
    gates, _ = _whole_tree_gates()
    unexpected = _unexpected(gates)
    assert not unexpected, (
        "Unwired-dependency gate(s) do not provably deny:\n  "
        + "\n  ".join(
            f"{g.rel_path}:{g.line} {g.func}[{g.mod}] -> {g.outcome}: {g.evidence}"
            for g in unexpected
        )
        + "\nMake the unwired branch deny (raise / return False / return an "
        "error), or record a documented non-auth exception in "
        "DOCUMENTED_NON_AUTH_GATES with an explicit (path, function) reason."
    )


def test_documented_allowlist_entries_are_concrete():
    """Hygiene: every allowlist entry names a real scoped file, gate and reason."""
    seen: set[tuple[str, str]] = set()
    for entry in DOCUMENTED_NON_AUTH_GATES:
        assert entry.rel_path.startswith(("src/api/routes/", "src/core/"))
        assert _committed_source(entry.rel_path)  # path must exist at HEAD
        assert entry.func.startswith(GATE_PREFIXES)
        assert len(entry.why.strip()) > 25, entry.why
        key = (entry.rel_path, entry.func)
        assert key not in seen, f"duplicate allowlist entry: {key}"
        seen.add(key)


# --- Self-tests: the guard is non-vacuous ----------------------------------


def _synthetic_source(body: str, name: str = "_require_new_gate") -> str:
    """A gate module with an unwired branch whose outcome is ``body``."""
    return (
        "import src.api as api\n"
        f"def {name}(servers_mod):\n"
        "    if not servers_mod:\n"
        f"        {body}\n"
        "    do_privileged_work()\n"
    )


@pytest.mark.parametrize(
    "allow_body",
    ["return", "return True", "return 1", "return ['ok']"],
)
def test_detector_flags_unwired_allow_gates(allow_body):
    """Non-vacuity: a fresh unwired-ALLOW gate under a novel name is flagged."""
    findings = find_unwired_gates(_synthetic_source(allow_body), "synthetic.py")
    assert len(findings) == 1
    assert findings[0].outcome == "allow", allow_body
    assert findings[0].func == "_require_new_gate"


def test_detector_fails_closed_on_unprovable_outcome():
    """An unfamiliar unwired exit is UNKNOWN and still fails the guard."""
    findings = find_unwired_gates(
        _synthetic_source("call_unknown_gate()"), "synthetic.py"
    )
    assert len(findings) == 1
    assert findings[0].outcome == "unknown"
    assert _unexpected(findings) == findings


@pytest.mark.parametrize(
    "deny_body",
    [
        "raise PermissionError('unwired')",
        "return False",
        "return None",
        "return []",
        "_deny()",
    ],
)
def test_detector_accepts_unwired_deny_gates(deny_body):
    """Non-vacuity: a fail-closed unwired branch is not mistaken for a grant."""
    findings = find_unwired_gates(_synthetic_source(deny_body), "synthetic.py")
    assert len(findings) == 1
    assert findings[0].outcome == "deny", deny_body


def test_detector_flags_getter_attributed_and_is_none_shapes():
    """A gate with no module parameter but an ``api.get_servers()`` getter, and
    an ``if <mod> is None`` branch, are both in scope."""
    source = (
        "import src.api as api\n"
        "def _check_novel_gate(user_id):\n"
        "    servers = api.get_servers()\n"
        "    if servers is None:\n"
        "        return\n"
        "    do_privileged_work()\n"
    )
    findings = find_unwired_gates(source, "synthetic.py")
    assert [(f.func, f.mod, f.outcome) for f in findings] == [
        ("_check_novel_gate", "servers", "allow")
    ]


def test_documented_allowlist_suppresses_only_its_own_pair():
    """A documented entry is not flagged; a novel name still fails closed."""
    documented = NonAuthGate(
        "synthetic.py",
        "_require_spam_protection",
        "Best-effort spam-protection lookup: when unwired, the request is "
        "graded by the slower authoritative path rather than dropped.",
    )
    registered = find_unwired_gates(
        _synthetic_source("return", documented.func), documented.rel_path
    )
    assert registered and all(_is_documented(g, (documented,)) for g in registered)
    # The documented pair is suppressed only when actually registered.
    assert _unexpected(registered, (documented,)) == []
    assert _unexpected(registered, ()) == registered

    # A novel gate name with the same unwired-ALLOW shape is still a failure.
    novel = find_unwired_gates(
        _synthetic_source("return", "_require_new_gate"), "synthetic.py"
    )
    assert len(novel) == 1 and not _is_documented(novel[0], (documented,))
    assert _unexpected(novel, (documented,)) == novel
