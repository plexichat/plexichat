"""PERF700 invariants: process caches stay bounded and are invalidated on writes.

Two static detectors pin two related perf fixes:

* ``src.core.bounded_lru.BoundedLRU`` enforces entry-count, byte and TTL caps on
  every write, and every adopter passes all three limits explicitly. Without
  those caps a cache keyed by request-derived data grows without limit.
* Write paths that hydrate a cached read invalidate that cache. Without the
  invalidation call a mutation leaves a stale value served from the cache until
  its TTL expires.

Both detectors parse real modules with :mod:`ast`, need no app import/config/DB,
and are validated against synthetic violations before the real tree is trusted.
Marked ``unit`` so the default pytest selection keeps them.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[2]

CORE = "src/core/bounded_lru.py"
REQUIRED_LIMITS = frozenset({"max_entries", "max_bytes", "ttl_seconds"})

# Cache-bounded caches and the batch size of cache-invalidation deletes.
CACHE_BOUND_GATES = {
    "src/core/database/cache/operations.py": "_INVALIDATE_DELETE_BATCH",
}

# Write method -> invalidation helper that must be called inside it.
INVALIDATION_SITES = {
    "src/core/messaging/repositories/message.py": {
        "create": "invalidate_conversation_cache",
        "update_content": "invalidate_conversation_cache",
        "update_metadata": "invalidate_conversation_cache",
        "soft_delete": "invalidate_conversation_cache",
        "hard_delete": "invalidate_conversation_cache",
    },
    "src/core/messaging/services/message/edit_delete.py": {
        "edit_message": "cache_delete",
        "update_message_metadata": "cache_delete",
        "automod_remove_message": "cache_delete",
        "delete_message": "cache_delete",
        "delete_messages_bulk": "cache_delete",
    },
    "src/core/messaging/services/user_settings.py": {
        "update_message_settings": "_cache_invalidate",
    },
    "src/core/messaging/services/content_filter.py": {
        "update_filter_settings": "_cache_invalidate",
    },
    "src/core/messaging/services/participant.py": {
        "add_participant": "_cache_invalidate",
        "remove_participant": "_cache_invalidate",
        "add_user_to_multiple_conversations": "_cache_invalidate",
        "remove_user_from_multiple_conversations": "_cache_invalidate",
    },
}


def _parse(source):
    return ast.parse(textwrap.dedent(source))


def _find_class(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _find_function(node, name):
    for child in ast.walk(node):
        if (
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name == name
        ):
            return child
    return None


def _names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _attribute_names(node):
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def _call_attrs(node):
    return {
        n.func.attr
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }


def _raised_messages(fn):
    messages = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            for arg in node.exc.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    messages.add(arg.value)
    return messages


def _init_missing(cls):
    if cls is None:
        return ["BoundedLRU class"]
    init = _find_function(cls, "__init__")
    if init is None:
        return ["__init__ method"]
    joined = " ".join(_raised_messages(init))
    return [
        f"{limit} guard" for limit in sorted(REQUIRED_LIMITS) if limit not in joined
    ]


def _set_missing(cls):
    if cls is None:
        return ["BoundedLRU class"]
    set_fn = _find_function(cls, "set")
    if set_fn is None:
        return ["set method"]
    missing = []
    if not any(isinstance(n, ast.While) for n in ast.walk(set_fn)):
        missing.append("eviction loop")
    names = _names(set_fn) | _attribute_names(set_fn)
    for marker in ("_max_entries", "_max_bytes"):
        if marker not in names:
            missing.append(marker)
    if "expires_at" not in _names(set_fn):
        missing.append("expires_at")
    if "popitem" not in _call_attrs(set_fn):
        missing.append("popitem")
    return missing


def _get_missing(cls):
    if cls is None:
        return ["BoundedLRU class"]
    get_fn = _find_function(cls, "get")
    if get_fn is None:
        return ["get method"]
    missing = []
    for marker in ("expires_at", "now"):
        if marker not in _names(get_fn):
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
    for node in ast.walk(_parse(source)):
        if _is_bounded_lru_call(node):
            return {kw.arg for kw in node.keywords if kw.arg is not None}
    return None


def _adoptions():
    found = []
    for path in (ROOT / "src").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel == CORE or rel.startswith("src/tests/"):
            continue
        try:
            tree = _parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if _is_bounded_lru_call(node):
                keywords = {kw.arg for kw in node.keywords if kw.arg is not None}
                found.append((rel, node.lineno, keywords))
    return found


def _calls_named(fn, name):
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id == name) or (
                isinstance(func, ast.Attribute) and func.attr == name
            ):
                return True
    return False


def _check_invalidation_source(source, func_name, marker):
    fn = _find_function(_parse(source), func_name)
    if fn is None:
        return [f"{func_name}: function not found"]
    if not _calls_named(fn, marker):
        return [f"{func_name}: no {marker}() call on the write path"]
    return []


def _module_constant(tree, name):
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node.value
    return None


def _check_cache_bound_source(source, name):
    """Problems with a cache-bound constant that must stay <= 500."""
    tree = _parse(source)
    node = _module_constant(tree, name)
    if node is None:
        return [f"{name}: missing module-level constant"]
    if not (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ):
        return [f"{name}: not an integer literal"]
    if node.value < 1 or node.value > 500:
        return [f"{name}: out of range {node.value}"]
    return []


def _real_cache_bound_problems():
    problems, scanned = [], []
    for rel, name in sorted(CACHE_BOUND_GATES.items()):
        path = ROOT / rel
        if not path.exists():
            problems.append(f"{rel}: missing file")
            continue
        problems.extend(
            f"{rel}: {p}"
            for p in _check_cache_bound_source(path.read_text(encoding="utf-8"), name)
        )
        scanned.append(rel)
    return problems, scanned


def _real_invalidation_problems():
    problems = []
    scanned = []
    for rel, methods in sorted(INVALIDATION_SITES.items()):
        path = ROOT / rel
        if not path.exists():
            problems.append(f"{rel}: missing file")
            continue
        source = path.read_text(encoding="utf-8")
        for method, marker in sorted(methods.items()):
            problems.extend(
                f"{rel}: {p}"
                for p in _check_invalidation_source(source, method, marker)
            )
        scanned.append(rel)
    return problems, scanned


_CORE_CLASS = _find_class(
    _parse((ROOT / CORE).read_text(encoding="utf-8")), "BoundedLRU"
)
_ADOPTIONS = _adoptions()
_INVALIDATION_PROBLEMS, _INVALIDATION_SCANNED = _real_invalidation_problems()
_CACHE_BOUND_PROBLEMS, _CACHE_BOUND_SCANNED = _real_cache_bound_problems()


def test_self_test_bounded_lru_detectors_catch_synthetic_violations():
    """Missing cap guards/eviction/expiry and missing kwargs are detected."""
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
    good = _find_class(_parse(good_cls), "BoundedLRU")
    bad = _find_class(_parse(bad_cls), "BoundedLRU")

    assert _init_missing(good) == []
    assert _set_missing(good) == []
    assert _get_missing(good) == []
    assert set(_init_missing(bad)) == {"max_bytes guard", "ttl_seconds guard"}
    assert "eviction loop" in _set_missing(bad)
    assert "pop" in _get_missing(bad)

    assert (
        _construction_keywords(
            "c = BoundedLRU(max_entries=10, max_bytes=100, ttl_seconds=5)"
        )
        == REQUIRED_LIMITS
    )
    assert _construction_keywords("c = BoundedLRU(max_entries=10, max_bytes=100)") == {
        "max_entries",
        "max_bytes",
    }


def test_self_test_cache_invalidation_detector_catches_synthetic_violation():
    """A write method without an invalidation call is flagged."""
    good = textwrap.dedent(
        """
        class Service:
            def update(self, user_id):
                self._db.execute("UPDATE t SET x = 1")
                self._cache_invalidate(("key", user_id))
        """
    )
    bad = textwrap.dedent(
        """
        class Service:
            def update(self, user_id):
                self._db.execute("UPDATE t SET x = 1")
        """
    )
    name_call = 'def update(message_id):\n    cache_delete(f"msg:obj:{message_id}")\n'

    assert _check_invalidation_source(good, "update", "_cache_invalidate") == []
    assert _check_invalidation_source(name_call, "update", "cache_delete") == []
    assert any(
        "cache_invalidate" in p
        for p in _check_invalidation_source(bad, "update", "_cache_invalidate")
    )
    assert any(
        "not found" in p
        for p in _check_invalidation_source("x = 1\n", "update", "cache_delete")
    )


def test_scan_is_not_vacuous():
    assert _CORE_CLASS is not None, "BoundedLRU primitive not found"
    assert len(_ADOPTIONS) >= 2
    assert any(rel.endswith("dialect.py") for rel, _, _ in _ADOPTIONS)
    assert sum(len(m) for m in INVALIDATION_SITES.values()) >= 15
    assert len(_INVALIDATION_SCANNED) == len(INVALIDATION_SITES)
    assert len(_CACHE_BOUND_SCANNED) == len(CACHE_BOUND_GATES)


def test_bounded_lru_primitive_enforces_all_three_limits():
    assert not _init_missing(_CORE_CLASS), (
        "constructor lost a bound guard:\n  " + "\n  ".join(_init_missing(_CORE_CLASS))
    )
    assert not _set_missing(_CORE_CLASS), (
        "set() lost an eviction marker:\n  " + "\n  ".join(_set_missing(_CORE_CLASS))
    )
    assert not _get_missing(_CORE_CLASS), (
        "get() lost expiry handling:\n  " + "\n  ".join(_get_missing(_CORE_CLASS))
    )


def test_every_bounded_lru_adopter_passes_all_three_limits():
    offenders = [
        f"{rel}:{lineno} missing {sorted(REQUIRED_LIMITS - keywords)}"
        for rel, lineno, keywords in _ADOPTIONS
        if not REQUIRED_LIMITS.issubset(keywords)
    ]
    assert not offenders, (
        "BoundedLRU caches constructed without all three bounds:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_write_paths_invalidate_their_caches():
    assert not _INVALIDATION_PROBLEMS, (
        "cache invalidation missing on a write path:\n  "
        + "\n  ".join(sorted(_INVALIDATION_PROBLEMS))
    )


def test_self_test_cache_bound_detector_catches_synthetic_violation():
    """A drifted invalidation delete batch is flagged."""
    good = "_INVALIDATE_DELETE_BATCH = 500\n"
    oversized = "_INVALIDATE_DELETE_BATCH = 5000\n"
    missing = "def invalidate():\n    pass\n"
    assert _check_cache_bound_source(good, "_INVALIDATE_DELETE_BATCH") == []
    assert any(
        "range" in p
        for p in _check_cache_bound_source(oversized, "_INVALIDATE_DELETE_BATCH")
    )
    assert any(
        "missing" in p
        for p in _check_cache_bound_source(missing, "_INVALIDATE_DELETE_BATCH")
    )


def test_cache_invalidation_delete_batch_is_bounded():
    assert not _CACHE_BOUND_PROBLEMS, (
        "cache invalidation delete batch drifted above 500:\n  "
        + "\n  ".join(sorted(_CACHE_BOUND_PROBLEMS))
    )
