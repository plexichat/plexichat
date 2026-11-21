"""Static guard: folded read queries must stay owner-scoped and fail closed.

This campaign folded several second round trips into the query that already
ran on the hot path:

* session verification now resolves the trusted-device flag in the same SELECT,
* the access-token detail endpoint reuses one usage-summary row,
* private-thread listings batch the live server-membership probe,
* the storage-quota status snapshot carries the override audit columns, and
* invite creation reads the ``invites`` config once per mint.

Each fold replaced a helper that was individually audited for owner-scoping.
A future refactor that drops a scope predicate (``user_id = ?`` / ``id = ?`` /
``inviter_id = ?`` ...) while keeping the fold would silently widen the query
to other owners, and a fold that starts treating a missing row as a grant
would fail open. Runtime tests only exercise the queries a fixture happens to
run; this guard reads the *actual* SQL text out of each source file with AST
and asserts, for every fold:

1. the owner/subject-scoping predicate(s) are still present in the folded
   query, and the caller identity is still bound into the read (``= ?``);
2. the caller fails closed when the scoped row is missing (no row / no
   membership / no override / no insert -> denial, never a grant).

A parametrized self-test removes each scope predicate from the extracted query
text (and each fail-closed marker from its function source) and proves this
guard flags the removal, so it cannot pass vacuously. It never edits files on
disk -- all mutations are in-memory strings.
"""

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class QuerySpec:
    """One folded SQL string and the predicates that must survive in it."""

    fingerprint: str
    required: tuple[str, ...]
    # Function whose body contains the query. None means a module-level
    # constant (search all string constants in the file).
    scope_func: str | None
    # Function whose source must bind the caller identity into this read, and
    # a literal slice proving it (the ``= ?`` placeholders carry no meaning if
    # the bound tuple stops naming the caller).
    bind_func: str
    bind_marker: str


@dataclass(frozen=True)
class FoldSpec:
    name: str
    rel_path: str
    queries: tuple[QuerySpec, ...]
    # (function_name, substring) pairs that must remain in the function source
    # so a missing scoped row denies instead of granting.
    fail_closed: tuple[tuple[str, str], ...]


FOLDS: tuple[FoldSpec, ...] = (
    FoldSpec(
        name="session trusted-device fold",
        rel_path="core/auth/managers/session/verification.py",
        queries=(
            QuerySpec(
                fingerprint="SELECT d.trusted FROM auth_devices d",
                required=(
                    # Device row bound to the session's own device *and owner*.
                    "d.id = s.device_id",
                    "d.user_id = s.user_id",
                    # Session/user row bound to the caller's identity.
                    "JOIN auth_users u ON s.user_id = u.id",
                    "WHERE s.id = ?",
                ),
                scope_func=None,
                bind_func="_verify_session_token",
                bind_marker='_SESSION_VERIFY_SQL, (parsed["id"],)',
            ),
        ),
        fail_closed=(
            # Missing/revoked session never authenticates.
            ("_verify_session_token", "not row"),
            # Missing device row -> NULL device_trusted -> no trust grant.
            ("_verify_session_token", 'row.get("device_trusted")'),
        ),
    ),
    FoldSpec(
        name="token-usage fold",
        rel_path="core/auth/managers/api_token.py",
        queries=(
            QuerySpec(
                fingerprint="FROM auth_api_access_tokens",
                required=("WHERE id = ?",),
                scope_func="get_api_access_token",
                bind_func="get_api_access_token",
                bind_marker="(token_id,)",
            ),
            QuerySpec(
                fingerprint="COUNT(DISTINCT ip_index)",
                required=("WHERE token_id = ?",),
                scope_func="get_api_access_token_usage",
                bind_func="get_api_access_token_usage",
                bind_marker="(token_id,)",
            ),
        ),
        fail_closed=(
            ("get_api_access_token", "if not row"),
            ("get_api_access_token_usage", "or {}"),
            ("get_api_access_token_usage", "if not token"),
        ),
    ),
    FoldSpec(
        name="threads membership batch",
        rel_path="core/threads/manager/permissions.py",
        queries=(
            QuerySpec(
                fingerprint="SELECT server_id FROM srv_members WHERE user_id = ?",
                required=("WHERE user_id = ?",),
                scope_func="_live_server_member_ids",
                bind_func="_live_server_member_ids",
                bind_marker="(user_id,)",
            ),
        ),
        fail_closed=(
            # Lookup fault yields an empty allow-set, not a full grant.
            ("_live_server_member_ids", "return set()"),
            # Only servers actually requested by the caller can be granted.
            ("_live_server_member_ids", "& wanted"),
        ),
    ),
    FoldSpec(
        name="storage override fold",
        rel_path="core/storage_quota/manager.py",
        queries=(
            QuerySpec(
                fingerprint="q.note AS q_note",
                required=(
                    "uploaded_by = ?",
                    "user_id = ? AND expires_at > ?",
                    "LEFT JOIN user_storage_quotas q ON q.user_id = ?",
                ),
                scope_func="_status_snapshot",
                bind_func="_status_snapshot",
                bind_marker="(uid, uid, uid, now, uid)",
            ),
        ),
        fail_closed=(
            # A LEFT JOIN miss yields NULLs -> no override, never a shared one.
            ("_status_snapshot", "q_limit is not None or q_unlimited is not None"),
        ),
    ),
    FoldSpec(
        name="invite config read-once",
        rel_path="core/servers/handlers/members/invites.py",
        queries=(
            QuerySpec(
                fingerprint="INSERT INTO srv_invites",
                required=(
                    # The authoritative cap guards stay bound to the inviter
                    # and the server; a dropped predicate would cap a different
                    # owner (or none) while the fold still reads config once.
                    "WHERE inviter_id = ?",
                    "WHERE server_id = ?",
                ),
                scope_func=None,
                bind_func="create_invite",
                bind_marker="invite_id, code, channel.server_id, channel_id, user_id,",
            ),
            QuerySpec(
                fingerprint="SELECT COUNT(*) AS c FROM srv_invites WHERE inviter_id",
                required=("WHERE inviter_id = ?",),
                scope_func=None,
                bind_func="_enforce_invite_quota",
                bind_marker="(user_id, now_ms)",
            ),
        ),
        fail_closed=(
            ("create_invite", "if not inserted"),
            # A zero-row insert always raises; no phantom invite is returned.
            ("_raise_active_invite_limit", "raise InviteLimitReachedError"),
        ),
    ),
)


pytestmark = [pytest.mark.unit, pytest.mark.security]


def _norm(text: str) -> str:
    """Whitespace-insensitive form so multi-line SQL can be substring-checked."""
    return " ".join(text.split())


@lru_cache(maxsize=None)
def _source(rel_path: str) -> str:
    return (SRC / rel_path).read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _tree(rel_path: str) -> ast.Module:
    return ast.parse(_source(rel_path))


def _func_source(rel_path: str, name: str) -> str:
    for node in ast.walk(_tree(rel_path)):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            segment = ast.get_source_segment(_source(rel_path), node)
            if segment is not None:
                return segment
    raise AssertionError(f"{rel_path}: function {name!r} not found")


def _find_query(rel_path: str, spec: QuerySpec) -> str:
    """Return the folded SQL string identified by ``spec``.

    Scope the search to the function that issues the query when one is named;
    otherwise scan module-level string constants. A renamed/removed query is
    a hard failure -- the guard must never pass vacuously because it found
    nothing to inspect.
    """
    tree = _tree(rel_path)
    if spec.scope_func is not None:
        holder = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == spec.scope_func
        )
    else:
        holder = tree
    matches = [
        node.value
        for node in ast.walk(holder)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and spec.fingerprint in node.value
    ]
    assert len(matches) == 1, (
        f"{rel_path}: expected exactly one SQL string containing "
        f"{spec.fingerprint!r}, found {len(matches)}. The folded query was "
        "removed/renamed or duplicated; re-audit its scope predicates."
    )
    return _norm(matches[0])


def _missing(text: str, required: tuple[str, ...]) -> list[str]:
    normalized = _norm(text)
    return [pred for pred in required if _norm(pred) not in normalized]


def _marker_missing(source: str, marker: str) -> bool:
    """The fail-closed guard predicate: is ``marker`` absent from ``source``?"""
    return _norm(marker) not in _norm(source)


def test_folded_queries_keep_owner_scope_predicates():
    """Every folded query still scopes to the caller's owner/subject."""
    for fold in FOLDS:
        for query in fold.queries:
            sql = _find_query(fold.rel_path, query)
            missing = _missing(sql, query.required)
            assert not missing, (
                f"{fold.name}: query {query.fingerprint!r} lost scope "
                f"predicate(s) {missing}. Restoring the owner/subject bound "
                "predicate prevents the fold from widening to other rows."
            )


def test_folded_queries_bind_caller_identity():
    """The ``= ?`` placeholders are still bound to the caller's identity."""
    for fold in FOLDS:
        for query in fold.queries:
            source = _norm(_func_source(fold.rel_path, query.bind_func))
            assert _norm(query.bind_marker) in source, (
                f"{fold.name}: {query.bind_func} no longer binds the caller "
                f"identity ({query.bind_marker!r}) into the folded read."
            )


def test_folded_queries_fail_closed_on_missing_rows():
    """A missing scoped row denies instead of granting access."""
    for fold in FOLDS:
        for func, marker in fold.fail_closed:
            source = _func_source(fold.rel_path, func)
            assert not _marker_missing(source, marker), (
                f"{fold.name}: {func} lost fail-closed marker {marker!r}; a "
                "missing row/membership/override must never become a grant."
            )


def test_invite_config_is_read_once_per_mint():
    """The read-once fold still threads one config snapshot through creation."""
    source = _norm(_func_source(FOLDS[-1].rel_path, "create_invite"))
    assert source.count("_invite_config()") == 1, (
        "create_invite must load the invites config exactly once; a second "
        "read can let the advisory caps and the INSERT guards drift apart."
    )
    assert "_enforce_invite_quota(user_id, channel.server_id, cfg=cfg)" in source, (
        "The single config snapshot must be reused by the advisory quota "
        "check, not silently re-read inside the helper."
    )


def _predicate_cases():
    cases = []
    for fold in FOLDS:
        for qi, query in enumerate(fold.queries):
            for pi in range(len(query.required)):
                cases.append((fold.name, qi, pi))
    return cases


@pytest.mark.parametrize("fold_name,qi,pi", _predicate_cases())
def test_guard_flags_removed_scope_predicate(fold_name, qi, pi):
    """Non-vacuity: removing any scope predicate must be flagged by the guard.

    The mutation is purely in-memory: replace the predicate with a tautology
    (``1 = 1``), which is exactly the widening a careless refactor introduces.
    """
    fold = next(f for f in FOLDS if f.name == fold_name)
    query = fold.queries[qi]
    predicate = query.required[pi]

    sql = _find_query(fold.rel_path, query)
    assert not _missing(sql, (predicate,))  # precondition: currently present

    widened = _norm(sql).replace(_norm(predicate), " 1 = 1 ")
    assert _norm(predicate) not in widened  # removal actually happened
    assert _missing(widened, (predicate,)) == [predicate]  # guard flags it


def _fail_closed_cases():
    cases = []
    for fold in FOLDS:
        for mi in range(len(fold.fail_closed)):
            cases.append((fold.name, mi))
    return cases


@pytest.mark.parametrize("fold_name,mi", _fail_closed_cases())
def test_guard_flags_removed_fail_closed_marker(fold_name, mi):
    """Non-vacuity: removing a fail-closed branch must be flagged."""
    fold = next(f for f in FOLDS if f.name == fold_name)
    func, marker = fold.fail_closed[mi]

    source = _func_source(fold.rel_path, func)
    assert not _marker_missing(source, marker)  # precondition: present

    weakened = _norm(source).replace(_norm(marker), " pass ")
    assert _marker_missing(weakened, marker)  # the guard flags the removal
