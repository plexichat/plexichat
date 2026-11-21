"""Meta-guard: critical security fixes must stay present in the current tree.

Follow-up to the N467 silent-regression audit.  That audit confirmed every
known fix-marker was present at HEAD and restored the one real regression it
found.  The vulnerability class it defends against is *silent* regression: a
later commit removes a security fix (or guts the static guard that enforces
it) without any existing test noticing.

This module turns the audit's findings into a single, documented registry
(:data:`FIX_MARKERS`).  Each entry names a file and a stable text marker that
*is* the fix (or the helper that keeps a guard non-vacuous).  A parametrized
test asserts every marker is still present in the working tree, so an
accidental revert breaks CI with a message naming the missing fix.

Scope is deliberately narrow -- only critical, stable fixes, not every helper:

* credential/bot-token guards and the cache keys they protect,
* owner-row type-confusion checks (``type(x) is int``),
* the atomic guarded INSERT that closes a quota TOCTOU,
* the AST helpers (``_own_body_nodes`` / ``_canonical_names``) that keep the
  most important static sweeps from passing vacuously.

A follow-up audit of the rest of the campaign added four more high-value,
stable classes, each with its own downstream regression test:

* fail-closed query-cache invalidation for CTE/comment/dollar-quote/stacked
  writes (``_query_mutates_data``) so stale authz verdicts are never served,
* the shared upper-bound clamp (``clamp_page_limit``) that keeps every dedup
  listing's ``LIMIT`` bounded and fails closed to an empty page,
* the SSRF/DNS-rebinding pin (``pinned_urlopen``) used by all outbound fetchers,
* the SEC500 fail-closed-participation ratchet marker whose invariant string
  keeps the static sweep from passing vacuously.

A documented ``moved_to`` successor path is tolerated so a benign file rename
does not produce a false positive.  Non-vacuous self-tests prove that a
synthetic removal of a marker (and a missing file) are actually flagged, and
that the documented rename tolerance works.

``absent`` markers and negative assertions
------------------------------------------
Some sub-fixes are defined by the *absence* of a vulnerable pattern (e.g. the
query-cache classifier must NOT treat ``#`` as a comment, and must NOT list
``EXPLAIN`` as a read-only prefix).  Those entries set ``absent=True``; the
evaluator inverts the check and fails when the pattern is *found*.  An optional
``scope=(start_anchor, end_anchor)`` restricts the search to one lexical block,
so a pattern that is legitimately discussed in an adjacent explanatory comment
does not create a false positive.  Non-vacuity is proven from the other side:
a synthetic blob that REINTRODUCES the pattern must be flagged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class FixMarker:
    """One critical fix and where its marker is expected to live.

    ``path`` is repo-relative.  ``moved_to`` lists documented successor paths
    tolerated when the original file is renamed/relocated.  ``why`` records the
    security purpose so a future maintainer knows what they are protecting.

    ``absent=True`` inverts the check: the entry passes while ``marker`` is
    absent and FAILS if the vulnerable pattern reappears.  ``scope`` optionally
    bounds the search to the text between two literal anchors (used when the
    pattern is also named in an explanatory comment on the same file).
    """

    key: str
    path: str
    marker: str
    why: str
    moved_to: tuple[str, ...] = field(default_factory=tuple)
    absent: bool = False
    scope: tuple[str, str] | None = None

    @property
    def candidates(self) -> tuple[str, ...]:
        return (self.path,) + self.moved_to


# The single documented registry.  Derived from the N467 audit of stable,
# critical fixes; each marker was verified present in the tree when written.
FIX_MARKERS: tuple[FixMarker, ...] = (
    FixMarker(
        key="bot-token-owner-guard-helper",
        path="src/api/routes/users/helpers.py",
        marker="def require_user_token(",
        why="rejects bot credentials on human-account /@me handlers",
        # Documented rename target; tolerated if helpers.py is ever split up.
        moved_to=("src/api/routes/users/_token_guards.py",),
    ),
    FixMarker(
        key="credential-partitioned-cache-key",
        path="src/api/routes/users/helpers.py",
        marker="def authenticated_cache_key(",
        why="partitions private cache entries by credential (bot vs owner)",
    ),
    FixMarker(
        key="relationship-listing-cache-credential-isolation",
        path="src/api/routes/relationships/listing.py",
        marker="key_builder=authenticated_cache_key(",
        why="keeps relationship listings isolated across credentials",
    ),
    FixMarker(
        key="messages-list-cache-credential-isolation",
        path="src/api/routes/messages/messages_list.py",
        marker="key_builder=authenticated_cache_key(",
        why="keeps message/pin listings isolated across credentials",
    ),
    FixMarker(
        key="owner-row-type-confusion-exemptions",
        path="src/core/automod/manager/exemptions.py",
        marker="type(owner_id) is int",
        why="prevents bool/subclass owner ids bypassing the owner check",
    ),
    FixMarker(
        key="member-id-type-confusion-user-search",
        path="src/core/search/manager/user_search.py",
        marker="type(uid) is int",
        why="rejects non-int member ids in scoped user search",
    ),
    FixMarker(
        key="webhook-quota-guarded-insert",
        path="src/core/webhooks/manager/crud.py",
        marker="_ATOMIC_WEBHOOK_INSERT_SQL",
        why="atomic guarded INSERT closes the webhook quota TOCTOU",
    ),
    FixMarker(
        key="admin-cache-key-guard-helper",
        path="src/tests/security/test_sec600_admin_cache_key_invariant.py",
        marker="def _own_body_nodes(",
        why="keeps the admin cache-key static sweep non-vacuous",
    ),
    FixMarker(
        key="cached-route-authz-guard-helper",
        path="src/tests/security/test_sec700_cached_route_authz_completeness.py",
        marker="def _canonical_names(",
        why="keeps the cached-route authz sweep non-vacuous",
        # Documented rename target for the sweep test.
        moved_to=("src/tests/security/test_cached_route_authz_canonical_names.py",),
    ),
    FixMarker(
        key="sql-builder-injection-guard-helper",
        path="src/tests/servers/test_update_builder_sql_guard.py",
        marker="def _own_body_nodes(",
        why="keeps the SQL-builder injection guard non-vacuous",
    ),
    # --- Follow-up audit: additional distinct critical fixes from the campaign ---
    FixMarker(
        key="query-cache-write-classification",
        path="src/core/database/core/execution.py",
        marker="def _query_mutates_data(",
        why=(
            "fail-closed classification of CTE, comment/dollar-quote-prefixed, "
            "MERGE/TRUNCATE and stacked writes so the query cache never serves "
            "a stale authorization verdict after a write"
        ),
    ),
    # --- Query-cache classifier sub-fixes (seven distinct fixes) ---
    # The coarse ``query-cache-write-classification`` entry above pins only the
    # enclosing function.  These entries pin the individual hardening steps so
    # a refactor that silently drops one of them is caught by name.
    FixMarker(
        key="query-cache-escape-string-pattern",
        path="src/core/database/core/execution.py",
        marker=r"\b[Ee]'(?:[^'\\]|\\.|'')*'",
        why=(
            "backslash-aware [Ee] escape-string alternative in _SQL_NON_CODE_RE; "
            "without it an escape-quoted quote pairs with a later quote and a "
            "stacked write separator is swallowed, so a write stays a 'read' "
            "and stale authz verdicts keep being served"
        ),
    ),
    FixMarker(
        key="query-cache-line-comment-cr-aware",
        path="src/core/database/core/execution.py",
        marker=r"--[^\n\r]*",
        why=(
            "the SQL line-comment alternative stops at both newline characters; "
            "a \\n-only variant lets a CR terminate the PostgreSQL comment early "
            "and exposes a stacked write separator the cache must see"
        ),
    ),
    FixMarker(
        key="query-cache-hash-not-comment",
        path="src/core/database/core/execution.py",
        marker=r"#[^\n]*",
        why=(
            "'#' must NOT be neutralised as a comment: on PostgreSQL it is the "
            "integer-XOR operator, so stripping a '#' tail would hide a stacked "
            "'; UPDATE ...' from the separator count. Scoped to the regex block "
            "because the same text appears in the adjacent rationale comment."
        ),
        absent=True,
        scope=("_SQL_NON_CODE_RE = re.compile(", "re.DOTALL,"),
    ),
    FixMarker(
        key="query-cache-explain-not-read-prefix",
        path="src/core/database/core/execution.py",
        marker='"EXPLAIN",',
        why=(
            "EXPLAIN must stay out of _QUERY_CACHE_READ_PREFIXES: PostgreSQL "
            "EXPLAIN ANALYZE executes its target, so a leading EXPLAIN would let "
            "a committed write keep the query cache serving a stale verdict"
        ),
        absent=True,
    ),
    FixMarker(
        key="query-cache-multiple-statement-check",
        path="src/core/database/core/execution.py",
        marker="def _has_multiple_statements(",
        why=(
            "counts top-level separators after neutralising literals, quoted "
            "bodies and comments, so a write stacked behind a read is treated "
            "as a write and the cache is invalidated"
        ),
    ),
    FixMarker(
        key="query-cache-dollar-quote-neutralization",
        path="src/core/database/core/execution.py",
        marker=r"\$\$.*?\$\$|\$(?P<dollar_tag>",
        why=(
            "dollar-quoted bodies ($$...$$ and $tag$...$tag$) are blanked as a "
            "unit so a lone quote inside one cannot pair with an outside quote "
            "and swallow a genuine stacked-write separator"
        ),
    ),
    FixMarker(
        key="dedup-listing-limit-clamp",
        path="src/core/media/deduplication/paging.py",
        marker="def clamp_page_limit(",
        why=(
            "shared upper-bound clamp for every dedup listing LIMIT; negative "
            "or oversized values previously became unbounded reads (LIMIT -1)"
        ),
    ),
    FixMarker(
        key="outbound-ssrf-pinned-fetch",
        path="src/utils/outbound.py",
        marker="def pinned_urlopen(",
        why=(
            "validates the URL once and connects to the screened IP (SNI/Host "
            "carry the hostname) with no second DNS lookup or redirects, closing "
            "SSRF/DNS-rebinding across all outbound fetchers"
        ),
    ),
    FixMarker(
        key="messaging-fail-closed-participation-ratchet",
        path="src/tests/security/test_sec500_messaging_fail_closed_writes.py",
        marker='FAIL_CLOSED_MESSAGE = "Cannot verify conversation participation"',
        why=(
            "invariant string that keeps the SEC500 static sweep non-vacuous: "
            "messaging write paths must deny when the participation checker is "
            "unwired/absent instead of silently skipping the check"
        ),
    ),
)

# The four entries added by the follow-up audit.  Kept explicit so the
# per-entry non-vacuity self-test below covers each of them by name.
ADDED_MARKER_KEYS: frozenset[str] = frozenset(
    {
        "query-cache-write-classification",
        "dedup-listing-limit-clamp",
        "outbound-ssrf-pinned-fetch",
        "messaging-fail-closed-participation-ratchet",
    }
)

# The fine-grained query-cache classifier sub-fixes.  Kept explicit so every
# one of them gets a dedicated non-vacuity self-test below.
QUERY_CACHE_MARKER_KEYS: frozenset[str] = frozenset(
    {
        "query-cache-escape-string-pattern",
        "query-cache-line-comment-cr-aware",
        "query-cache-hash-not-comment",
        "query-cache-explain-not-read-prefix",
        "query-cache-multiple-statement-check",
        "query-cache-dollar-quote-neutralization",
    }
)


def _read_text(repo_relative: str) -> str:
    """Read a registry-relative file from the current tree."""
    return (_REPO_ROOT / repo_relative).read_text(encoding="utf-8")


def _scoped_text(text: str, record: FixMarker) -> str | None:
    """Return only the region between ``record.scope`` anchors, or ``None``.

    ``None`` is treated as a failure by the evaluator so a missing scope can
    never make a negative assertion pass vacuously.
    """
    if record.scope is None:
        return text
    start_anchor, end_anchor = record.scope
    start = text.find(start_anchor)
    if start < 0:
        return None
    end = text.find(end_anchor, start + len(start_anchor))
    if end < 0:
        return None
    return text[start:end]


def _evaluate(
    record: FixMarker, reader=_read_text
) -> tuple[bool, str]:
    """Return ``(present, detail)`` for one fix marker.

    ``reader`` is injectable so the self-tests can simulate a marker being
    removed/reintroduced or a file being moved without touching the real tree.
    For ``absent`` records the polarity is inverted: the entry passes only
    while the vulnerable pattern is NOT found in the (optional) scope.
    """
    checked: list[str] = []
    for rel in record.candidates:
        try:
            text = reader(rel)
        except (OSError, UnicodeDecodeError) as exc:
            checked.append(f"{rel} (unreadable: {exc.__class__.__name__})")
            continue
        searched = _scoped_text(text, record)
        if searched is None:
            checked.append(f"{rel} (scope {record.scope!r} not found)")
            continue
        found = record.marker in searched
        if record.absent:
            if not found:
                return True, (
                    f"absent marker {record.marker} for {record.key!r} "
                    f"confirmed absent in {rel}"
                )
            checked.append(f"{rel} (VULNERABLE pattern {record.marker} present)")
        else:
            if found:
                return True, f"marker {record.marker} for {record.key!r} present in {rel}"
            checked.append(rel)

    if record.absent:
        location = f"must be absent at {record.path!r}"
    else:
        location = f"expected at {record.path!r}"
    if record.moved_to:
        location += f" or documented successor(s) {record.moved_to!r}"
    if record.absent:
        detail = (
            f"SECURITY FIX MARKER VIOLATED: {record.key!r} -- vulnerable pattern "
            f"{record.marker} was REINTRODUCED ({location}); checked: "
            + ", ".join(checked)
            + f". Purpose: {record.why}"
        )
    else:
        detail = (
            f"SECURITY FIX MARKER MISSING: {record.key!r} -- "
            f"{record.marker} not found ({location}); checked: "
            + ", ".join(checked)
            + f". Purpose: {record.why}"
        )
    return False, detail


@pytest.mark.parametrize("record", FIX_MARKERS, ids=[r.key for r in FIX_MARKERS])
def test_critical_fix_marker_present(record: FixMarker) -> None:
    """Every documented critical fix must still be present in the tree."""
    present, detail = _evaluate(record)
    assert present, detail


def test_registry_is_documentable_and_unique() -> None:
    """The single table is well-formed (no empty/duplicate entries)."""
    keys = [r.key for r in FIX_MARKERS]
    assert keys, "registry must not be empty"
    assert len(keys) == len(set(keys)), "duplicate fix keys in registry"
    for record in FIX_MARKERS:
        assert record.marker, f"empty marker for {record.key!r}"
        assert record.path.startswith("src/"), f"bad path for {record.key!r}"
        assert record.why, f"undocumented purpose for {record.key!r}"


def test_added_keys_are_registered() -> None:
    """The follow-up keys name real, unique registry entries."""
    keys = [r.key for r in FIX_MARKERS]
    assert ADDED_MARKER_KEYS, "added-key set must not be empty"
    assert ADDED_MARKER_KEYS <= set(keys), (
        "added-key set references keys missing from FIX_MARKERS: "
        f"{sorted(ADDED_MARKER_KEYS - set(keys))}"
    )


@pytest.mark.parametrize("key", sorted(ADDED_MARKER_KEYS))
def test_added_marker_synthetic_removal_is_flagged(key: str) -> None:
    """Non-vacuous per-entry check: removing each new marker must be flagged.

    The parametrized ``test_critical_fix_marker_present`` proves each marker is
    *present*; this proves the detector actually *cares* when that specific
    marker disappears (rather than passing vacuously on an unchecked path).
    """
    record = next(r for r in FIX_MARKERS if r.key == key)
    present_in_tree, _ = _evaluate(record)
    assert present_in_tree, f"added marker {key!r} is not present in the tree"

    def reader_without_marker(rel: str) -> str:
        return _read_text(rel).replace(record.marker, "")

    present, detail = _evaluate(record, reader_without_marker)
    assert not present, f"synthetic removal of {key!r} was not detected"
    assert record.key in detail and record.marker in detail


def test_query_cache_keys_are_registered() -> None:
    """The fine-grained classifier keys name real, unique registry entries."""
    keys = [r.key for r in FIX_MARKERS]
    assert QUERY_CACHE_MARKER_KEYS <= set(keys), (
        "query-cache key set references keys missing from FIX_MARKERS: "
        f"{sorted(QUERY_CACHE_MARKER_KEYS - set(keys))}"
    )
    for key in QUERY_CACHE_MARKER_KEYS:
        record = next(r for r in FIX_MARKERS if r.key == key)
        assert record.path == "src/core/database/core/execution.py"


def test_absent_marker_scopes_resolve() -> None:
    """Every scoped negative assertion resolves to a real, non-empty region."""
    for record in FIX_MARKERS:
        if not (record.absent and record.scope):
            continue
        region = _scoped_text(_read_text(record.path), record)
        assert region, f"scope {record.scope!r} did not resolve for {record.key!r}"
        assert record.marker not in region, (
            f"vulnerable pattern is already present in the current scope "
            f"of {record.key!r}"
        )


@pytest.mark.parametrize("key", sorted(QUERY_CACHE_MARKER_KEYS))
def test_query_cache_marker_non_vacuity(key: str) -> None:
    """Each sub-fix is live in the tree AND the guard reacts when it is undone.

    Positive markers are tested by synthetic removal (the fix disappears).
    ``absent`` markers are tested from the other side: a synthetic blob that
    REINTRODUCES the vulnerable pattern must be flagged, otherwise the negative
    assertion would be vacuous.
    """
    record = next(r for r in FIX_MARKERS if r.key == key)
    present_in_tree, _ = _evaluate(record)
    assert present_in_tree, f"query-cache marker {key!r} is not present in the tree"

    if record.absent:

        def reader_reintroduces(rel: str) -> str:
            text = _read_text(rel)
            if record.scope is None:
                return text + f"\n{record.marker}\n"
            start_anchor, _end_anchor = record.scope
            return text.replace(
                start_anchor, f"{start_anchor}\n{record.marker}\n", 1
            )

        present, detail = _evaluate(record, reader_reintroduces)
        assert not present, (
            f"reintroduction of vulnerable pattern for {key!r} was not detected"
        )
        assert record.key in detail and record.marker in detail
        assert "REINTRODUCED" in detail
    else:

        def reader_without_marker(rel: str) -> str:
            return _read_text(rel).replace(record.marker, "")

        present, detail = _evaluate(record, reader_without_marker)
        assert not present, f"synthetic removal of {key!r} was not detected"
        assert record.key in detail and record.marker in detail


def test_selftest_synthetic_removal_is_flagged() -> None:
    """Non-vacuous: stripping a marker from every candidate makes us fail."""
    record = FIX_MARKERS[0]

    def reader_without_marker(rel: str) -> str:
        return _read_text(rel).replace(record.marker, "")

    present, detail = _evaluate(record, reader_without_marker)
    assert not present, "synthetic removal must be detected"
    assert record.key in detail and record.marker in detail


def test_selftest_missing_file_is_flagged() -> None:
    """A missing expected file fails with a clear, named message."""
    record = FixMarker(
        key="synthetic-missing-file",
        path="src/tests/security/_does_not_exist_meta_guard.py",
        marker="NEVER_PRESENT",
        why="self-test only",
    )
    present, detail = _evaluate(record)
    assert not present
    assert record.path in detail and "unreadable" in detail


def test_selftest_moved_to_path_is_tolerated() -> None:
    """A documented rename (marker moved to successor) is accepted."""
    record = next(r for r in FIX_MARKERS if r.moved_to)
    successor = record.moved_to[0]

    def reader_after_move(rel: str) -> str:
        if rel == record.path:
            return "# marker deliberately removed from old path\n"
        if rel == successor:
            return f"# relocated fix\n{record.marker}\n"
        return _read_text(rel)

    present, detail = _evaluate(record, reader_after_move)
    assert present, detail
    assert successor in detail
