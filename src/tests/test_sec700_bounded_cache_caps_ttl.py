"""SEC700 invariant: standalone bounded caches keep a hard cap AND a TTL.

``BoundedLRU`` has its own primitive invariant suite. This suite pins the same
caps+TTL safety properties on the hand-rolled in-process caches that hold their
own state: each must declare a positive max-entries (and where relevant
max-bytes) constant, a positive TTL constant, and actually use them for LRU
eviction / expiry. Dropping a cap lets a hostile stream of distinct keys grow the
cache without bound; dropping the TTL lets stale, cross-worker reads survive.

This is a static AST invariant: no app import, config, DB or network is needed.
The detector is validated against synthetic good/bad snippets (including a
missing cap, TTL, eviction call and expiry marker) before the real tree is
trusted, so it cannot silently become an always-passing scan.
"""

import ast
import operator
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[2]

# (source path, cap constants, ttl constant, eviction markers, expiry markers)
CACHES = [
    (
        "src/core/reactions/manager/cache.py",
        ("DEFAULT_MAX_ENTRIES",),
        "DEFAULT_READ_TTL_SECONDS",
        ("popitem",),
        ("expires_at",),
    ),
    (
        "src/core/reactions/manager/emoji_lookup_cache.py",
        ("DEFAULT_EMOJI_MAX_ENTRIES", "DEFAULT_EMOJI_MAX_BYTES"),
        "DEFAULT_EMOJI_TTL_SECONDS",
        ("popitem",),
        ("expires_at",),
    ),
    (
        "src/core/admin/read_cache.py",
        ("_MAX_ENTRIES",),
        "DEFAULT_TTL_SECONDS",
        ("pop", "min"),
        ("ttl",),
    ),
]

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def _const_value(node):
    """Evaluate a literal or simple numeric arithmetic expression."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _const_value(node.operand)
        if isinstance(value, (int, float)):
            return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left, right = _const_value(node.left), _const_value(node.right)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            try:
                return _BINOPS[type(node.op)](left, right)
            except (ArithmeticError, ValueError):
                return None
    return None


def _module_constants(tree):
    """name -> numeric value for simple module-level assignments."""
    out = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = _const_value(node.value)
        if isinstance(value, (int, float)):
            out[target.id] = value
    return out


def _names_and_calls(tree):
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    calls = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            calls.add(node.func.attr)
        elif isinstance(node.func, ast.Name):
            calls.add(node.func.id)
    return names, calls


def _positive_number(consts, name):
    value = consts.get(name)
    return isinstance(value, (int, float)) and value > 0


def _cache_missing(source, cap_names, ttl_name, eviction_markers, expiry_markers):
    """Return a sorted list of missing safety markers for one cache module."""
    tree = ast.parse(source)
    consts = _module_constants(tree)
    names, calls = _names_and_calls(tree)
    markers = names | calls
    missing = []
    for cap in cap_names:
        if not _positive_number(consts, cap):
            missing.append(f"cap {cap}")
    if not _positive_number(consts, ttl_name):
        missing.append(f"ttl {ttl_name}")
    for marker in eviction_markers:
        if marker not in markers:
            missing.append(f"eviction {marker}")
    for marker in expiry_markers:
        if marker not in markers:
            missing.append(f"expiry {marker}")
    return sorted(missing)


def test_self_test_detectors_catch_uncapped_cache():
    """Good caches pass; a cache without caps/TTL/eviction/expiry is flagged."""
    good = textwrap.dedent(
        """
        DEFAULT_MAX_ENTRIES = 512
        DEFAULT_TTL_SECONDS = 3.0

        class C:
            def set(self, key, value):
                while len(self._data) >= DEFAULT_MAX_ENTRIES:
                    self._data.popitem(last=False)
                self._data[key] = (now + DEFAULT_TTL_SECONDS, value)

            def get(self, key):
                expires_at, value = self._data[key]
                if expires_at <= now:
                    del self._data[key]
                return value
        """
    )
    bad = textwrap.dedent(
        """
        class C:
            def set(self, key, value):
                self._data[key] = value

            def get(self, key):
                return self._data[key]
        """
    )
    good_cfg = (
        ("DEFAULT_MAX_ENTRIES",),
        "DEFAULT_TTL_SECONDS",
        ("popitem",),
        ("expires_at",),
    )
    assert _cache_missing(good, *good_cfg) == []
    missing = _cache_missing(bad, *good_cfg)
    assert "cap DEFAULT_MAX_ENTRIES" in missing
    assert "ttl DEFAULT_TTL_SECONDS" in missing
    assert "eviction popitem" in missing
    assert "expiry expires_at" in missing

    # A non-positive cap or TTL is not a bound at all and must be rejected.
    zero_cap = good.replace("DEFAULT_MAX_ENTRIES = 512", "DEFAULT_MAX_ENTRIES = 0")
    assert "cap DEFAULT_MAX_ENTRIES" in _cache_missing(zero_cap, *good_cfg)
    zero_ttl = good.replace("DEFAULT_TTL_SECONDS = 3.0", "DEFAULT_TTL_SECONDS = 0")
    assert "ttl DEFAULT_TTL_SECONDS" in _cache_missing(zero_ttl, *good_cfg)


def test_self_test_arithmetic_cap_evaluation():
    """Byte caps written as ``256 * 1024`` are evaluated, not silently missed."""
    tree = ast.parse("DEFAULT_EMOJI_MAX_BYTES = 256 * 1024")
    assert _module_constants(tree)["DEFAULT_EMOJI_MAX_BYTES"] == 262144


def test_scan_is_not_vacuous():
    assert len(CACHES) >= 3
    for rel, *_cfg in CACHES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        assert _module_constants(tree), f"{rel}: no module constants discovered"


@pytest.mark.parametrize(
    "rel,caps,ttl,eviction,expiry", CACHES, ids=[c[0].split("/")[-1] for c in CACHES]
)
def test_bounded_cache_has_caps_and_ttl(rel, caps, ttl, eviction, expiry):
    source = (ROOT / rel).read_text(encoding="utf-8")
    missing = _cache_missing(source, caps, ttl, eviction, expiry)
    assert not missing, f"{rel} lost a bound:\n  " + "\n  ".join(missing)
