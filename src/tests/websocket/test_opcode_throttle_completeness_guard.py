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


def _enum_members(source):
    """Return ``{GatewayOpcode member name: int value}`` from opcodes.py text."""
    for node in ast.walk(ast.parse(source)):
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


def _declared_set(source, name):
    """Return the member names referenced in a declared frozenset of opcodes."""
    value = _assignment_value(ast.parse(source), name)
    names = set()
    for node in ast.walk(value):
        member = _opcode_member(node)
        if member is not None:
            names.add(member)
    return names


def _dict_limits(source):
    """Return ``{opcode name: (max_events, window)}`` from the handlers dict literal."""
    value = _assignment_value(ast.parse(source), "INBOUND_RATE_LIMITS")
    assert isinstance(value, ast.Dict), "INBOUND_RATE_LIMITS must be a dict literal"
    limits = {}
    for key, val in zip(value.keys, value.values):
        member = _opcode_member(key)
        assert member is not None, f"unrecognised rate-limit key: {ast.unparse(key)}"
        limits[member] = ast.literal_eval(val)
    return limits


def _setdefault_limits(source):
    """Return limits registered through ``INBOUND_RATE_LIMITS.setdefault(...)``."""
    limits = {}
    for node in ast.walk(ast.parse(source)):
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


def _effective_limits(handlers_source, package_source):
    """All limits actually enforced by the dispatcher, from both registration paths."""
    limits = _dict_limits(handlers_source)
    limits.update(_setdefault_limits(package_source))
    return limits


def _routed_opcodes(source):
    """Return member names compared in the ``handle`` dispatch chain (``op == X``)."""
    routed = set()
    for node in ast.walk(ast.parse(source)):
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


def _missing_throttles(opcodes_source, handlers_source, package_source):
    """Client opcodes that must be throttled but are not, as member names."""
    client = _declared_set(opcodes_source, "CLIENT_OPCODES")
    must_throttle = client - set(SOFT_REJECT_ALLOWLIST)
    return must_throttle - _effective_limits(handlers_source, package_source).keys()


OPCODES_TEXT = OPCODES_SRC.read_text(encoding="utf-8")
HANDLERS_TEXT = HANDLERS_SRC.read_text(encoding="utf-8")
PACKAGE_TEXT = PACKAGE_SRC.read_text(encoding="utf-8")

MUST_THROTTLE = _declared_set(OPCODES_TEXT, "CLIENT_OPCODES") - set(
    SOFT_REJECT_ALLOWLIST
)


def test_allowlist_is_documented_and_genuinely_soft_rejected():
    """Allowlist entries must be real client opcodes that are actually ignored."""
    client = _declared_set(OPCODES_TEXT, "CLIENT_OPCODES")
    ignored = _declared_set(HANDLERS_TEXT, "IGNORED_OPCODES")
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
    missing = _missing_throttles(OPCODES_TEXT, HANDLERS_TEXT, PACKAGE_TEXT)
    assert not missing, (
        "client-sendable opcodes with no inbound throttle and no documented "
        f"soft-reject allowlist entry: {sorted(missing)}"
    )


def test_every_routed_opcode_is_throttled():
    """Nothing that reaches a dispatch handler may be unthrottled."""
    routed = _routed_opcodes(HANDLERS_TEXT)
    client = _declared_set(OPCODES_TEXT, "CLIENT_OPCODES")
    limits = _effective_limits(HANDLERS_TEXT, PACKAGE_TEXT)

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
    members = _enum_members(OPCODES_TEXT)
    client = _declared_set(OPCODES_TEXT, "CLIENT_OPCODES")
    limits = _effective_limits(HANDLERS_TEXT, PACKAGE_TEXT)

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
    limits = _effective_limits(HANDLERS_TEXT, PACKAGE_TEXT)
    assert name in limits, f"{name} is client-sendable, routed-capable and unthrottled"


def test_self_test_removed_dict_literal_entry_is_flagged():
    """Non-vacuity: deleting a throttle from the dict literal is detected."""
    victim = "PRESENCE_UPDATE"
    assert victim in _dict_limits(HANDLERS_TEXT)
    lines = HANDLERS_TEXT.splitlines(keepends=True)
    pruned = [line for line in lines if f"int(GatewayOpcode.{victim}):" not in line]
    assert len(pruned) == len(lines) - 1, "self-test fixture did not remove one entry"
    synthetic = "".join(pruned)
    ast.parse(synthetic)  # still valid source
    assert victim not in _dict_limits(synthetic)
    assert victim in _missing_throttles(OPCODES_TEXT, synthetic, PACKAGE_TEXT), (
        "detector failed to notice the removed dict-literal throttle"
    )


def test_self_test_removed_setdefault_registration_is_flagged():
    """Non-vacuity: deleting a setdefault-only throttle (VOICE_SPEAKING) is detected."""
    victim = "VOICE_SPEAKING"
    # It lives only in the package init path -- a dict-literal-only guard misses it.
    assert victim not in _dict_limits(HANDLERS_TEXT)
    assert victim in _setdefault_limits(PACKAGE_TEXT)
    lines = PACKAGE_TEXT.splitlines(keepends=True)
    pruned = [
        line for line in lines if f"setdefault(int(GatewayOpcode.{victim})" not in line
    ]
    assert len(pruned) == len(lines) - 1, "self-test fixture did not remove one line"
    synthetic = "".join(pruned)
    ast.parse(synthetic)
    assert victim not in _setdefault_limits(synthetic)
    assert victim in _missing_throttles(OPCODES_TEXT, HANDLERS_TEXT, synthetic), (
        "detector failed to notice the removed setdefault throttle"
    )


def test_self_test_new_unthrottled_client_opcode_is_flagged():
    """Non-vacuity: a newly declared, unthrottled client opcode is detected."""
    new_member = "FUTURE_HEAVY"
    assert new_member not in _enum_members(OPCODES_TEXT)

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
    ast.parse(synthetic)
    assert new_member in _declared_set(synthetic, "CLIENT_OPCODES")
    assert new_member in _missing_throttles(synthetic, HANDLERS_TEXT, PACKAGE_TEXT), (
        "detector failed to notice a newly declared unthrottled client opcode"
    )
