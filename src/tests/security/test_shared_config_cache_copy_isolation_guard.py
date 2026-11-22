"""FINAL static sweep guard for the shared config/metadata cache family.

Definitive enumeration of the process-wide caches whose VALUE is a mutable
(list / dict / dataclass with nested mutables) that feeds an authorization or
config decision and is returned by reference.  Each family was hardened by a
peer with a dedicated copy-on-read/write regression test; this module is the
final sweep that keeps those guards from rotting in a future refactor.

========================  ================================================  ================================================
cache family              process-wide store                                 copy contract that must survive
========================  ================================================  ================================================
capability verdicts       _capability_cache (artifacts)                       _copy_capability_info on every read
admin host allowlist      _explicit_hosts_cache["value"]                      _copy_explicit_hosts on every read
profile bulk              mem_cache_get("user_profile:*")                     _profile_snapshot on store and read
emoji catalog             ReactionManager._emoji_catalog_cache_instance       replace()/dict() on every cached read
tier-limit verdicts       _tier_limits_snapshot / _tier_configuration_snap    _copy_tier_limits on store and read
API config                _api_config_snapshot                                deepcopy on store and every read
user-feature verdicts     _features_cache                                     _copy_user_features on store and read
========================  ================================================  ================================================

Why each value is in scope (the poisoning impact the copy breaks):

* ``CapabilityInfo`` (mutable dataclass + ``details`` dict) gates licensed
  features via ``is_usable(info.state)``.  Handing out the memoized instance
  lets one caller flip the state for every later request.
* the explicit admin allowlist is a mutable list fed to
  ``check_host_restriction`` on every admin request; an in-place append of a
  permissive CIDR would widen the admin gate for the whole staleness window.
* bulk profile dicts carry mutable ``badges``/``permissions``; mutating a
  returned profile would poison every later listing that resolves that id.
* emoji ``CustomEmoji`` rows / count dicts are cached server shared metadata;
  a caller renaming a returned element would rewrite the cached catalog.
* ``TierLimits`` (mutable dataclass) and ``TierConfiguration`` (mutable list +
  dict) are cached process-wide by config version and gate rate limits, upload
  sizes and emoji slots; mutating either poisons every later quota decision.
* ``APIConfig`` (mutable dataclass with CORS lists and ``access_token_required``)
  is cached process-wide and gates CORS and access-token auth on every request;
  mutating it widens those gates for every later caller.
* ``UserFeatures`` (mutable dataclass with a mutable ``badges`` list) is memoized
  process-wide in ``_features_cache`` and every tier / feature-flag / badge /
  rate-limit decision funnels through ``get_user_features``. Handing out the
  memoized instance lets one caller flip ``rate_limit_tier`` to ``premium`` or
  append ``admin`` to ``badges`` and widen those gates for the whole TTL.

Values that are deliberately NOT part of this family (so the enumeration is
complete): caches whose value is immutable (``StaticPolicy`` frozen dataclass
with a frozenset, ``URLResolutionCache`` tuple resolutions, CATEGORY_SCOPE,
``permission_grant_cache`` frozensets, ``read_cache`` bytes), and reaction L0
query-result caches whose rows are display data with the live authorization
gate applied *before* the cache (see ``ReactionReadCache`` docstring).

The guard parses each module with ``ast`` and requires the accessor to actually
call its copy helper on the cached value -- not merely that a copy helper is
defined somewhere.  Parametrized self-tests mutate the source in memory and
prove that dropping any copy call is flagged, so the guard cannot pass
vacuously.

A fixed tuple of families can still fail open on a cache that does not exist
yet: a developer adding a NEW process-wide mutable verdict cache under a novel
name would not appear in ``COPY_GUARDS`` and the parametrized checks above would
stay green.  The structural sweep at the bottom of this module closes that gap:
it discovers module-level cache stores and every accessor that returns a cached
value by reference from the AST of the whole production tree, requires each such
return to pass through a copy, and fails on any unclassified site.  Sites whose
cached value is immutable or is not a decision verdict are allowlisted with an
explicit reason.  A synthetic novel-name self-test proves a by-reference cache
is flagged and that the detector is not vacuously rejecting every accessor.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import Dict, Set, Tuple

import pytest

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]

# (label, module path, cache symbol that must be present in the source,
#  optional copy helper -> names it must call,
#  accessors -> copy-call names each must make).
# ``helper`` may be None when the copy is inline in each accessor.
Spec = Tuple[
    str, str, str, Tuple[str, str, ...] | None, Tuple[Tuple[str, str, ...], ...]
]

COPY_GUARDS: Tuple[Spec, ...] = (
    (
        "capability verdicts",
        "src/core/artifacts/capabilities.py",
        "_capability_cache",
        ("_copy_capability_info", ("replace", "deepcopy")),
        (
            ("get_capability", ("_copy_capability_info",)),
            ("get_artifact_capabilities", ("_copy_capability_info",)),
        ),
    ),
    (
        "admin host allowlist",
        "src/api/routes/admin/utils.py",
        "_explicit_hosts_cache",
        ("_copy_explicit_hosts", ("list",)),
        (("_explicit_admin_allowed_hosts", ("_copy_explicit_hosts",)),),
    ),
    (
        "profile bulk",
        "src/core/auth/managers/profile.py",
        "user_profile:",
        ("_profile_snapshot", ("deepcopy",)),
        (
            ("get_user_profiles_bulk", ("_profile_snapshot",)),
            ("_profile_from_cache", ("_profile_snapshot",)),
        ),
    ),
    (
        "emoji catalog",
        "src/core/reactions/manager/emojis.py",
        "EmojiCatalogCache",
        None,
        (
            ("get_custom_emoji", ("replace",)),
            ("get_server_custom_emojis", ("replace",)),
            ("get_emoji_counts", ("dict",)),
        ),
    ),
    (
        "tier-limit verdicts",
        "src/core/features/__init__.py",
        "_tier_limits_snapshot",
        ("_copy_tier_limits", ("deepcopy",)),
        (
            ("get_tier_limits", ("_copy_tier_limits",)),
            ("get_tier_configuration", ("_copy_tier_limits",)),
        ),
    ),
    (
        "API config",
        "src/api/config.py",
        "_api_config_snapshot",
        None,
        (("get_api_config", ("deepcopy",)),),
    ),
    (
        "user-feature verdicts",
        "src/core/features/__init__.py",
        "_features_cache",
        ("_copy_user_features", ("deepcopy",)),
        (("get_user_features", ("_copy_user_features",)),),
    ),
)


def _source(relpath: str) -> str:
    return (_REPO / relpath).read_text(encoding="utf-8")


def _functions(source: str) -> Dict[str, ast.AST]:
    tree = ast.parse(source)
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _own_body_nodes(root: ast.AST):
    """Yield ``root`` and nodes reachable through execution, not definition.

    Descend through statement-level nesting (``if``/``for``/``with``/``try``)
    but skip nested function, async function and lambda bodies: *defining* a
    helper that copies a value does not copy it. Crediting a dead nested copy
    call would let an accessor hand out the shared mutable object by reference.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)


def _called_names(node: ast.AST) -> Set[str]:
    """Names/callable attributes invoked on a function's own execution path."""
    names: Set[str] = set()
    for child in _own_body_nodes(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _check(spec: Spec, sources: Dict[str, str]) -> None:
    label, relpath, cache_symbol, helper, accessors = spec
    source = sources[relpath]

    assert cache_symbol in source, (
        f"{label}: cache store {cache_symbol!r} no longer in {relpath}; "
        "re-triage whether the copy contract still applies."
    )

    funcs = _functions(source)
    if helper is not None:
        helper_name, markers = helper
        assert helper_name in funcs, f"{label}: copy helper {helper_name} was removed"
        called = _called_names(funcs[helper_name])
        missing = set(markers) - called
        assert not missing, (
            f"{label}: {helper_name} no longer copies (missing {sorted(missing)})"
        )

    for name, copy_calls in accessors:
        assert name in funcs, f"{label}: accessor {name} was removed from {relpath}"
        called = _called_names(funcs[name])
        missing = set(copy_calls) - called
        assert not missing, (
            f"{label}: {name} no longer copies the cached value on read "
            f"(missing call(s) {sorted(missing)}); a caller could mutate the "
            "shared memo and poison every later request."
        )


def _all_sources() -> Dict[str, str]:
    return {relpath: _source(relpath) for _, relpath, _, _, _ in COPY_GUARDS}


def test_enumeration_is_complete_and_nonempty():
    """Deleting a family from the guard is itself a failure."""
    labels = [spec[0] for spec in COPY_GUARDS]
    assert len(labels) == len(set(labels)) == 7, (
        f"expected the seven shared config/metadata cache families, got {labels}"
    )
    assert set(labels) == {
        "capability verdicts",
        "admin host allowlist",
        "profile bulk",
        "emoji catalog",
        "tier-limit verdicts",
        "API config",
        "user-feature verdicts",
    }


@pytest.mark.parametrize("spec", COPY_GUARDS, ids=lambda s: s[0])
def test_cache_copies_on_read_and_write(spec: Spec):
    """Every enumerated cache must still detach values from its shared store."""
    _check(spec, _all_sources())


@pytest.mark.parametrize("spec", COPY_GUARDS, ids=lambda s: s[0])
def test_guard_flags_a_dropped_accessor_copy(spec: Spec):
    """Non-vacuity: dropping the copy call in any accessor must be caught."""
    sources = _all_sources()
    relpath = spec[1]
    copy_calls: Set[str] = set()
    for _, calls in spec[4]:
        copy_calls.update(calls)

    # Rename the copy calls but leave the functions themselves defined, so the
    # failure is specifically "accessor hands out the cached object by ref".
    lines = []
    for line in sources[relpath].splitlines(keepends=True):
        for name in sorted(copy_calls):
            if f"def {name}(" not in line:
                line = line.replace(f"{name}(", "_copy_removed(")
        lines.append(line)
    sources[relpath] = "".join(lines)

    with pytest.raises(AssertionError, match="no longer copies the cached value"):
        _check(spec, sources)


@pytest.mark.parametrize(
    "spec", [s for s in COPY_GUARDS if s[3] is not None], ids=lambda s: s[0]
)
def test_guard_flags_a_helper_that_stops_copying(spec: Spec):
    """Non-vacuity: a copy helper that stops copying must be caught too."""
    assert spec[3] is not None
    helper_name, markers = spec[3]
    sources = _all_sources()
    relpath = spec[1]
    mutated = sources[relpath]
    for marker in markers:
        mutated = mutated.replace(f"{marker}(", "_no_copy(")
    sources[relpath] = mutated

    with pytest.raises(AssertionError, match="no longer copies"):
        _check(spec, sources)


def test_guard_rejects_dead_nested_copy_evidence():
    """A copy call buried in a never-called nested callable is not protection.

    Regression: ``_called_names`` used ``ast.walk(node)``, which descends into
    nested functions and lambdas. A helper that only *defined* a copier could
    satisfy the guard while handing the shared mutable object out by reference.
    """
    relpath = "src/synthetic_cache.py"
    spec: Spec = (
        "synthetic",
        relpath,
        "_cache",
        ("_copy_thing", ("deepcopy",)),
        (("get_thing", ("_copy_thing",)),),
    )
    dead_nested = textwrap.dedent(
        """
        _cache = {}

        def _copy_thing(value):
            def _never_called():
                return deepcopy(value)
            return value

        def get_thing():
            return _copy_thing(_cache.get("k"))
        """
    )
    dead_lambda = textwrap.dedent(
        """
        _cache = {}

        def _copy_thing(value):
            copier = lambda: deepcopy(value)
            return value

        def get_thing():
            return _copy_thing(_cache.get("k"))
        """
    )
    live_nested = textwrap.dedent(
        """
        _cache = {}

        def _copy_thing(value):
            if value:
                return deepcopy(value)
            return value

        def get_thing():
            return _copy_thing(_cache.get("k"))
        """
    )
    for source in (dead_nested, dead_lambda):
        fn = _functions(source)["_copy_thing"]
        # Confirm the old broad traversal would have credited the dead call.
        assert "deepcopy" in {
            n.func.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert _called_names(fn) == set(), "dead nested copy must not count"
        with pytest.raises(AssertionError, match="no longer copies"):
            _check(spec, {relpath: source})

    # Live evidence behind statement-level nesting still satisfies the guard.
    _check(spec, {relpath: live_nested})


# ---------------------------------------------------------------------------
# Structural sweep: a NOVEL by-reference mutable cache must not escape.
#
# The fixed ``COPY_GUARDS`` tuple above proves each *known* family keeps its
# copy contract, but it cannot see a cache that does not exist yet.  A developer
# adding a new process-wide cache holding, say, a mutable verdict dataclass and
# returning the memoized object from a differently named accessor would leave
# every check above green.  This section discovers cache stores and their
# by-reference accessors from the AST of the whole production tree (no name or
# function is hardcoded as a "known good" accessor), and fails on any accessor
# that hands a cached value straight back to its caller without a copy.
# ---------------------------------------------------------------------------

_SRC_ROOT = Path(__file__).resolve().parents[2]  # .../src
_REPO_ROOT = _SRC_ROOT.parent
_SCAN_EXCLUDED_DIRS = {"tests", "selftest"}

# Module-level mutable stores that act as process-wide caches.  Snapshot
# objects (``VersionedConfigSnapshot``) are intentionally excluded: their
# accessors are already enumerated in ``COPY_GUARDS`` and their value type is
# not discoverable from the store constructor alone.
_CACHE_CTORS = frozenset(
    {
        "dict",
        "OrderedDict",
        "defaultdict",
        "WeakValueDictionary",
        "WeakKeyDictionary",
        "LRUCache",
        "BoundedLRU",
        "BoundedTTLCache",
        "TTLCache",
        "Cache",
        "list",
    }
)


def _is_copy_call_name(name: str) -> bool:
    """Names that prove a cached value was detached before being returned."""
    lowered = name.lower()
    return (
        "copy" in lowered
        or "snapshot" in lowered
        or "clone" in lowered
        or lowered
        in {"dict", "list", "tuple", "set", "frozenset", "replace", "model_dump"}
    )


def _called_call_names(node: ast.AST) -> Set[str]:
    names: Set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _module_cache_names(tree: ast.AST) -> Set[str]:
    """Names bound at module scope to a mutable cache container."""

    def is_cache_ctor(value: ast.AST) -> bool:
        if isinstance(value, (ast.Dict, ast.List)):
            return True
        if isinstance(value, ast.Call):
            func = value.func
            if isinstance(func, ast.Name):
                return func.id in _CACHE_CTORS
            if isinstance(func, ast.Attribute):
                return func.attr in _CACHE_CTORS
        return False

    names: Set[str] = set()
    for stmt in tree.body:
        value = None
        if isinstance(stmt, ast.Assign):
            value = stmt.value
            targets = stmt.targets
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            value = stmt.value
            targets = [stmt.target]
        else:
            continue
        if not is_cache_ctor(value):
            continue
        for tgt in targets:
            if isinstance(tgt, ast.Name) and "cache" in tgt.id.lower():
                names.add(tgt.id)
    return names


def _cache_tainted_locals(fn: ast.AST, caches: Set[str]) -> Set[str]:
    """Locals assigned from a read of one of the module's cache stores."""
    tainted: Set[str] = set()
    for node in ast.walk(fn):
        value = None
        targets: Tuple[ast.AST, ...] = ()
        if isinstance(node, ast.Assign):
            value, targets = node.value, tuple(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value, targets = node.value, (node.target,)
        if value is None:
            continue
        if not ({n.id for n in ast.walk(value) if isinstance(n, ast.Name)} & caches):
            continue
        for tgt in targets:
            if isinstance(tgt, ast.Name):
                tainted.add(tgt.id)
    return tainted


# By-reference cache returns that are already proven safe (no mutable decision
# verdict can be poisoned through them).  Keyed by (source path, accessor) so a
# *new* accessor under a novel name is never covered by an existing entry.
_SAFE_BYREF_SITES: Dict[Tuple[str, str], str] = {
    # Immutable tuple / str / bool / bytes / int verdicts or version tokens.
    ("src/api/media_serving.py", "_response_metadata"): "(str, bool, str) tuple",
    ("src/api/routes/admin/ui.py", "admin_dashboard_css"): "str CSS body",
    ("src/api/routes/avatars/validation.py", "is_serve_safe"): "bool verdict",
    ("src/api/routes/media.py", "_is_allowed_proxy_url"): "bool verdict",
    ("src/api/routes/media.py", "_proxy_config_version"): "tuple version token",
    ("src/api/routes/public_license.py", "_get_public_key_bytes"): "bytes key",
    ("src/api/websocket/compression.py", "_get_limits"): "(int, int) tuple",
    ("src/core/auth/totp.py", "decrypt_totp_secret"): "str secret",
    ("src/core/database/cache/base.py", "mem_cache_clear_pattern"): "int count",
    ("src/core/database/cache/serialization.py", "_serialized_type_tag"): "str tag",
    (
        "src/core/database/cache/serialization.py",
        "_serialized_dataclass_fields",
    ): "tuple[str, ...] type metadata",
    ("src/core/database/dialect.py", "convert_placeholders"): "str SQL",
    ("src/core/push/manager/delivery.py", "_serialized_value_bytes"): "int size",
    ("src/core/telemetry/__init__.py", "_matches_registered_template"): "bool verdict",
    ("src/core/telemetry/__init__.py", "_normalize_endpoint"): "str endpoint",
    ("src/core/tls/chain_validation.py", "validate_certificate_file"): (
        "frozen ValidatedChain dataclass"
    ),
    # Stable metadata / report data whose live security gate is re-evaluated
    # outside the cache, so mutating the returned object cannot widen a gate.
    ("src/api/media_serving.py", "_load_servable_metadata"): (
        "row metadata; scan/deleted gate read fresh on every request"
    ),
    ("src/api/routes/search.py", "_suggestions"): (
        "display suggestions; authorization gate applied before the cache"
    ),
    ("src/core/database/cache/serialization.py", "_enum_fields_for_type"): (
        "type metadata, not a decision verdict"
    ),
    ("src/core/migrations/manager.py", "validate_migration_integrity"): (
        "admin integrity report returned as a response, not fed into a gate"
    ),
    ("src/core/storage_quota/manager.py", "get_manager"): (
        "service object, not a cached verdict"
    ),
}


def _new_cache_findings() -> Dict[str, Set]:
    return {"stores": set(), "copy": set(), "safe": set(), "unclassified": set()}


def _classify_cache_module(
    tree: ast.AST, relpath: str, findings: Dict[str, Set]
) -> None:
    """Classify every cached-value return in one parsed module."""
    caches = _module_cache_names(tree)
    findings["stores"].update((relpath, name) for name in caches)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tainted = _cache_tainted_locals(fn, caches)
        if not tainted:
            continue
        for ret in ast.walk(fn):
            if not isinstance(ret, ast.Return) or ret.value is None:
                continue
            returned = {n.id for n in ast.walk(ret.value) if isinstance(n, ast.Name)}
            if not (returned & tainted):
                continue
            key = (relpath, fn.name)
            if any(_is_copy_call_name(n) for n in _called_call_names(ret.value)):
                findings["copy"].add(key)
            elif key in _SAFE_BYREF_SITES:
                findings["safe"].add(key)
            else:
                findings["unclassified"].add(key)


def _read_cache_source(py: Path) -> str:
    """Read a module, tolerating transient rewrite races in a shared worktree."""
    import time

    last: OSError | None = None
    for _ in range(6):
        try:
            return py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:  # another agent mid-rewrite
            last = exc
            time.sleep(0.05)
    raise last


def _production_cache_findings() -> Dict[str, Set]:
    findings = _new_cache_findings()
    for py in sorted(_SRC_ROOT.rglob("*.py")):
        rel = py.relative_to(_SRC_ROOT)
        if any(part in _SCAN_EXCLUDED_DIRS for part in rel.parts):
            continue
        if py.name.startswith("test_"):
            continue
        _classify_cache_module(
            ast.parse(_read_cache_source(py), filename=str(py)),
            str(py.relative_to(_REPO_ROOT)),
            findings,
        )
    return findings


def test_structural_sweep_has_no_unclassified_byref_cache_returns():
    """A newly added by-reference mutable cache cannot hide under a novel name."""
    f = _production_cache_findings()
    # Non-vacuity: the scanner must actually see real stores, protected
    # accessors and safe by-reference accessors. A scanner that matched nothing
    # would trivially pass the emptiness assertion.
    assert f["stores"], "structural sweep discovered no module-level caches"
    assert f["copy"], "structural sweep discovered no copy-detached accessors"
    assert f["safe"], "structural sweep discovered no by-reference accessors"
    assert not f["unclassified"], (
        "process-wide cache accessor returns a cached value by reference with "
        "no copy and no documented immutability reason: "
        + repr(sorted(f["unclassified"]))
    )


def test_structural_detector_flags_novel_byref_cache():
    """Non-vacuity: a brand-new by-reference cache under a novel name is caught.

    The same accessor with a copy on the read path is classified as protected,
    proving the detector is not vacuously rejecting every cache accessor.
    """
    byref = textwrap.dedent(
        """
        _novel_verdict_cache = {}

        def verdict_for_account(account_id):
            cached = _novel_verdict_cache.get(account_id)
            if cached is not None:
                return cached["verdict"]
            verdict = {"allowed": False, "scopes": []}
            _novel_verdict_cache[account_id] = verdict
            return verdict
        """
    )
    f = _new_cache_findings()
    _classify_cache_module(ast.parse(byref), "<synthetic>", f)
    assert ("<synthetic>", "verdict_for_account") in f["unclassified"], (
        "a novel cache handing out a mutable verdict by reference must be flagged"
    )

    copied = byref.replace(
        'return cached["verdict"]', 'return deepcopy(cached["verdict"])'
    )
    f2 = _new_cache_findings()
    _classify_cache_module(ast.parse(copied), "<synthetic>", f2)
    assert ("<synthetic>", "verdict_for_account") in f2["copy"]
    assert not f2["unclassified"]
