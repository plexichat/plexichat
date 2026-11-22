"""PERF invariant: process-local LRU caches keep hard entry/byte/TTL bounds.

``src.core.bounded_lru.BoundedLRU`` is the shared primitive introduced by the
"bound cache sizing" work. It exists precisely so a cache keyed by
request-derived data can never grow without limit: the constructor rejects
non-positive limits, ``set`` evicts expired and least-recently-used entries
against both an entry count and a byte budget, and ``get`` drops expired entries
so a stale value is never served. Adopters pass all three limits explicitly.

This is a *static* invariant, so it is marked ``unit`` rather than
``performance`` (the default pytest selection deselects the latter) -- it needs
no app import, config, DB or network and cannot flake. Detectors are validated
against synthetic good/bad snippets before the real tree is trusted, and a
non-vacuity test proves the scan discovers the primitive and its adopters.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[2]
CORE = "src/core/bounded_lru.py"
REQUIRED_LIMITS = frozenset({"max_entries", "max_bytes", "ttl_seconds"})


def _find_function(node, name):
    for child in ast.walk(node):
        if (
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name == name
        ):
            return child
    return None


def _find_class(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _loaded_names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _call_attrs(node):
    return {
        n.func.attr
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }


def _attribute_names(node):
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def _raised_messages(fn):
    """String messages passed to ``raise ValueError(...)`` inside ``fn``."""
    messages = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            for arg in node.exc.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    messages.add(arg.value)
    return messages


def _init_guards_missing(cls):
    if cls is None:
        return ["BoundedLRU class"]
    init = _find_function(cls, "__init__")
    if init is None:
        return ["__init__ method"]
    joined = " ".join(_raised_messages(init))
    return [
        f"{limit} guard" for limit in sorted(REQUIRED_LIMITS) if limit not in joined
    ]


def _set_guards_missing(cls):
    if cls is None:
        return ["BoundedLRU class"]
    set_fn = _find_function(cls, "set")
    if set_fn is None:
        return ["set method"]
    missing = []
    if not any(isinstance(n, ast.While) for n in ast.walk(set_fn)):
        missing.append("eviction loop")
    names = _loaded_names(set_fn) | _attribute_names(set_fn)
    for marker in ("_max_entries", "_max_bytes"):
        if marker not in names:
            missing.append(marker)
    if "expires_at" not in _loaded_names(set_fn):
        missing.append("expires_at")
    if "popitem" not in _call_attrs(set_fn):
        missing.append("popitem")
    return missing


def _get_guards_missing(cls):
    if cls is None:
        return ["BoundedLRU class"]
    get_fn = _find_function(cls, "get")
    if get_fn is None:
        return ["get method"]
    missing = []
    names = _loaded_names(get_fn)
    for marker in ("expires_at", "now"):
        if marker not in names:
            missing.append(marker)
    if "pop" not in _call_attrs(get_fn):
        missing.append("pop")
    return missing


def _is_bounded_lru_call(node):
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id == "BoundedLRU")
        or (isinstance(node.func, ast.Attribute) and node.func.attr == "BoundedLRU")
    )


def _construction_keywords(source):
    """Keyword names on the first BoundedLRU(...) call in a source snippet."""
    for node in ast.walk(ast.parse(source)):
        if _is_bounded_lru_call(node):
            return {kw.arg for kw in node.keywords if kw.arg is not None}
    return None


def _adoptions():
    """All BoundedLRU constructions in src (excluding tests and the primitive)."""
    found = []
    for path in (ROOT / "src").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel == CORE or rel.startswith("src/tests/"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if _is_bounded_lru_call(node):
                keywords = {kw.arg for kw in node.keywords if kw.arg is not None}
                found.append((rel, node.lineno, keywords))
    return found


_CORE_TREE = ast.parse((ROOT / CORE).read_text(encoding="utf-8"))
_CORE_CLASS = _find_class(_CORE_TREE, "BoundedLRU")
_ADOPTIONS = _adoptions()


def test_self_test_detectors_catch_synthetic_violations():
    """Synthetic unbounded constructors / missing eviction are detected."""
    good_cls = textwrap.dedent(
        """
        class BoundedLRU:
            def __init__(self, max_entries, max_bytes, ttl_seconds):
                if max_entries < 1:
                    raise ValueError("BoundedLRU max_entries must be at least 1")
                if max_bytes < 1:
                    raise ValueError("BoundedLRU max_bytes must be at least 1")
                if ttl_seconds <= 0:
                    raise ValueError("BoundedLRU ttl_seconds must be positive")

            def set(self, key, value):
                expires_at = now + self._ttl
                while self._data and (
                    len(self._data) > self._max_entries
                    or self._bytes > self._max_bytes
                ):
                    self._data.popitem(last=False)

            def get(self, key):
                expires_at, value, size = self._data[key]
                if expires_at <= now:
                    self._data.pop(key, None)
                return value
        """
    )
    bad_cls = textwrap.dedent(
        """
        class BoundedLRU:
            def __init__(self, max_entries, max_bytes, ttl_seconds):
                if max_entries < 1:
                    raise ValueError("BoundedLRU max_entries must be at least 1")

            def set(self, key, value):
                self._data[key] = value

            def get(self, key):
                return self._data[key]
        """
    )
    good = _find_class(ast.parse(good_cls), "BoundedLRU")
    bad = _find_class(ast.parse(bad_cls), "BoundedLRU")
    assert _init_guards_missing(good) == []
    assert _set_guards_missing(good) == []
    assert _get_guards_missing(good) == []
    assert set(_init_guards_missing(bad)) == {"max_bytes guard", "ttl_seconds guard"}
    assert "eviction loop" in _set_guards_missing(bad)
    assert "pop" in _get_guards_missing(bad)

    good_call = "c = BoundedLRU(max_entries=10, max_bytes=100, ttl_seconds=5)"
    bad_call = "c = BoundedLRU(max_entries=10, max_bytes=100)"
    assert _construction_keywords(good_call) == REQUIRED_LIMITS
    assert _construction_keywords(bad_call) == {"max_entries", "max_bytes"}
    assert "ttl_seconds" not in _construction_keywords(bad_call)


def test_scan_is_not_vacuous():
    assert _CORE_CLASS is not None, "BoundedLRU primitive not found"
    # At least the database dialect and artifact capability caches adopt it.
    assert len(_ADOPTIONS) >= 2, (
        f"only {len(_ADOPTIONS)} BoundedLRU adopters discovered"
    )
    assert any(rel.endswith("dialect.py") for rel, _, _ in _ADOPTIONS)


def test_bounded_lru_primitive_enforces_all_three_limits():
    assert not _init_guards_missing(_CORE_CLASS), (
        "constructor lost a bound guard:\n  "
        + "\n  ".join(_init_guards_missing(_CORE_CLASS))
    )
    assert not _set_guards_missing(_CORE_CLASS), (
        "set() lost an eviction marker:\n  "
        + "\n  ".join(_set_guards_missing(_CORE_CLASS))
    )
    assert not _get_guards_missing(_CORE_CLASS), (
        "get() lost expiry handling:\n  "
        + "\n  ".join(_get_guards_missing(_CORE_CLASS))
    )


def test_every_adopter_passes_entry_byte_and_ttl_limits():
    offenders = [
        f"{rel}:{lineno} missing {sorted(REQUIRED_LIMITS - keywords)}"
        for rel, lineno, keywords in _ADOPTIONS
        if not REQUIRED_LIMITS.issubset(keywords)
    ]
    assert not offenders, (
        "BoundedLRU caches constructed without all three bounds:\n  "
        + "\n  ".join(sorted(offenders))
    )
