"""Invariant: every client-sendable, routed gateway opcode is throttled.

``INBOUND_RATE_LIMITS`` is the only per-connection frame-rate ceiling on the
inbound gateway path. An opcode that is routed to a heavy handler but missing
from that table silently re-opens unbounded DB write / SFU allocation / ordered
log fan-out amplification. Peers have already closed three individual gaps
(``VOICE_SPEAKING``, ``SUM_UNSUBSCRIBE``, ``ARTIFACT_UNSUBSCRIBE``); this suite
closes the *class* of bug instead of chasing one opcode at a time.

Unlike the manifest-based check, this guard enumerates the complete
``CLIENT_OPCODES`` declaration from the shipped source and requires every
member to be throttled unless it is in the documented allowlist below. Adding a
new client opcode therefore fails closed until it is either throttled or
explicitly justified as a cheap soft-reject.

Two independent throttle registration paths are read statically:

* the ``INBOUND_RATE_LIMITS`` dict literal in ``handlers/__init__.py``; and
* ``INBOUND_RATE_LIMITS.setdefault(...)`` calls in the package ``__init__.py``
  (this is where ``VOICE_SPEAKING`` is registered, so a guard that only reads
  the dict literal would miss it).

Nothing is imported from the live modules, so a monkey-patched table cannot
fool the check. Three self-tests prove the detector is non-vacuous: removing a
dict entry, removing the ``setdefault`` registration, and declaring a brand-new
unthrottled client opcode are each detected.
"""

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.websocket, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[3]

OPCODES_SRC = ROOT / "src" / "api" / "websocket" / "opcodes.py"
HANDLERS_SRC = ROOT / "src" / "api" / "websocket" / "handlers" / "__init__.py"
PACKAGE_SRC = ROOT / "src" / "api" / "websocket" / "__init__.py"

# Opcodes a client may send that are deliberately cheap: they have no
# server-side consumer and are SOFT-REJECTED (ignored + logged) before reaching
# any handler, so they need no per-connection ceiling. Each entry must also be
# present in ``IGNORED_OPCODES`` in the shipped handler source; that cross-check
# prevents a genuinely mutating opcode from being hidden here to silence the
# guard. Extension opcodes with no consumer yet fall in this bucket.
SOFT_REJECT_ALLOWLIST = {
    "SERVER_STATUS": "extension probe; no server-side consumer, ignored + logged",
    "VERSION_CHECK": "extension probe; no server-side consumer, ignored + logged",
    "INTERACTION_CREATE": "extension opcode; no server-side consumer, ignored + logged",
    "INTERACTION_RESPONSE": "extension opcode; no server-side consumer, ignored + logged",
    "RATCHET_UPDATE": "ratchet extension; no inbound handler, ignored + logged",
}


def _assignment_value(tree, name):
    """Return the value node assigned to ``name`` (Assign or AnnAssign)."""
    for node in ast.walk(tree):
        target = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target, value = node.targets[0].id, node.value
        if target == name and value is not None:
            return value
    raise AssertionError(f"no assignment to {name} found in source")


def _opcode_member(node):
    """Return the member name for ``GatewayOpcode.X`` or ``int(GatewayOpcode.X)``."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "int"
        and len(node.args) == 1
    ):
        node = node.args[0]
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "GatewayOpcode"
    ):
        return node.attr
    return None


def _enum_members(tree):
    """Return ``{GatewayOpcode member name: int value}`` from an opcodes tree."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "GatewayOpcode":
            members = {}
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                ):
                    try:
                        members[stmt.targets[0].id] = ast.literal_eval(stmt.value)
                    except (ValueError, TypeError):
                        pass
            return members
    raise AssertionError("GatewayOpcode enum not found in opcodes.py")


def _declared_set(tree, name):
    """Return the member names referenced in a declared frozenset of opcodes."""
    value = _assignment_value(tree, name)
    names = set()
    for node in ast.walk(value):
        member = _opcode_member(node)
        if member is not None:
            names.add(member)
    return names


def _dict_limits(tree):
    """Return ``{opcode name: (max_events, window)}`` from the handlers dict literal."""
    value = _assignment_value(tree, "INBOUND_RATE_LIMITS")
    assert isinstance(value, ast.Dict), "INBOUND_RATE_LIMITS must be a dict literal"
    limits = {}
    for key, val in zip(value.keys, value.values):
        member = _opcode_member(key)
        assert member is not None, f"unrecognised rate-limit key: {ast.unparse(key)}"
        limits[member] = ast.literal_eval(val)
    return limits


def _setdefault_limits(tree):
    """Return limits registered through ``INBOUND_RATE_LIMITS.setdefault(...)``."""
    limits = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setdefault"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "INBOUND_RATE_LIMITS"
            and len(node.args) >= 2
        ):
            continue
        member = _opcode_member(node.args[0])
        assert member is not None, (
            f"unrecognised setdefault key: {ast.unparse(node.args[0])}"
        )
        limits[member] = ast.literal_eval(node.args[1])
    return limits


def _effective_limits(handlers_tree, package_tree):
    """All limits actually enforced by the dispatcher, from both registration paths."""
    limits = _dict_limits(handlers_tree)
    limits.update(_setdefault_limits(package_tree))
    return limits


def _routed_opcodes(tree):
    """Return member names compared in the ``handle`` dispatch chain (``op == X``)."""
    routed = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "op"
        ):
            for operator, comparator in zip(node.ops, node.comparators):
                if isinstance(operator, ast.Eq):
                    member = _opcode_member(comparator)
                    if member is not None:
                        routed.add(member)
    return routed


def _missing_throttles(opcodes_tree, handlers_tree, package_tree):
    """Client opcodes that must be throttled but are not, as member names."""
    client = _declared_set(opcodes_tree, "CLIENT_OPCODES")
    must_throttle = client - set(SOFT_REJECT_ALLOWLIST)
    return must_throttle - _effective_limits(handlers_tree, package_tree).keys()


def _load_sources():
    """Read and parse each gateway source exactly once.

    Returns ``((opcodes, handlers, package) texts, (..) trees)``. Every detector
    works on the shared trees, so the many real-tree assertions -- including the
    per-opcode parametrization -- add no reads or parses of their own. Self-tests
    that exercise synthetic regressions parse their snippet once and pass that
    tree explicitly.
    """
    texts = tuple(
        src.read_text(encoding="utf-8")
        for src in (OPCODES_SRC, HANDLERS_SRC, PACKAGE_SRC)
    )
    trees = tuple(ast.parse(text) for text in texts)
    return texts, trees


(OPCODES_TEXT, HANDLERS_TEXT, PACKAGE_TEXT), (
    OPCODES_TREE,
    HANDLERS_TREE,
    PACKAGE_TREE,
) = _load_sources()

MUST_THROTTLE = _declared_set(OPCODES_TREE, "CLIENT_OPCODES") - set(
    SOFT_REJECT_ALLOWLIST
)


def test_allowlist_is_documented_and_genuinely_soft_rejected():
    """Allowlist entries must be real client opcodes that are actually ignored."""
    client = _declared_set(OPCODES_TREE, "CLIENT_OPCODES")
    ignored = _declared_set(HANDLERS_TREE, "IGNORED_OPCODES")
    allowlisted = set(SOFT_REJECT_ALLOWLIST)

    assert allowlisted <= client, (
        f"allowlist names that are not client-sendable: {allowlisted - client}"
    )
    assert allowlisted <= ignored, (
        "allowlisted opcodes are not soft-rejected (they reach a handler or are "
        f"unknown): {sorted(allowlisted - ignored)}"
    )
    assert all(SOFT_REJECT_ALLOWLIST[name] for name in allowlisted), (
        "every allowlist entry needs a written justification"
    )
    # A newly soft-rejected client opcode must be added to the documented list
    # rather than quietly escaping the throttle enumeration.
    assert (client & ignored) <= allowlisted, (
        "soft-rejected client opcodes missing from SOFT_REJECT_ALLOWLIST: "
        f"{sorted((client & ignored) - allowlisted)}"
    )


def test_every_non_allowlisted_client_opcode_is_throttled():
    """Core invariant: the whole client opcode surface is covered or allowlisted."""
    missing = _missing_throttles(OPCODES_TREE, HANDLERS_TREE, PACKAGE_TREE)
    assert not missing, (
        "client-sendable opcodes with no inbound throttle and no documented "
        f"soft-reject allowlist entry: {sorted(missing)}"
    )


def test_every_routed_opcode_is_throttled():
    """Nothing that reaches a dispatch handler may be unthrottled."""
    routed = _routed_opcodes(HANDLERS_TREE)
    client = _declared_set(OPCODES_TREE, "CLIENT_OPCODES")
    limits = _effective_limits(HANDLERS_TREE, PACKAGE_TREE)

    assert routed <= client, (
        f"routed opcodes missing from CLIENT_OPCODES: {routed - client}"
    )
    assert routed <= limits.keys(), (
        f"routed opcodes missing from INBOUND_RATE_LIMITS: {sorted(routed - limits.keys())}"
    )
    assert not routed & SOFT_REJECT_ALLOWLIST.keys(), (
        f"allowlisted soft-reject opcodes must not be routed: "
        f"{sorted(routed & SOFT_REJECT_ALLOWLIST.keys())}"
    )


def test_throttle_keys_are_known_client_opcodes_with_sane_budgets():
    """Both registration paths may only throttle known opcodes with sane budgets."""
    members = _enum_members(OPCODES_TREE)
    client = _declared_set(OPCODES_TREE, "CLIENT_OPCODES")
    limits = _effective_limits(HANDLERS_TREE, PACKAGE_TREE)

    assert limits.keys() <= members.keys(), (
        f"unknown opcodes in throttle table: {sorted(limits.keys() - members.keys())}"
    )
    assert limits.keys() <= client, (
        f"non-client opcodes in throttle table: {sorted(limits.keys() - client)}"
    )
    for name, budget in limits.items():
        assert isinstance(budget, tuple) and len(budget) == 2, (name, budget)
        max_events, window = budget
        assert isinstance(max_events, int) and not isinstance(max_events, bool), (
            name,
            budget,
        )
        assert isinstance(window, (int, float)) and not isinstance(window, bool), (
            name,
            budget,
        )
        assert max_events > 0 and window > 0, (name, budget)


@pytest.mark.parametrize("name", sorted(MUST_THROTTLE))
def test_each_required_opcode_individually_covered(name):
    """Per-opcode reporting pinpoints the exact gap on a regression."""
    limits = _effective_limits(HANDLERS_TREE, PACKAGE_TREE)
    assert name in limits, f"{name} is client-sendable, routed-capable and unthrottled"


def test_self_test_removed_dict_literal_entry_is_flagged():
    """Non-vacuity: deleting a throttle from the dict literal is detected."""
    victim = "PRESENCE_UPDATE"
    assert victim in _dict_limits(HANDLERS_TREE)
    lines = HANDLERS_TEXT.splitlines(keepends=True)
    pruned = [line for line in lines if f"int(GatewayOpcode.{victim}):" not in line]
    assert len(pruned) == len(lines) - 1, "self-test fixture did not remove one entry"
    synthetic = "".join(pruned)
    synthetic_tree = ast.parse(synthetic)  # still valid source
    assert victim not in _dict_limits(synthetic_tree)
    assert victim in _missing_throttles(OPCODES_TREE, synthetic_tree, PACKAGE_TREE), (
        "detector failed to notice the removed dict-literal throttle"
    )


def test_self_test_removed_setdefault_registration_is_flagged():
    """Non-vacuity: deleting a setdefault-only throttle (VOICE_SPEAKING) is detected."""
    victim = "VOICE_SPEAKING"
    # It lives only in the package init path -- a dict-literal-only guard misses it.
    assert victim not in _dict_limits(HANDLERS_TREE)
    assert victim in _setdefault_limits(PACKAGE_TREE)
    lines = PACKAGE_TEXT.splitlines(keepends=True)
    pruned = [
        line for line in lines if f"setdefault(int(GatewayOpcode.{victim})" not in line
    ]
    assert len(pruned) == len(lines) - 1, "self-test fixture did not remove one line"
    synthetic = "".join(pruned)
    synthetic_tree = ast.parse(synthetic)
    assert victim not in _setdefault_limits(synthetic_tree)
    assert victim in _missing_throttles(OPCODES_TREE, HANDLERS_TREE, synthetic_tree), (
        "detector failed to notice the removed setdefault throttle"
    )


def test_self_test_new_unthrottled_client_opcode_is_flagged():
    """Non-vacuity: a newly declared, unthrottled client opcode is detected."""
    new_member = "FUTURE_HEAVY"
    assert new_member not in _enum_members(OPCODES_TREE)

    synthetic = OPCODES_TEXT.replace(
        "    SUM_OP = 66\n",
        f"    SUM_OP = 66\n    {new_member} = 69\n",
        1,
    ).replace(
        "        GatewayOpcode.SUM_OP,\n",
        f"        GatewayOpcode.SUM_OP,\n        GatewayOpcode.{new_member},\n",
        1,
    )
    assert synthetic != OPCODES_TEXT
    synthetic_tree = ast.parse(synthetic)
    assert new_member in _declared_set(synthetic_tree, "CLIENT_OPCODES")
    assert new_member in _missing_throttles(
        synthetic_tree, HANDLERS_TREE, PACKAGE_TREE
    ), "detector failed to notice a newly declared unthrottled client opcode"


def test_sources_are_read_and_parsed_once(monkeypatch):
    """Perf regression: each gateway source is read and AST-parsed once.

    Previously every detector re-parsed the same source text on each call, and
    the per-opcode parametrized test re-parsed both large handler sources once
    per opcode (21x). Now ``_load_sources`` reads + parses each file exactly once
    and every detector reuses those trees. Counting reads/parses pins this
    deterministically -- no wall-clock timing and no flaky threshold.
    """
    reads = []
    parses = []
    real_read_text = Path.read_text
    real_parse = ast.parse

    def counting_read_text(self, *args, **kwargs):
        reads.append(self)
        return real_read_text(self, *args, **kwargs)

    def counting_parse(*args, **kwargs):
        parses.append(args[0] if args else None)
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    monkeypatch.setattr(ast, "parse", counting_parse)

    (opcodes_text, handlers_text, package_text), (
        opcodes_tree,
        handlers_tree,
        package_tree,
    ) = _load_sources()

    assert reads == [OPCODES_SRC, HANDLERS_SRC, PACKAGE_SRC]
    assert len(parses) == 3
    assert parses == [opcodes_text, handlers_text, package_text]

    # Every real-tree detector must reuse the pre-built trees: no extra reads
    # or parses, however many assertions/parameters call them.
    _declared_set(opcodes_tree, "CLIENT_OPCODES")
    _declared_set(handlers_tree, "IGNORED_OPCODES")
    _enum_members(opcodes_tree)
    _routed_opcodes(handlers_tree)
    _effective_limits(handlers_tree, package_tree)
    _missing_throttles(opcodes_tree, handlers_tree, package_tree)

    assert reads == [OPCODES_SRC, HANDLERS_SRC, PACKAGE_SRC]
    assert len(parses) == 3, "a detector re-parsed an already-parsed source"
