"""Committed-tree guard: ``use_invite``'s atomic consumption UPDATE.

``MemberInviteMixin.use_invite`` performs the authoritative redemption decision
with one guarded ``UPDATE srv_invites`` statement.  The early validation reads
above it are a fast-fail courtesy only; the SQL predicate is what actually
stops a revoked, expired or fully-used invite from being consumed:

* ``AND revoked = 0`` -- revoke is re-checked atomically at write time;
* ``AND (expires_at IS NULL OR expires_at > ?)`` -- expiry is re-checked at
  write time against a *fresh* clock read, closing the "validated, then the
  DB write lands after expiry" window (N975 follow-up to ``cac562c8``);
* ``AND (max_uses = 0 OR uses < max_uses)`` -- the concurrent cap is enforced
  by the statement, not by the racy pre-check;
* ``rowcount == 0`` fail-closed refresh -- a lost CAS re-reads the row and
  raises the precise error instead of granting membership.

The runtime test ``test_invite_join_expiry_at_write_atomic.py`` uses a fake DB
and never inspects the literal SQL, so dropping any predicate from the real
UPDATE would not be caught there.  This guard reads the file straight out of
``HEAD`` with ``git show`` -- never the working tree -- and structurally
parses the ``use_invite`` method, so a stale staged copy cannot silently
revert the fix before any behavioural test runs.

Non-vacuity: each predicate, the write-time clock re-read, the rowcount guard
and the fail-closed refresh are dropped one at a time from the committed blob
and the same evaluator must reject every mutation.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET = "src/core/servers/handlers/members/invites.py"
_CLASS = "MemberInviteMixin"
_METHOD = "use_invite"

FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)

# Literal substrings that must survive in the normalized SQL of the guarded
# UPDATE.  ``expires_at IS NULL OR`` is folded in so the null-safe form cannot
# be swapped for a bare ``expires_at > ?`` that would reject never-expiring
# invites.
_SQL_PREDICATES = (
    "AND revoked = 0",
    "expires_at IS NULL OR expires_at > ?",
    "max_uses = 0 OR uses < max_uses",
)

_REFRESH_COLUMNS = ("revoked", "expires_at", "max_uses", "uses")
_FAIL_CLOSED_ERRORS = frozenset(
    {"InviteNotFoundError", "InviteExpiredError", "InviteMaxUsesError"}
)


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd or _REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    """Resolve HEAD once per run; every blob read is pinned to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _committed(commit: str, path: str, cwd: Path | None = None) -> str:
    proc = _git("show", f"{commit}:{path}", cwd=cwd)
    assert proc.returncode == 0, (
        f"cannot read committed {path!r} from {commit}: {proc.stderr.strip()}"
    )
    return proc.stdout


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _node_source(source: str, node: ast.AST) -> str:
    return ast.get_source_segment(source, node) or ""


def _first_str_arg(call: ast.Call) -> str | None:
    if not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _evaluate(source: str) -> tuple[bool, str]:
    """Return ``(ok, detail)`` for the committed invites module source."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"committed invites module does not parse: {exc}"

    cls = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == _CLASS
        ),
        None,
    )
    if cls is None:
        return False, f"{_CLASS} class missing from committed blob"

    method = next(
        (
            node
            for node in cls.body
            if isinstance(node, FUNC_TYPES) and node.name == _METHOD
        ),
        None,
    )
    if method is None:
        return False, f"{_CLASS}.{_METHOD} missing from committed blob"

    # Isolate the authoritative guarded UPDATE (not the pre-check SELECT or
    # the uses-decrement rollback execute).
    sql: str | None = None
    guarded: ast.Call | None = None
    for node in ast.walk(method):
        if not isinstance(node, ast.Call):
            continue
        if _dotted(node.func) != "self.db.execute":
            continue
        candidate = _first_str_arg(node)
        if (
            candidate
            and "UPDATE srv_invites" in candidate
            and "uses = uses + 1" in candidate
        ):
            sql, guarded = candidate, node
            break
    if guarded is None or sql is None:
        return False, (
            "guarded UPDATE (self.db.execute with 'uses = uses + 1') "
            "missing from use_invite"
        )

    normalized = " ".join(sql.split())
    for predicate in _SQL_PREDICATES:
        if predicate not in normalized:
            return False, f"guarded UPDATE SQL missing predicate: {predicate!r}"

    # The guard parameter must be the freshly read clock, captured after the
    # early validation read and immediately before the write.
    now_assignments: list[int] = []
    for node in ast.walk(method):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        if not _dotted(node.value.func).endswith("_get_timestamp"):
            continue
        if any(isinstance(t, ast.Name) and t.id == "now" for t in node.targets):
            now_assignments.append(node.lineno)
    now_assignments.sort()
    if len(now_assignments) < 2:
        return False, (
            "no write-time clock re-read: expected at least two "
            "`now = ..._get_timestamp()` reads in use_invite"
        )
    if not (now_assignments[0] < now_assignments[-1] < guarded.lineno):
        return False, (
            "write-time clock re-read is not positioned between the early "
            "validation read and the guarded UPDATE"
        )
    if len(guarded.args) < 2 or not any(
        isinstance(n, ast.Name) and n.id == "now" for n in ast.walk(guarded.args[1])
    ):
        return False, "guarded UPDATE parameters do not pass the freshly read `now`"

    # rowcount == 0 must trigger a fail-closed refresh, not a silent success.
    rowcount_ifs = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.If)
        and node.lineno > guarded.lineno
        and "rowcount" in _node_source(source, node.test)
        and "== 0" in _node_source(source, node.test)
    ]
    if not rowcount_ifs:
        return False, "no `rowcount == 0` fail-closed branch after the guarded UPDATE"

    rowcount_if = rowcount_ifs[0]
    refreshed = False
    raised: set[str] = set()
    for node in ast.walk(rowcount_if):
        if isinstance(node, ast.Call) and _dotted(node.func) == "self.db.fetch_one":
            refresh_sql = _first_str_arg(node) or ""
            if all(column in refresh_sql for column in _REFRESH_COLUMNS):
                refreshed = True
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(exc, ast.Name):
                raised.add(exc.id)
            elif isinstance(exc, ast.Attribute):
                raised.add(exc.attr)
    if not refreshed:
        return False, "rowcount == 0 branch does not refresh all consumption columns"
    missing_errors = _FAIL_CLOSED_ERRORS - raised
    if missing_errors:
        return False, (
            "rowcount == 0 branch does not fail closed with: "
            + ", ".join(sorted(missing_errors))
        )
    return True, "use_invite SQL predicates and fail-closed refresh pinned at HEAD"


def test_invite_use_sql_guards_pinned_at_head(commit_sha: str) -> None:
    """The real guarded UPDATE is present in the committed blob at HEAD."""
    ok, detail = _evaluate(_committed(commit_sha, _TARGET))
    assert ok, detail


# Each mutation is a syntactically valid revert of one part of the fix.  The
# same evaluator used for the live HEAD check must reject every one, so a
# detector that never fires cannot pass.  Each `old` is asserted present so a
# drifted fixture cannot silently turn a non-vacuity case into a no-op.
_REVERTS: tuple[tuple[str, str, str], ...] = (
    (
        "no-revoked-predicate",
        "               WHERE code = ?\n"
        "                 AND revoked = 0\n"
        "                 AND (expires_at IS NULL OR expires_at > ?)",
        "               WHERE code = ?\n"
        "                 AND 1 = 1\n"
        "                 AND (expires_at IS NULL OR expires_at > ?)",
    ),
    (
        "no-expiry-write-predicate",
        "                 AND (expires_at IS NULL OR expires_at > ?)\n",
        "",
    ),
    (
        "no-null-safe-expiry",
        "                 AND (expires_at IS NULL OR expires_at > ?)\n",
        "                 AND (expires_at > ?)\n",
    ),
    (
        "no-max-uses-predicate",
        "                 AND (max_uses = 0 OR uses < max_uses)",
        "                 AND 1 = 1",
    ),
    (
        "no-write-time-clock-reread",
        "        now = self.manager._get_timestamp()\n"
        "        update_cursor = self.db.execute(\n",
        "        update_cursor = self.db.execute(\n",
    ),
    (
        "no-rowcount-guard",
        '        if getattr(update_cursor, "rowcount", 0) == 0:\n',
        "        if False:\n",
    ),
    (
        "no-failclosed-refresh",
        '"SELECT revoked, expires_at, max_uses, uses FROM srv_invites WHERE code = ?"',
        '"SELECT 1 FROM srv_invites WHERE code = ?"',
    ),
)


@pytest.mark.parametrize("name,old,new", _REVERTS, ids=[r[0] for r in _REVERTS])
def test_each_guard_is_falsifiable(
    commit_sha: str, name: str, old: str, new: str
) -> None:
    """Dropping each predicate / safety branch must be caught by the evaluator."""
    source = _committed(commit_sha, _TARGET)
    assert old in source, f"revert fixture {name!r} no longer matches committed source"
    ok, detail = _evaluate(source.replace(old, new, 1))
    assert not ok, f"synthetic revert {name!r} was not caught: {detail}"


def test_gutted_blob_is_rejected() -> None:
    """A blob with no use_invite method at all cannot pass."""
    ok, detail = _evaluate("# gutted invites module\n")
    assert not ok
    assert _CLASS in detail or _METHOD in detail


def test_reader_returns_committed_blob_not_working_tree(tmp_path: Path) -> None:
    """The blob reader really uses `git show`, not a mutated working copy."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git("init", "-q", cwd=repo).returncode == 0
    target = repo / "module.py"
    target.write_text("VALUE = 'committed'\n", encoding="utf-8")
    assert _git("add", "module.py", cwd=repo).returncode == 0
    assert (
        _git(
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "init",
            cwd=repo,
        ).returncode
        == 0
    )
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    target.write_text("VALUE = 'working-tree-drift'\n", encoding="utf-8")
    assert _committed(sha, "module.py", cwd=repo) == "VALUE = 'committed'\n"
