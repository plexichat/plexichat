"""SEC600 invariant: privileged admin caches are caller-partitioned and bounded.

A request-scoped admin cache keyed only by the *target* admin id -- or one whose
store is not bounded -- turns a short-lived memoization into a cross-caller
grant leak or an unbounded in-process registry. Two prior fixes hardened this
boundary:

* ``permission_grant_cache.get_cached_grants`` keys its store by
  ``(db token, db path, credential, subject)``, caps entry count, applies a short
  TTL and fails closed by coercing every loader result to a ``frozenset``. The
  credential component is what stops one admin session from riding another
  caller's cached grants.
* ``request_roles.cached_session_roles`` reuses a request's role payloads only
  when the request-context binding, database identity and admin id all still
  match (the "authz gate before cache reuse" fix).

This suite is a pure-AST ratchet over those two modules -- no app import, config
or DB is needed, so it cannot flake. Every detector is first exercised against
synthetic good/bad modules (self-test), then the real tree is checked, and a
non-vacuity test proves the scan really sees the helper functions it guards.
"""

import ast
import copy
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]
GRANT = "src/core/admin/permission_grant_cache.py"
REQUEST_ROLES = "src/core/admin/request_roles.py"


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _own_body_nodes(node: ast.AST):
    """Yield nodes on ``node``'s own execution path, skipping nested callables.

    Descend through statement-level nesting (``if``/``for``/``with``/``try``)
    but never into a nested function, async function or lambda: *defining* a
    helper that references a cache-key or identity-binding marker does not
    execute it. Crediting such dead evidence lets an unpartitioned or ungated
    cache satisfy this ratchet with names that never run.
    """
    stack = [node]
    while stack:
        for child in ast.iter_child_nodes(stack.pop()):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            stack.append(child)


def _loaded_names(node):
    return {n.id for n in _own_body_nodes(node) if isinstance(n, ast.Name)}


def _module_constants(tree):
    """Simple module-level ``NAME = <literal>`` assignments."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
            if isinstance(target, ast.Name) and isinstance(value, ast.Constant):
                out[target.id] = value.value
    return out


def _grant_key_missing(fn):
    """Markers that must be present in the cache-key builder."""
    if fn is None:
        return ["_make_key function"]
    names = _loaded_names(fn)
    return [
        f"key component {m}"
        for m in ("credential", "subject", "DIRECT_CREDENTIAL")
        if m not in names
    ]


def _grant_use_missing(fn):
    """Bound / TTL / fail-closed markers inside the cache accessor."""
    if fn is None:
        return ["get_cached_grants function"]
    names = _loaded_names(fn)
    return [m for m in ("_MAX_ENTRIES", "ttl", "frozenset") if m not in names]


def _request_roles_gate_missing(fn):
    """Identity bindings that must gate request-scoped cache reuse."""
    if fn is None:
        return ["cached_session_roles function"]
    names = _loaded_names(fn)
    return [
        m for m in ("_bound_request_context", "id", "cached_admin_id") if m not in names
    ]


def _tree(relative):
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _scan():
    grant_tree = _tree(GRANT)
    roles_tree = _tree(REQUEST_ROLES)
    constants = _module_constants(grant_tree)
    return {
        "key_missing": _grant_key_missing(_find_function(grant_tree, "_make_key")),
        "use_missing": _grant_use_missing(
            _find_function(grant_tree, "get_cached_grants")
        ),
        "gate_missing": _request_roles_gate_missing(
            _find_function(roles_tree, "cached_session_roles")
        ),
        "max_entries": constants.get("_MAX_ENTRIES"),
        "ttl": constants.get("DEFAULT_TTL_SECONDS"),
    }


_SCAN = _scan()


def test_self_test_detectors_catch_synthetic_violations():
    """Every detector flags the regression shape and accepts the good shape."""
    good_key = textwrap.dedent(
        """
        def _make_key(db, credential, subject):
            token, path = _db_identity(db)
            return (token, path, credential or DIRECT_CREDENTIAL, _normalize(subject))
        """
    )
    bad_key = textwrap.dedent(
        """
        def _make_key(db, subject):
            token, path = _db_identity(db)
            return (token, path, _normalize(subject))
        """
    )
    assert _grant_key_missing(_find_function(ast.parse(good_key), "_make_key")) == []
    bad_key_missing = _grant_key_missing(
        _find_function(ast.parse(bad_key), "_make_key")
    )
    assert "key component credential" in bad_key_missing
    assert "key component DIRECT_CREDENTIAL" in bad_key_missing

    good_use = textwrap.dedent(
        """
        def get_cached_grants(db, credential, subject, loader, ttl=1.0):
            if len(_ENTRIES) >= _MAX_ENTRIES:
                _ENTRIES.clear()
            value = loader()
            if not isinstance(value, frozenset):
                value = frozenset()
            _ENTRIES[key] = (now + ttl, value)
            return value
        """
    )
    bad_use = textwrap.dedent(
        """
        def get_cached_grants(db, credential, subject, loader, ttl=1.0):
            value = loader()
            _ENTRIES[key] = (now, value)
            return value
        """
    )
    assert (
        _grant_use_missing(_find_function(ast.parse(good_use), "get_cached_grants"))
        == []
    )
    assert set(
        _grant_use_missing(_find_function(ast.parse(bad_use), "get_cached_grants"))
    ) == {
        "_MAX_ENTRIES",
        "ttl",
        "frozenset",
    }

    good_roles = textwrap.dedent(
        """
        def cached_session_roles(db, admin_id):
            value = _session_roles.get()
            if value is None:
                return None
            db_identity, cached_admin_id, payloads, bound_context = value
            if bound_context is not _bound_request_context():
                return None
            if db_identity != id(db):
                return None
            if cached_admin_id == admin_id:
                return payloads
            return None
        """
    )
    bad_roles = textwrap.dedent(
        """
        def cached_session_roles(db, admin_id):
            value = _session_roles.get()
            if value is None:
                return None
            db_identity, cached_admin_id, payloads, bound_context = value
            if cached_admin_id == admin_id:
                return payloads
            return None
        """
    )
    assert (
        _request_roles_gate_missing(
            _find_function(ast.parse(good_roles), "cached_session_roles")
        )
        == []
    )
    assert set(
        _request_roles_gate_missing(
            _find_function(ast.parse(bad_roles), "cached_session_roles")
        )
    ) == {"_bound_request_context", "id"}


def test_detectors_reject_dead_nested_key_evidence():
    """Markers living only in a never-called nested callable are not protection.

    Regression: ``_loaded_names`` used ``ast.walk``, which descends into nested
    functions and lambdas. An unpartitioned key, unbounded accessor or ungated
    cache reuse could pass every check by parking the required names in dead
    nested code.
    """
    dead = textwrap.dedent(
        """
        def _make_key(db, subject):
            def _never_called():
                return (token, path, credential or DIRECT_CREDENTIAL, subject)
            return None
        """
    )
    fn = _find_function(ast.parse(dead), "_make_key")
    assert set(_grant_key_missing(fn)) == {
        "key component credential",
        "key component subject",
        "key component DIRECT_CREDENTIAL",
    }

    dead = textwrap.dedent(
        """
        def get_cached_grants(db, subject):
            helper = lambda: (
                len(_ENTRIES) >= _MAX_ENTRIES and ttl and frozenset()
            )
            return None
        """
    )
    fn = _find_function(ast.parse(dead), "get_cached_grants")
    assert set(_grant_use_missing(fn)) == {"_MAX_ENTRIES", "ttl", "frozenset"}

    dead = textwrap.dedent(
        """
        def cached_session_roles(db, admin_id):
            def _never_called():
                return _bound_request_context() is id(db) and cached_admin_id
            return None
        """
    )
    fn = _find_function(ast.parse(dead), "cached_session_roles")
    assert set(_request_roles_gate_missing(fn)) == {
        "_bound_request_context",
        "id",
        "cached_admin_id",
    }

    # Live markers still pass behind statement-level nesting, even when an
    # unrelated dead helper is present in the same handler.
    live = textwrap.dedent(
        """
        def _make_key(db, credential, subject):
            def _unrelated_helper():
                return 1
            token, path = _db_identity(db)
            if token:
                return (token, path, credential or DIRECT_CREDENTIAL, subject)
            return None
        """
    )
    fn = _find_function(ast.parse(live), "_make_key")
    assert _grant_key_missing(fn) == []


def test_real_scan_is_not_vacuous_under_in_memory_mutation():
    """Renaming real in-body evidence makes each detector report the loss."""
    grant_tree = _tree(GRANT)
    roles_tree = _tree(REQUEST_ROLES)

    key_fn = _find_function(grant_tree, "_make_key")
    assert _grant_key_missing(key_fn) == []
    clone = copy.deepcopy(key_fn)
    for node in _own_body_nodes(clone):
        if isinstance(node, ast.Name) and node.id == "credential":
            node.id = "_credential_renamed"
    assert "key component credential" in _grant_key_missing(clone)

    use_fn = _find_function(grant_tree, "get_cached_grants")
    assert _grant_use_missing(use_fn) == []
    clone = copy.deepcopy(use_fn)
    for node in _own_body_nodes(clone):
        if isinstance(node, ast.Name) and node.id == "_MAX_ENTRIES":
            node.id = "_MAX_ENTRIES_renamed"
    assert "_MAX_ENTRIES" in _grant_use_missing(clone)

    gate_fn = _find_function(roles_tree, "cached_session_roles")
    assert _request_roles_gate_missing(gate_fn) == []
    clone = copy.deepcopy(gate_fn)
    for node in _own_body_nodes(clone):
        if isinstance(node, ast.Name) and node.id == "_bound_request_context":
            node.id = "_bound_request_context_renamed"
    assert "_bound_request_context" in _request_roles_gate_missing(clone)


def test_scan_is_not_vacuous():
    """The real helpers/bounds must actually be discovered by the scan."""
    assert _SCAN["key_missing"] is not None
    assert "_make_key function" not in _SCAN["key_missing"]
    assert "get_cached_grants function" not in _SCAN["use_missing"]
    assert "cached_session_roles function" not in _SCAN["gate_missing"]

    max_entries = _SCAN["max_entries"]
    ttl = _SCAN["ttl"]
    assert isinstance(max_entries, int), f"_MAX_ENTRIES is {max_entries!r}"
    assert 0 < max_entries <= 10_000, f"grant cache is not bounded: {max_entries}"
    assert isinstance(ttl, float), f"DEFAULT_TTL_SECONDS is {ttl!r}"
    assert 0 < ttl <= 3600, f"grant cache TTL is not short/bounded: {ttl}"


def test_real_grant_cache_key_is_caller_partitioned():
    assert not _SCAN["key_missing"], (
        "grant cache key lost a caller-identity component:\n  "
        + "\n  ".join(_SCAN["key_missing"])
    )


def test_real_grant_cache_is_bounded_and_fails_closed():
    assert not _SCAN["use_missing"], (
        "grant cache accessor lost a bound/TTL/fail-closed marker:\n  "
        + "\n  ".join(_SCAN["use_missing"])
    )


def test_real_request_role_cache_checks_bindings_before_reuse():
    assert not _SCAN["gate_missing"], (
        "request-scoped role cache lost an identity gate:\n  "
        + "\n  ".join(_SCAN["gate_missing"])
    )
