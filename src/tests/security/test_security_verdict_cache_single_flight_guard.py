"""Completeness guard: every security-verdict ``@cached`` coalesces cold misses.

N718 (``d52a2da5``) gave ``has_perm`` a single-flight verdict cache, following
``be77d831`` (``token_verify``) and ``afba24ca`` (``access_token_required``).
A verdict cache that coalesces N concurrent cold misses into one authoritative
computation is an availability fix: without it, one namespace invalidation
(e.g. ``invalidate_pattern("has_perm:*")`` after a role change) makes every
in-flight request re-run the DB-backed resolver for the same key -- a
thundering-herd stampede. The verdict and all authorization semantics are
unchanged, so these caches are safe to coalesce and unsafe to leave unguarded.

This module locks that invariant in with a **definitive static enumeration** of
the committed tree. It walks every production ``src/**/*.py`` blob via
``git show <HEAD>:<blob>`` (never the working tree, which may be contested),
finds every ``@cached``-decorated function (resolving local aliases), and
requires that any *verdict-feeding* cache either carries ``single_flight=True``
or sits in a documented allowlist with a concrete reason.

A cache is verdict-feeding when any of these holds:

  * its ``(path, name)`` is a known verdict pin (the three fixed tiers above);
  * its ``prefix`` is a known verdict namespace (``has_perm``,
    ``token_verify``, ``access_token_required``);
  * it lives in a verdict-bearing module (the session-auth managers,
    ``api_token.py``, ``ip_blacklist.py`` or the role-permission handler),
    so a newly added verdict cache there fails closed until it is classified.

Completeness is asserted both ways: every discovered verdict cache is either
single-flight or allowlisted, and every known/allowlisted key must still exist.
The detector is validated against synthetic sources and proven non-vacuous
before the committed tree is trusted: a synthetic missing-single-flight verdict
cache is flagged while the documented allowlisted cases pass. No app, config,
DB or network is imported.
"""

from __future__ import annotations

import ast
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

# Committed-tree sweep: ~5.5k HEAD blobs per test (20-30s here, and slower on
# a shared runner), so the suite-wide per-test timeout is too tight.
pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.timeout(180),
]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOT = "src"
_TEST_PREFIX = "src/tests/"

# Verdict caches fixed to coalesce cold misses. Each (path, function) must carry
# single_flight=True at HEAD. These are the tiers whose stampede was measured
# and repaired in be77d831 / afba24ca / d52a2da5.
SINGLE_FLIGHT_REQUIRED: dict[tuple[str, str], str] = {
    (
        "src/core/auth/managers/session/verification.py",
        "_verify_token",
    ): "be77d831: session/bot token verification verdict (token_verify)",
    (
        "src/core/auth/managers/api_token.py",
        "is_api_access_token_required",
    ): "afba24ca: API-access-token-required authn gate verdict",
    (
        "src/core/servers/handlers/roles/permissions.py",
        "_compute_has_permission",
    ): "d52a2da5 (N718): role permission verdict (has_perm)",
}

# Verdict namespaces by cache-key prefix. A cache in one of these namespaces is
# verdict-feeding even if its (path, name) is not individually pinned above, so
# a new mirror of a known verdict cannot slip through unclassified.
_VERDICT_PREFIXES = frozenset({"has_perm", "token_verify", "access_token_required"})

# Modules that feed security verdicts. Any @cached added here is treated as
# verdict-feeding by default (fails closed). Kept as exact files or directory
# prefixes so enumeration is deterministic and independent of the working tree.
_VERDICT_FILES = frozenset(
    {
        "src/core/auth/managers/api_token.py",
        "src/core/auth/managers/ip_blacklist.py",
        "src/core/servers/handlers/roles/permissions.py",
    }
)
_VERDICT_DIRS = ("src/core/auth/managers/session/",)

# Documented exceptions: verdict-feeding caches that intentionally do NOT carry
# single_flight. Every entry names a real committed cache and a concrete reason;
# adding one is an explicit, reviewable decision, never a silent escape hatch.
ALLOWLIST: dict[tuple[str, str], str] = {
    (
        "src/core/auth/managers/session/lifecycle.py",
        "_verify_token",
    ): (
        "Legacy token_verify verdict mirror on LifecycleMixin (the cached inner "
        "verifier; LifecycleMixin.verify_token itself is not cached). "
        "SessionMixin's MRO puts VerificationMixin first, so "
        "VerificationMixin._verify_token (which IS single-flight) is the live "
        "verifier; this copy is shadowed/unreachable for the composed manager "
        "and its cache is never the one cold-missed. Keep both verifier copies "
        "code-parity-pinned by the N712 mirror guard; a future single-flight "
        "fix here must land on both copies."
    ),
    (
        "src/core/auth/managers/ip_blacklist.py",
        "is_ip_blocked",
    ): (
        "Fail-open IP-block verdict (ttl=300), invalidated on every block/"
        "unblock write. Security-relevant, but the loader is a single blind-"
        "index SELECT that is cheap and the verdict is fail-open, so cold-miss "
        "coalescing is an optional hardening rather than a stampede fix. "
        "Tracked here explicitly; give it single_flight=True in a dedicated "
        "follow-up and remove this entry rather than adding more exceptions."
    ),
}

# Decorators whose local aliases must resolve back to the canonical ``cached``.
_TRACKED_DECORATORS = frozenset({"cached"})


@dataclass(frozen=True)
class Source:
    """One named Python source blob (path is repo-relative)."""

    path: str
    text: str


@dataclass(frozen=True)
class CachedFn:
    """One discovered ``@cached``-decorated function."""

    path: str
    name: str
    lineno: int
    prefix: str
    single_flight: bool

    @property
    def key(self) -> tuple[str, str]:
        return (self.path, self.name)


# ---------------------------------------------------------------------------
# Git / committed-tree reader
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path = _REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    """Resolve HEAD once per run, then pin every blob read to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _committed_sources(commit: str) -> list[Source]:
    """Enumerate production .py blobs from the committed tree (never the worktree)."""
    proc = _git("ls-tree", "-r", "--name-only", "HEAD", "--", _SOURCE_ROOT)
    assert proc.returncode == 0, f"cannot list HEAD tree: {proc.stderr.strip()}"
    sources: list[Source] = []
    for path in proc.stdout.splitlines():
        if not path.endswith(".py") or path.startswith(_TEST_PREFIX):
            continue
        blob = _git("show", f"{commit}:{path}")
        if blob.returncode != 0:
            continue
        sources.append(Source(path=path, text=blob.stdout))
    return sources


# ---------------------------------------------------------------------------
# AST helpers (alias resolution mirrors SEC700's cached-handler guard)
# ---------------------------------------------------------------------------


def _canonical_names(tree: ast.AST) -> dict[str, str]:
    """Resolve local aliases of ``cached`` (``import as`` / chained assign)."""
    aliases: dict[str, str] = {}

    def resolve(name: str) -> str:
        seen: set[str] = set()
        while name in aliases and name not in seen:
            seen.add(name)
            name = aliases[name]
        return name

    nodes = list(ast.walk(tree))
    for _ in range(len(nodes) + 1):
        changed = False
        for node in nodes:
            pairs: list[tuple[str, str]] = []
            if isinstance(node, ast.ImportFrom):
                pairs = [
                    (alias.asname or alias.name, alias.name)
                    for alias in node.names
                    if alias.name in _TRACKED_DECORATORS
                ]
            elif isinstance(node, ast.Assign):
                value = node.value
                canonical = None
                if isinstance(value, ast.Name):
                    canonical = resolve(value.id)
                elif isinstance(value, ast.Attribute):
                    canonical = resolve(value.attr)
                if canonical in _TRACKED_DECORATORS:
                    pairs = [
                        (target.id, canonical)
                        for target in node.targets
                        if isinstance(target, ast.Name)
                    ]
            for local, canonical in pairs:
                if aliases.get(local) != canonical:
                    aliases[local] = canonical
                    changed = True
        if not changed:
            break
    return aliases


def _cached_decorator(fn, aliases: dict[str, str]):
    """Return the ``@cached`` decorator node on ``fn`` (or None)."""
    for deco in fn.decorator_list:
        func = deco.func if isinstance(deco, ast.Call) else deco
        if isinstance(func, ast.Name) and aliases.get(func.id, func.id) == "cached":
            return deco
        if isinstance(func, ast.Attribute) and func.attr == "cached":
            return deco
    return None


def _prefix_of(deco) -> str:
    if not isinstance(deco, ast.Call):
        return ""
    for kw in deco.keywords:
        if kw.arg == "prefix":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
            return ast.unparse(kw.value)
    return ""


def _single_flight_of(deco) -> bool:
    if not isinstance(deco, ast.Call):
        return False
    for kw in deco.keywords:
        if kw.arg == "single_flight":
            return isinstance(kw.value, ast.Constant) and kw.value.value is True
    return False


def discover(sources) -> dict[tuple[str, str], CachedFn]:
    """Enumerate every ``@cached`` function in ``sources``, keyed by (path, name)."""
    found: dict[tuple[str, str], CachedFn] = {}
    for src in sources:
        try:
            tree = ast.parse(src.text, filename=src.path)
        except SyntaxError:
            continue
        aliases = _canonical_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            deco = _cached_decorator(node, aliases)
            if deco is None:
                continue
            found[(src.path, node.name)] = CachedFn(
                path=src.path,
                name=node.name,
                lineno=node.lineno,
                prefix=_prefix_of(deco),
                single_flight=_single_flight_of(deco),
            )
    return found


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def is_verdict_feeding(cache: CachedFn) -> bool:
    """Whether ``cache`` feeds a security verdict and must be coalesced/allowlisted."""
    return (
        cache.key in SINGLE_FLIGHT_REQUIRED
        or cache.prefix in _VERDICT_PREFIXES
        or cache.path in _VERDICT_FILES
        or any(cache.path.startswith(prefix) for prefix in _VERDICT_DIRS)
    )


def is_unprotected_verdict(
    cache: CachedFn,
    *,
    required: dict[tuple[str, str], str] = SINGLE_FLIGHT_REQUIRED,
    allowlist: dict[tuple[str, str], str] = ALLOWLIST,
) -> bool:
    """True when a verdict cache is single-flight-less AND not documented."""
    if not is_verdict_feeding(cache):
        return False
    if cache.single_flight:
        return False
    return cache.key not in allowlist


def unprotected_at(
    sources,
    *,
    required: dict[tuple[str, str], str] = SINGLE_FLIGHT_REQUIRED,
    allowlist: dict[tuple[str, str], str] = ALLOWLIST,
) -> list[CachedFn]:
    """Verdict caches in ``sources`` that fail the invariant."""
    return [
        c
        for c in discover(sources).values()
        if is_unprotected_verdict(c, required=required, allowlist=allowlist)
    ]


# ---------------------------------------------------------------------------
# Detector self-tests (synthetic sources; validated before trusting HEAD)
# ---------------------------------------------------------------------------


_SYNTHETIC = textwrap.dedent(
    """
    from src.core.database import cached as memo

    @cached(ttl=10, prefix="has_perm", single_flight=True)
    def coalesced():
        ...

    @cached(ttl=10, prefix="has_perm")
    def missing_single_flight():
        ...

    @memo(ttl=30, prefix="access_token_required", single_flight=True)
    def aliased_and_coalesced():
        ...

    @cached(ttl=60)
    def not_a_verdict():
        ...

    def undecorated():
        ...
    """
)


def test_detector_enumerates_cached_and_reads_single_flight() -> None:
    found = discover([Source("synthetic.py", _SYNTHETIC)])
    assert set(found) == {
        ("synthetic.py", "coalesced"),
        ("synthetic.py", "missing_single_flight"),
        ("synthetic.py", "aliased_and_coalesced"),
        ("synthetic.py", "not_a_verdict"),
    }
    assert found[("synthetic.py", "coalesced")].single_flight is True
    assert found[("synthetic.py", "coalesced")].prefix == "has_perm"
    # The aliased decorator is discovered and its flags read identically.
    assert found[("synthetic.py", "aliased_and_coalesced")].single_flight is True
    assert found[("synthetic.py", "aliased_and_coalesced")].prefix == (
        "access_token_required"
    )


def test_synthetic_missing_single_flight_verdict_is_flagged() -> None:
    """A synthetic verdict cache without single-flight is caught (non-vacuity)."""
    synthetic = [
        Source(
            "src/core/servers/handlers/roles/permissions.py",
            textwrap.dedent(
                """
                @cached(ttl=10, prefix="has_perm")
                def _compute_has_permission(self, user_id, server_id):
                    return self.get_permissions(user_id, server_id)
                """
            ),
        )
    ]
    found = discover(synthetic)
    cache = found[
        ("src/core/servers/handlers/roles/permissions.py", "_compute_has_permission")
    ]
    assert is_verdict_feeding(cache)
    assert cache.single_flight is False
    assert is_unprotected_verdict(cache) is True
    assert unprotected_at(synthetic) == [cache]

    # Adding single_flight=True clears the finding through the same evaluator.
    fixed = [
        Source(
            "src/core/servers/handlers/roles/permissions.py",
            textwrap.dedent(
                """
                @cached(ttl=10, prefix="has_perm", single_flight=True)
                def _compute_has_permission(self, user_id, server_id):
                    return self.get_permissions(user_id, server_id)
                """
            ),
        )
    ]
    assert unprotected_at(fixed) == []


def test_synthetic_new_verdict_cache_in_guarded_module_fails_closed() -> None:
    """A brand-new verdict cache in a guarded module is flagged by default."""
    new_cache = [
        Source(
            "src/core/auth/managers/session/verification.py",
            textwrap.dedent(
                """
                @cached(ttl=10, prefix="some_new_verdict")
                def _verify_new_credential(self, token):
                    return self._db.check(token)
                """
            ),
        )
    ]
    found = discover(new_cache)
    cache = found[
        ("src/core/auth/managers/session/verification.py", "_verify_new_credential")
    ]
    assert is_verdict_feeding(cache)  # via _VERDICT_DIRS, not name/prefix
    assert is_unprotected_verdict(cache) is True


def test_synthetic_allowlisted_cases_pass() -> None:
    """The documented allowlist cases pass, but only by explicit decision."""
    synthetic = [
        Source(
            "src/core/auth/managers/session/lifecycle.py",
            textwrap.dedent(
                """
                @cached(ttl=10, prefix="token_verify")
                def _verify_token(self, token):
                    return self.parse_and_check(token)
                """
            ),
        ),
        Source(
            "src/core/auth/managers/ip_blacklist.py",
            textwrap.dedent(
                """
                @cached(ttl=300, key_builder=_ip_blocked_cache_key)
                def is_ip_blocked(self, ip_address):
                    return False
                """
            ),
        ),
    ]
    found = discover(synthetic)
    assert set(found) == set(ALLOWLIST)
    for cache in found.values():
        assert is_verdict_feeding(cache)
        assert cache.single_flight is False
        # Allowlisted -> not a finding through the exact production evaluator.
        assert is_unprotected_verdict(cache) is False
    assert unprotected_at(synthetic) == []

    # Remove the allowlist and the same caches become findings (not vacuous).
    assert unprotected_at(synthetic, allowlist={}) != []


def test_registry_and_allowlist_are_well_formed() -> None:
    assert SINGLE_FLIGHT_REQUIRED, "known verdict registry must not be empty"
    assert not (set(SINGLE_FLIGHT_REQUIRED) & set(ALLOWLIST)), (
        "a cache cannot be both single-flight-required and allowlisted"
    )
    for key, reason in SINGLE_FLIGHT_REQUIRED.items():
        assert isinstance(reason, str) and reason.strip(), key
    for key, reason in ALLOWLIST.items():
        assert isinstance(reason, str) and len(reason.strip()) >= 40, (
            f"allowlist entry {key} needs a concrete documented reason"
        )


# ---------------------------------------------------------------------------
# Real committed-tree invariant
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def committed(commit_sha: str) -> dict[tuple[str, str], CachedFn]:
    return discover(_committed_sources(commit_sha))


def test_enumeration_is_non_vacuous(committed) -> None:
    # The scan must see a real, sizeable population, including verdict caches.
    assert len(committed) >= 40, sorted(committed)
    verdict_caches = [c for c in committed.values() if is_verdict_feeding(c)]
    assert len(verdict_caches) >= len(SINGLE_FLIGHT_REQUIRED), sorted(committed)
    # The classifier must discriminate: at least one ordinary cached data read
    # is correctly NOT treated as a verdict cache.
    ordinary = [c for c in committed.values() if not is_verdict_feeding(c)]
    assert ordinary, "classifier marks every cache as a verdict (vacuous)"


def test_known_verdict_caches_are_present_and_single_flight(committed) -> None:
    """Every pinned verdict fix is live at HEAD with single_flight=True."""
    for key, why in SINGLE_FLIGHT_REQUIRED.items():
        cache = committed.get(key)
        assert cache is not None, (
            f"verdict cache {key} missing from committed tree: {why}"
        )
        assert cache.single_flight is True, (
            f"{key}: single_flight=True missing from committed blob "
            f"{cache.path}:{cache.lineno} ({why})"
        )


def test_allowlist_keys_exist_in_committed_tree(committed) -> None:
    """Allowlist entries name real caches, so none can be a dead escape hatch."""
    for key, reason in ALLOWLIST.items():
        assert key in committed, f"allowlist key {key} not found at HEAD: {reason}"


def test_no_unprotected_verdict_cache_in_committed_tree(committed) -> None:
    """Every verdict cache at HEAD is single-flight or documented (fails closed)."""
    wrong = [c for c in committed.values() if is_unprotected_verdict(c)]
    assert not wrong, (
        "security verdict caches without single_flight and without a documented "
        "allowlist entry:\n  "
        + "\n  ".join(
            f"{c.path}:{c.lineno} {c.name} (prefix={c.prefix!r})" for c in wrong
        )
    )


def test_reader_uses_committed_blob_not_stale_working_tree(tmp_path: Path) -> None:
    """Non-vacuity: discovery reads HEAD, so a stale worktree cannot fool it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "fix.py"
    target.write_text(
        '@cached(ttl=10, prefix="has_perm", single_flight=True)\n'
        "def _compute_has_permission(self):\n"
        "    return True\n",
        encoding="utf-8",
    )
    assert _git("init", "-q", cwd=repo).returncode == 0
    assert _git("add", "fix.py", cwd=repo).returncode == 0
    committed = _git(
        "-c",
        "user.email=guard@example.invalid",
        "-c",
        "user.name=guard",
        "commit",
        "-qm",
        "fix",
        cwd=repo,
    )
    assert committed.returncode == 0, committed.stderr
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    assert sha

    # Peer-style stale working copy: single-flight is gone from disk only.
    target.write_text(
        '@cached(ttl=10, prefix="has_perm")\n'
        "def _compute_has_permission(self):\n"
        "    return True\n",
        encoding="utf-8",
    )
    blob = _git("show", f"{sha}:fix.py", cwd=repo)
    assert blob.returncode == 0
    found = discover([Source("fix.py", blob.stdout)])
    assert found[("fix.py", "_compute_has_permission")].single_flight is True
