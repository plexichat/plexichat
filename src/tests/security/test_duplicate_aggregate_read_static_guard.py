"""Static guard against re-introducing duplicate aggregate reads.

Two perf folds in this campaign removed a *second* round trip that read the
same rows as a query already on the hot path:

* ``242127f9`` -- the access-token detail endpoint used to issue its own
  ``COUNT(DISTINCT ip_index)/SUM(denied)`` summary over
  ``auth_api_access_token_events WHERE token_id = ?`` while the row converter it
  called re-issued the same aggregate for the nested token.
* ``62a488b8`` -- the storage-quota status snapshot used to LEFT JOIN
  ``user_storage_quotas`` on ``user_id`` while ``get_override`` re-read that
  same row with a dedicated ``WHERE user_id = ?`` SELECT.

Both folds replaced a second read with a threaded value. A later refactor that
drops the threading (or adds the second SELECT back) silently restores the
extra round trip; runtime tests only see the queries a fixture happens to run.
This guard reads the SQL out of the source with AST and flags a function that
issues two aggregate SELECTs with the same table set, the same WHERE predicate
and the same GROUP BY over the same scope -- i.e. the exact duplicate shape
those folds removed.

To keep it reliable (no brittle guard), a pair is only flagged when the two
queries also aggregate *the same subject* (they share a non-``COUNT(*)``
aggregate target, or both are plain ``COUNT(*)``). Queries that differ in
table, WHERE predicate or GROUP BY -- including the documented batched/
grouped breakdowns that legitimately coexist in the token-detail endpoint --
are deliberately not flagged.

The guard is non-vacuous: parametrized self-tests feed it the *real* SQL from
the two folded functions and assert a synthetic duplicate is flagged while the
legitimately-different breakdown queries in the very same function are not.
It never edits files on disk; all mutations are in-memory strings.

Known limitation (kept narrow on purpose): extraction handles literal SQL
constants with simple ``COUNT/SUM/AVG/MIN/MAX(expr)`` aggregates whose argument
contains no nested parentheses. F-string-built SQL and nested aggregate
arguments are not parsed as full reads; the documented fold functions use
literal SQL with flat aggregate arguments, so those are covered.

Coverage is two-layered. The scoped checks below protect the two audited fold
functions and their threading (where this campaign actually removed a read). A
whole-source-tree backstop additionally scans every production module and FAILS
on any duplicate aggregate read it cannot classify, so a later fold introduced
in a new function/file cannot silently reintroduce an extra round trip. The
only classified exceptions are legitimate "count before / count after a
mutation" checks (``core/erasure.py`` and ``application_crud.py``), listed in
``_KNOWN_LEGITIMATE_COUNTS``; test fixtures are out of scope because they
legitimately count before/after mutations. The backstop's self-test injects a
fresh duplicate at a brand-new site and proves it is flagged, so the guard is
not vacuous for unclassified folds.
"""

import ast
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]

TOKEN_REL = "core/auth/managers/api_token.py"
STORAGE_REL = "core/storage_quota/manager.py"

pytestmark = [pytest.mark.unit, pytest.mark.security]


# --- SQL extraction -------------------------------------------------------

_AGG_RE = re.compile(r"\b(count|sum|avg|min|max)\s*\(([^)]*)\)", re.I)
_FROM_RE = re.compile(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)", re.I)
_WHERE_RE = re.compile(
    r"\bwhere\b(.*?)(?=\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\blimit\b|$)",
    re.I | re.S,
)
_GROUP_RE = re.compile(
    r"\bgroup\s+by\b(.*?)(?=\border\s+by\b|\bhaving\b|\blimit\b|$)",
    re.I | re.S,
)


def _norm(text: str) -> str:
    """Whitespace-insensitive, case-folded form for comparing SQL fragments."""
    return " ".join(text.split()).lower()


@dataclass(frozen=True)
class AggregateRead:
    """One aggregate SELECT literal found in a function's own source."""

    owner: str
    tables: frozenset[str]
    where: tuple[str, ...]
    group_by: tuple[str, ...]
    subjects: frozenset[str]
    sql: str

    @property
    def key(self) -> tuple:
        return (self.tables, self.where, self.group_by)


@dataclass(frozen=True)
class DuplicateRead:
    owner: str
    first: AggregateRead
    second: AggregateRead


def _own_string_constants(node: ast.AST):
    """Yield string constants under ``node``, stopping at nested definitions.

    A function's reads are only its own; a nested function's SQL belongs to
    that nested function and is inspected separately.
    """
    stack = list(ast.iter_child_nodes(node))
    while stack:
        child = stack.pop()
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(child))
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.value


def _read_from_sql(sql: str, owner: str) -> AggregateRead | None:
    norm = _norm(sql)
    if "select" not in norm or not _AGG_RE.search(norm):
        return None
    tables = frozenset(t.lower() for t in _FROM_RE.findall(norm))
    if not tables:
        return None
    return AggregateRead(
        owner=owner,
        tables=tables,
        where=tuple(_norm(w) for w in _WHERE_RE.findall(norm)),
        group_by=tuple(_norm(g) for g in _GROUP_RE.findall(norm)),
        subjects=frozenset(_norm(m.group(2)) for m in _AGG_RE.finditer(norm)),
        sql=norm,
    )


def extract_reads(source: str) -> dict[str, list[AggregateRead]]:
    """Map each function name to the aggregate reads in its own source."""
    tree = ast.parse(source)
    reads: dict[str, list[AggregateRead]] = {}

    module_reads = [
        read
        for sql in _own_string_constants(tree)
        if (read := _read_from_sql(sql, "<module>")) is not None
    ]
    if module_reads:
        reads["<module>"] = module_reads

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_reads = [
                read
                for sql in _own_string_constants(node)
                if (read := _read_from_sql(sql, node.name)) is not None
            ]
            if func_reads:
                reads[node.name] = func_reads
    return reads


def _same_aggregate_subject(a: AggregateRead, b: AggregateRead) -> bool:
    """True when both reads aggregate the same metric, not just the same rows.

    ``COUNT(*)`` is treated as a wildcard: two plain counts, or two counts of
    the same concrete expression, are the duplicate shape. A plain count
    paired with a different concrete metric (e.g. ``SUM(bytes)``) is a
    legitimately different aggregate and is not flagged.
    """
    sa, sb = a.subjects - {"*"}, b.subjects - {"*"}
    if sa and sb:
        return bool(sa & sb)
    if not sa and not sb:
        return True  # both plain COUNT(*)
    return False


def find_duplicate_reads(reads: list[AggregateRead]) -> list[DuplicateRead]:
    """Flag pairs of reads with the same table/WHERE/GROUP and same metric."""
    duplicates = []
    for i, first in enumerate(reads):
        for second in reads[i + 1 :]:
            if first.key != second.key:
                # Different table, WHERE predicate or GROUP BY (including the
                # documented grouped breakdowns) is an intentional distinction.
                continue
            if not _same_aggregate_subject(first, second):
                continue
            duplicates.append(DuplicateRead(first.owner, first, second))
    return duplicates


def scan_source(source: str) -> dict[str, list[DuplicateRead]]:
    """Return {function: duplicates} for every function with a duplicate read."""
    return {
        owner: dups
        for owner, reads in extract_reads(source).items()
        if (dups := find_duplicate_reads(reads))
    }


# --- Real-source helpers --------------------------------------------------


@lru_cache(maxsize=None)
def _source(rel_path: str) -> str:
    return (SRC / rel_path).read_text(encoding="utf-8")


def _source_modules() -> list[str]:
    """Relative paths of production (non-test) Python modules under ``src/``."""
    return sorted(
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "tests" not in path.relative_to(SRC).parts
    )


def _function_source(rel_path: str, name: str) -> str:
    source = _source(rel_path)
    node = next(
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    segment = ast.get_source_segment(source, node)
    assert segment is not None
    return segment


def _sql_from_function(rel_path: str, name: str, fingerprint: str) -> str:
    """The single SQL literal in ``name`` containing ``fingerprint``."""
    source = _source(rel_path)
    node = next(
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    matches = [s for s in _own_string_constants(node) if _norm(fingerprint) in _norm(s)]
    assert len(matches) == 1, (
        f"{rel_path}:{name}: expected exactly one SQL literal containing "
        f"{fingerprint!r}, found {len(matches)}"
    )
    return matches[0]


def _synthetic_function(sql_a: str, sql_b: str) -> str:
    """Build a synthetic function issuing two reads against the given SQL."""
    return (
        "def synthetic(uid):\n"
        f"    first = db.fetch_one({sql_a!r}, (uid,))\n"
        f"    second = db.fetch_one({sql_b!r}, (uid,))\n"
        "    return first, second\n"
    )


# The functions that own the two folds. Each must stay free of an internal
# duplicate read; the threading assertions below guard the cross-function
# shape (where the second read used to live in a callee).
FOLD_FUNCTIONS = (
    (
        "token usage fold",
        TOKEN_REL,
        (
            "list_api_access_tokens",
            "get_api_access_token",
            "get_api_access_token_usage",
            "_row_to_access_token",
        ),
    ),
    (
        "storage override fold",
        STORAGE_REL,
        (
            "_status_snapshot",
            "_override_row",
            "get_status",
            "get_override",
            "resolve_limit",
        ),
    ),
)


# --- Whole-tree backstop ---------------------------------------------------

# Legitimate repeated COUNT(*) reads, explicitly classified so the backstop can
# fail on anything it cannot classify. Each entry is a count-before / count-after
# mutation check (a DELETE, or a fast-fail before a guarded INSERT), never the
# render-time duplicate aggregate these folds removed:
#   * create_application -- fast-fail quota count and the authoritative guarded-
#     INSERT count both filter app_applications by owner.
#   * purge_user_notifications -- notif_notifications counted before and after
#     the DELETE to report how many rows were purged.
# Entry = (rel_path, owning function, shared read key). Both reads must also be
# plain COUNT(*) so a future concrete-metric duplicate in these functions is
# still flagged rather than silently allowlisted.
_KNOWN_LEGITIMATE_COUNTS = {
    (
        "core/applications/manager/application_crud.py",
        "create_application",
        (frozenset({"app_applications"}), ("owner_id = ?",), ()),
    ),
    (
        "core/erasure.py",
        "purge_user_notifications",
        (frozenset({"notif_notifications"}), (), ()),
    ),
}


def _is_plain_count(read: AggregateRead) -> bool:
    """True when a read only uses COUNT(*) (no concrete aggregate metric)."""
    return not (read.subjects - {"*"})


def _unclassified_duplicates(rel_path: str, source: str) -> list[DuplicateRead]:
    """Duplicate reads in ``source`` not explained by the allowlist.

    Shared by the whole-tree backstop and its self-test so the self-test proves
    non-vacuity with the real detector/classifier, not a mock.
    """
    unclassified: list[DuplicateRead] = []
    for pairs in scan_source(source).values():
        for pair in pairs:
            if (
                _is_plain_count(pair.first)
                and _is_plain_count(pair.second)
                and (rel_path, pair.owner, pair.first.key) in _KNOWN_LEGITIMATE_COUNTS
            ):
                continue
            unclassified.append(pair)
    return unclassified


# --- Guards ---------------------------------------------------------------


def test_folded_functions_have_no_duplicate_aggregate_read():
    """No audited fold function may re-grow a second identical aggregate read."""
    for name, rel_path, functions in FOLD_FUNCTIONS:
        per_function = extract_reads(_source(rel_path))
        for func in functions:
            dups = find_duplicate_reads(per_function.get(func, []))
            assert not dups, (
                f"{name}: {rel_path}:{func} has duplicate aggregate reads: "
                + "; ".join(
                    f"{d.first.sql!r} vs {d.second.sql!r} (key={d.first.key})"
                    for d in dups
                )
            )


def test_fold_threading_still_replaces_the_second_read():
    """The folded value is still threaded, so no callee re-issues the read."""
    token_usage = _norm(_function_source(TOKEN_REL, "get_api_access_token_usage"))
    assert "usage_summary=summary" in token_usage, (
        "token detail must pass its one summary into the row converter"
    )
    token_get = _norm(_function_source(TOKEN_REL, "get_api_access_token"))
    assert "distinct_summary=usage_summary" in token_get, (
        "get_api_access_token must forward the summary to _row_to_access_token"
    )

    status = _norm(_function_source(STORAGE_REL, "get_status"))
    assert "self._status_snapshot(user_id)" in status
    assert "override=override" in status, (
        "get_status must reuse the snapshot's override row instead of letting "
        "resolve_limit re-read it"
    )
    storage_set = _norm(_function_source(STORAGE_REL, "set_override"))
    assert "include_override=include_override" in storage_set, (
        "admin storage endpoints must opt into the folded override"
    )


# --- Non-vacuity self-tests ----------------------------------------------


def test_detector_flags_synthetic_duplicate_built_from_real_fold_sql():
    """A duplicate assembled from the real fold SQL is flagged."""
    summary = _sql_from_function(
        TOKEN_REL, "get_api_access_token_usage", "count(*) as total_events"
    )
    on_demand = _sql_from_function(
        TOKEN_REL,
        "_row_to_access_token",
        "count(distinct ip_index) as distinct_ip_count",
    )
    dups = scan_source(_synthetic_function(summary, on_demand))
    assert "synthetic" in dups and len(dups["synthetic"]) == 1
    pair = dups["synthetic"][0]
    assert (
        pair.first.tables
        == pair.second.tables
        == frozenset({"auth_api_access_token_events"})
    )
    assert pair.first.where == pair.second.where == ("token_id = ?",)
    assert pair.first.group_by == pair.second.group_by == ()


def test_detector_flags_reintroduced_duplicate_in_real_function_source():
    """Mutating the real function to add the second read is flagged."""
    clean = _function_source(TOKEN_REL, "get_api_access_token_usage")
    assert not scan_source(clean)  # precondition: current source is clean

    summary = _sql_from_function(
        TOKEN_REL, "get_api_access_token_usage", "count(*) as total_events"
    )
    anchor = "token = self.get_api_access_token(token_id, usage_summary=summary)"
    assert anchor in clean
    mutated = clean.replace(
        anchor,
        f"duplicate = self._db.fetch_one({summary!r}, (token_id,))\n        {anchor}",
        1,
    )
    assert "duplicate = self._db.fetch_one" in mutated  # mutation applied
    dups = scan_source(mutated).get("get_api_access_token_usage", [])
    assert dups, "the guard failed to flag a re-introduced duplicate read"


def test_detector_does_not_flag_legitimately_different_aggregates():
    """Real grouped breakdowns and different metrics are not false positives."""
    ip_rows = _sql_from_function(
        TOKEN_REL, "get_api_access_token_usage", "group by ip_index"
    )
    path_rows = _sql_from_function(
        TOKEN_REL, "get_api_access_token_usage", "group by method, path"
    )
    # Same table and caller scope, but different WHERE predicates and GROUP BY.
    assert scan_source(_synthetic_function(ip_rows, path_rows)) == {}

    # Same table and WHERE, genuinely different aggregate metric.
    different_metric = _synthetic_function(
        "SELECT COUNT(*) FROM auth_api_access_token_events WHERE token_id = ?",
        "SELECT MAX(used_at) FROM auth_api_access_token_events WHERE token_id = ?",
    )
    assert scan_source(different_metric) == {}


def test_detector_allows_documented_different_grouping():
    """A batched GROUP BY read is not a duplicate of the single-scope read."""
    batched = (
        "SELECT token_id, COUNT(*) FROM auth_api_access_token_events "
        "WHERE token_id = ? GROUP BY token_id"
    )
    single = "SELECT COUNT(*) FROM auth_api_access_token_events WHERE token_id = ?"
    assert scan_source(_synthetic_function(batched, single)) == {}


# --- Whole-tree backstop guards -------------------------------------------


def test_no_unclassified_duplicate_aggregate_reads_in_source_tree():
    """A duplicate read anywhere in production source is flagged, not just in
    the two audited folds.

    The scoped checks above protect the original folds and their threading;
    this backstop covers every production module so a later fold added in a new
    function/file cannot silently reintroduce an extra aggregate round trip.
    The only exempt pairs are the explicitly classified count-before/after a
    mutation checks in _KNOWN_LEGITIMATE_COUNTS.
    """
    offenders = []
    for rel in _source_modules():
        for dup in _unclassified_duplicates(rel, _source(rel)):
            offenders.append(
                f"{rel}:{dup.owner}: {dup.first.sql!r} vs {dup.second.sql!r} "
                f"(key={dup.first.key})"
            )
    assert not offenders, (
        "unclassified duplicate aggregate reads in production source:\n"
        + "\n".join(offenders)
        + "\nIf a pair is a genuine count-before/after-mutation check, classify "
        "it in _KNOWN_LEGITIMATE_COUNTS; never silence a fresh fold."
    )


def test_whole_tree_backstop_flags_a_fresh_duplicate_site():
    """Self-test: the backstop is non-vacuous at a brand-new fold site.

    The detector/classifier above are shared with the backstop, so injecting a
    fresh duplicate-read function into a real production module's source proves
    the backstop flags a site that is in neither FOLD_FUNCTIONS nor the
    allowlist -- the exact case a fixed per-function list would silently miss.
    """
    fresh_sql = "SELECT COUNT(*) FROM auth_api_access_token_events WHERE token_id = ?"
    injected = (
        _source(TOKEN_REL)
        + "\ndef brand_new_fold_site(self, token_id):\n"
        + f"    a = self._db.fetch_one({fresh_sql!r}, (token_id,))\n"
        + f"    b = self._db.fetch_one({fresh_sql!r}, (token_id,))\n"
        + "    return a, b\n"
    )
    found = _unclassified_duplicates(TOKEN_REL, injected)
    assert any(dup.owner == "brand_new_fold_site" for dup in found), (
        "backstop failed to flag a newly-introduced duplicate read outside the "
        "scoped fold list and the allowlist"
    )
    # A genuinely different metric at a new site must NOT be flagged, so the
    # backstop is not vacuous by simply matching every repeated table read.
    different_metric = (
        _source(TOKEN_REL)
        + "\ndef new_but_fine(self, token_id):\n"
        + "    a = self._db.fetch_one("
        + repr("SELECT COUNT(*) FROM auth_api_access_token_events WHERE token_id = ?")
        + ", (token_id,))\n"
        + "    b = self._db.fetch_one("
        + repr(
            "SELECT MAX(used_at) FROM auth_api_access_token_events WHERE token_id = ?"
        )
        + ", (token_id,))\n"
        + "    return a, b\n"
    )
    assert not [
        dup
        for dup in _unclassified_duplicates(TOKEN_REL, different_metric)
        if dup.owner == "new_but_fine"
    ]


def test_classified_count_sites_are_real_and_rest():
    """Each allowlist entry is a genuine plain-COUNT duplicate (not a stale
    exemption hiding a live duplicate or excusing a concrete-metric one)."""
    for rel, owner, key in _KNOWN_LEGITIMATE_COUNTS:
        matches = [
            dup
            for dup in scan_source(_source(rel)).get(owner, [])
            if dup.first.key == key
        ]
        assert matches, f"stale classification: {rel}:{owner} no longer has {key}"
        assert all(
            _is_plain_count(dup.first) and _is_plain_count(dup.second)
            for dup in matches
        )
