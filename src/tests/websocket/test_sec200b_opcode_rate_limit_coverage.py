"""SEC200b invariant: every expensive inbound gateway opcode is throttled.

The dispatcher's ``INBOUND_RATE_LIMITS`` table is the only thing standing
between a malicious client and unbounded DB writes / SFU allocations / ordered
log fan-out. A new expensive opcode that is routed but *not* added to that
table silently re-opens the amplification bug, so this suite checks the table
against the protocol declarations with a static (AST) read of the shipped
source.

Three independent facts are pinned:

* the security contract's list of expensive opcode names (the manifest) all
  exist in ``GatewayOpcode`` and are client-sendable;
* every one of them is a key in the real ``INBOUND_RATE_LIMITS`` table;
* every key in that table is a *known* opcode and carries a sane
  ``(max_events, window)`` budget (positive count, positive window).

Nothing is imported from the live modules, so the check reads exactly the code
that ships and cannot be fooled by a monkey-patched table. A detector self-test
feeds a synthetic table with one expensive opcode missing and proves the scan
notices it.
"""

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.websocket, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[3]

OPCODES_SRC = ROOT / "src" / "api" / "websocket" / "opcodes.py"
HANDLERS_SRC = ROOT / "src" / "api" / "websocket" / "handlers" / "__init__.py"

# The expensive opcodes, by enum member name. Each one mutates server state or
# allocates a resource and fans out, so each must be present in the throttle
# table. Kept explicit on purpose so it is a reviewable security contract and a
# newly added expensive opcode has to be declared here (and throttled) rather
# than quietly slipping through.
EXPENSIVE_OPCODES = frozenset(
    {
        "HEARTBEAT",  # liveness bookkeeping on every frame
        "PRESENCE_UPDATE",  # DB write + friend/server fan-out
        "TYPING_START",  # DB write + fan-out
        "REQUEST_GUILD_MEMBERS",  # one threadpool DB lookup per member
        "VOICE_CONNECT",  # allocates SFU room + signed media tokens
        "VOICE_SDP_OFFER",  # SFU negotiation state
        "VOICE_SDP_ANSWER",  # SFU negotiation state
        "VOICE_ICE_CANDIDATE",  # trickle ICE peer state (high burst ceiling)
        "ARTIFACT_SUBSCRIBE",  # rebuilds a 500-op replay snapshot per frame
        "ARTIFACT_OP",  # ordered ops-log append + fan-out
        "SUM_SUBSCRIBE",  # workbook replay snapshot per frame
        "SUM_OP",  # ordered ops-log append + fan-out
    }
)


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
    """Return member names referenced in a ``frozenset({GatewayOpcode.X,...})``."""
    value = _assignment_value(tree, name)
    return {
        node.attr
        for node in ast.walk(value)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "GatewayOpcode"
    }


def _key_name(key):
    """Unwrap ``int(GatewayOpcode.NAME)`` dict keys to ``NAME``."""
    if (
        isinstance(key, ast.Call)
        and isinstance(key.func, ast.Name)
        and key.func.id == "int"
        and key.args
    ):
        arg = key.args[0]
        if (
            isinstance(arg, ast.Attribute)
            and isinstance(arg.value, ast.Name)
            and arg.value.id == "GatewayOpcode"
        ):
            return arg.attr
    return None


def _rate_limits(tree):
    """Return ``{opcode name: (max_events, window)}`` from a handlers tree."""
    value = _assignment_value(tree, "INBOUND_RATE_LIMITS")
    assert isinstance(value, ast.Dict), "INBOUND_RATE_LIMITS must be a dict literal"
    limits = {}
    for key, val in zip(value.keys, value.values):
        name = _key_name(key)
        assert name is not None, f"unrecognised rate-limit key: {ast.unparse(key)}"
        limits[name] = ast.literal_eval(val)
    return limits


def _load_sources():
    """Read and parse each gateway source exactly once.

    Returns ``((opcodes, handlers) texts, (opcodes, handlers) trees)``. Every
    detector works on the shared trees, so the many real-tree assertions --
    including the per-opcode parametrization -- add no reads or parses of their
    own. Self-tests that feed a synthetic table parse their snippet once and
    pass that tree explicitly.
    """
    texts = tuple(
        src.read_text(encoding="utf-8") for src in (OPCODES_SRC, HANDLERS_SRC)
    )
    trees = tuple(ast.parse(text) for text in texts)
    return texts, trees


(OPCODES_TEXT, HANDLERS_TEXT), (OPCODES_TREE, HANDLERS_TREE) = _load_sources()


def test_manifest_is_real_and_client_sendable():
    """Every declared expensive opcode exists in the enum and is client-sendable."""
    members = _enum_members(OPCODES_TREE)
    client = _declared_set(OPCODES_TREE, "CLIENT_OPCODES")
    assert EXPENSIVE_OPCODES <= members.keys(), (
        f"manifest names missing from GatewayOpcode: {EXPENSIVE_OPCODES - members.keys()}"
    )
    assert EXPENSIVE_OPCODES <= client, (
        f"expensive opcodes that are no longer client-sendable: {EXPENSIVE_OPCODES - client}"
    )


def test_every_expensive_opcode_is_rate_limited():
    """The core invariant: the shipped throttle table covers the whole manifest."""
    limits = _rate_limits(HANDLERS_TREE)
    missing = EXPENSIVE_OPCODES - limits.keys()
    assert not missing, (
        f"expensive opcodes missing from INBOUND_RATE_LIMITS: {sorted(missing)}"
    )


def test_rate_limit_table_has_no_stale_opcodes_and_sane_budgets():
    limits = _rate_limits(HANDLERS_TREE)
    members = _enum_members(OPCODES_TREE)
    assert limits.keys() <= members.keys(), (
        f"stale opcode keys in INBOUND_RATE_LIMITS: {sorted(limits.keys() - members.keys())}"
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


def test_detector_flags_a_missing_expensive_opcode():
    """Self-test: dropping one expensive opcode from the table must be detected."""
    full_limits = _rate_limits(HANDLERS_TREE)
    assert EXPENSIVE_OPCODES <= full_limits.keys()  # sanity of the fixture

    # Rebuild a synthetic module source whose rate-limit table omits one
    # expensive opcode, then assert the scan reports it as missing.
    victim = sorted(EXPENSIVE_OPCODES)[0]
    pruned = dict(full_limits)
    pruned.pop(victim)
    lines = ["INBOUND_RATE_LIMITS = {"]
    for name, (max_events, window) in pruned.items():
        lines.append(f"    int(GatewayOpcode.{name}): ({max_events}, {window!r}),")
    lines.append("}")
    synthetic_tree = ast.parse("\n".join(lines))  # still valid source
    synthetic_limits = _rate_limits(synthetic_tree)
    missing = EXPENSIVE_OPCODES - synthetic_limits.keys()
    assert victim in missing, "detector failed to notice the removed opcode"


@pytest.mark.parametrize("name", sorted(EXPENSIVE_OPCODES))
def test_each_expensive_opcode_individually_covered(name):
    """Per-opcode reporting makes a regression pinpoint the exact opcode."""
    limits = _rate_limits(HANDLERS_TREE)
    assert name in limits, f"{name} is not throttled"


def test_sources_are_read_and_parsed_once(monkeypatch):
    """Perf regression: each gateway source is read and AST-parsed once.

    Previously every detector re-parsed its source text on each call and the
    per-opcode parametrized test re-read + re-parsed the whole handlers source
    once per expensive opcode (12x). Now ``_load_sources`` reads + parses each
    file exactly once and every detector reuses those trees. Counting the
    reads/parses pins this deterministically -- no wall-clock timing and no
    flaky threshold.
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

    (opcodes_text, handlers_text), (opcodes_tree, handlers_tree) = _load_sources()

    assert reads == [OPCODES_SRC, HANDLERS_SRC]
    assert len(parses) == 2
    assert parses == [opcodes_text, handlers_text]

    # Every real-tree detector must reuse the pre-built trees: no extra reads
    # or parses, however many assertions/parameters call them.
    _enum_members(opcodes_tree)
    _declared_set(opcodes_tree, "CLIENT_OPCODES")
    _rate_limits(handlers_tree)
    _rate_limits(handlers_tree)
    _enum_members(opcodes_tree)

    assert reads == [OPCODES_SRC, HANDLERS_SRC]
    assert len(parses) == 2, "a detector re-parsed an already-parsed source"
