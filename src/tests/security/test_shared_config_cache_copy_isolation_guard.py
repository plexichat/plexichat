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
"""

from __future__ import annotations

import ast
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


def _called_names(node: ast.AST) -> Set[str]:
    """Names/callable attributes invoked anywhere in a function body."""
    names: Set[str] = set()
    for child in ast.walk(node):
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
    assert len(labels) == len(set(labels)) == 6, (
        f"expected the six shared config/metadata cache families, got {labels}"
    )
    assert set(labels) == {
        "capability verdicts",
        "admin host allowlist",
        "profile bulk",
        "emoji catalog",
        "tier-limit verdicts",
        "API config",
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
