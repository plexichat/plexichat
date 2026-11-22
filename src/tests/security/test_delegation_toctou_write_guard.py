"""Static guard: delegation role-grants must bind the write to the checked payload.

Background
----------
A family of fixes closed a check->write TOCTOU on *delegated* role grants: the
code reads a role's permission payload, decides the actor may delegate it, and
only then INSERTs/UPDATEs a role-assignment row.  Without binding that write to
the exact payload the decision read, a concurrent widening of the role in the
gap hands the target scopes the checker never authorized:

* N772  ``8ede88cb`` -- onboarding whitelist self-service grants
  (``assign_roles_from_whitelist``) now capture a permission snapshot and bind
  the INSERT with ``EXISTS (... pr.permissions = ?)`` + rowcount.
* N745  ``a94a3ad5`` -- admin-account create/update routes capture the payload
  via ``can_grant_existing_role(..., _out=...)`` and bind the assignment INSERT
  through ``_role_permission_write_guard`` (the CAS helper) + ``_write_missed``.
* N700/N642 -- ``admin/roles.py::assign_role`` binds the INSERT with
  ``EXISTS (... pr.permissions = ?)`` + rowcount; the server ``assign_role``
  re-reads the authoritative payload under the 142->143 advisory lock that
  ``update_role`` also takes (serialized compare-and-swap).

This module is a committed-tree structural guard, not a per-file marker list.
It enumerates, from HEAD blobs, *every* function in the production tree that
writes a role-assignment row (``srv_member_roles`` / ``admin_role_assignments``),
classifies which of those are delegated (a grantability / permission-payload
check immediately precedes the write), and asserts each delegated write is bound
to the authoritative payload via one of the accepted forms (CAS ``permissions
= ?`` / ``EXISTS (... permissions = ?)`` + fail-closed rowcount, or an
under-lock authoritative re-read), or is in a documented allowlist of
non-delegated bootstrap/self-test/default-role seeding with reasons.

Fails closed: a NEW delegated write with an unguarded INSERT after a snapshot
check is a violation, and a NEW assignment write that is neither delegated nor
documented in the allowlist is a violation too.  Non-vacuity is proven by
parametrized synthetic sources (unguarded delegation is flagged; CAS and
under-lock cases pass), by tying the live enumeration to the expected fix set,
and by a throwaway-repo self-test showing blobs come from HEAD, not the working
tree.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

_ASSIGNMENT_TABLES = ("srv_member_roles", "admin_role_assignments")

# An assignment write is an INSERT INTO / UPDATE of one of the two tables that
# actually carry role grants.  (\s* spans the SQL string concatenation the code
# uses, e.g. "INSERT INTO admin_role_assignments " on its own source line.)
_WRITE_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE)\s+(?:" + "|".join(_ASSIGNMENT_TABLES) + r")\b",
    re.IGNORECASE,
)

# Signals that a function makes a *delegation* decision about a role's payload
# before writing the grant.  ``permission_snapshots`` is the onboarding
# self-service path: the grantability decision is made in the step validator,
# which hands the exact payload to the writer via this argument.
_DELEGATION_MARKERS = (
    "can_grant_existing_role",
    "can_delegate_permissions",
    "check_permissions_grantable",
    "permission_snapshots",
    "validate_delegable_permissions",
)

_LOCK_MARKERS = ("pg_advisory_xact_lock", "pg_advisory_lock", "advisory_xact_lock")

# A CAS that does not fail closed on a missed write is not a binding guard: the
# caller must observe the rowcount and refuse the grant on mismatch.
_FAIL_CLOSED_MARKERS = ("rowcount", "_write_missed", "409")


@dataclass(frozen=True)
class WriteSite:
    """One function that writes a role-assignment row."""

    key: str
    path: str
    qualname: str
    delegated: bool
    bound: bool


# Delegated grant paths and the binding each must keep in the committed tree.
# Keys are "<repo-relative path>::<function qualname>".
DELEGATED_GUARDS: dict[str, str] = {
    "src/api/routes/admin/roles.py::assign_role": (
        "N700/N642 admin role grant: can_delegate_permissions over the hierarchy "
        "snapshot; INSERT bound with EXISTS (... pr.permissions = ?) + rowcount 409."
    ),
    "src/api/routes/admin/users/admin_users.py::AdminUsersMixin.create_admin_user": (
        "N745 admin-account create: can_grant_existing_role(_out=...) snapshot; "
        "INSERT bound via _role_permission_write_guard (EXISTS ... permissions = ?) "
        "+ _write_missed fail-closed."
    ),
    "src/api/routes/admin/users/admin_users.py::AdminUsersMixin.update_admin_user": (
        "N745 admin-account role retarget: same can_grant_existing_role snapshot + "
        "_role_permission_write_guard CAS + _write_missed fail-closed."
    ),
    "src/core/servers/handlers/roles/assignments.py::RoleAssignmentMixin.assign_role": (
        "N700/N642 server role grant: authoritative payload re-read under the "
        "142->143 advisory lock (serialized with update_role) and re-validated "
        "before the INSERT."
    ),
    "src/core/servers/handlers/roles/assignments.py::RoleAssignmentMixin.assign_roles_from_whitelist": (
        "N772 onboarding self-service grant: permission_snapshots captured from "
        "the grantability decision; INSERT bound with "
        "EXISTS (... pr.permissions = ?) + rowcount fail-closed."
    ),
}

# Non-delegated assignment writes that are intentionally unguarded, each with
# the reason it is safe to leave unbound (no payload-based delegation decision
# is being made at this site).
ALLOWED_NONDELEGATED_WRITES: dict[str, str] = {
    "src/core/admin/auth/auth_mixin.py::_seed_default_admin_roles": (
        "Bootstrap seed: ties the built-in admin to the freshly created system "
        "super_admin role; no caller-supplied role payload/grantability decision."
    ),
    "src/core/selftest/services/setup/admin.py::AdminSetupMixin.setup_admin_user_and_roles": (
        "Self-test fixture seeding of the super_admin role onto the test admin."
    ),
    "src/core/selftest/services/setup/admin.py::AdminSetupMixin.setup_other_admin_user": (
        "Self-test fixture seeding of a second admin's role."
    ),
    "src/core/selftest/services/setup/admin.py::AdminSetupMixin.promote_third_to_support_admin": (
        "Self-test fixture seeding of the third admin's support role for quorum."
    ),
    "src/core/servers/handlers/members/crud.py::MemberCrudMixin.add_member": (
        "Server default @everyone role auto-assigned on join; the role id comes "
        "from the is_default flag, not a permission-payload grantability check."
    ),
    "src/core/servers/manager/server_ops.py::ServerOpsMixin.create_server": (
        "Bootstrap: the owner receives the @everyone role created moments earlier "
        "in the same function; no delegation decision."
    ),
}


class BlobMissing(Exception):
    """Raised when a path is absent from the commit under inspection."""


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
    """Resolve HEAD once per run; every blob read is pinned to this commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _make_blob_reader(commit: str, repo_root: Path = _REPO_ROOT):
    """Return a reader that fetches ``<commit>:<path>`` with ``git show``."""

    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}", cwd=repo_root)
        if proc.returncode != 0:
            raise BlobMissing(f"{path!r} not present in commit: {proc.stderr.strip()}")
        return proc.stdout

    return read


def _candidate_paths(commit: str) -> list[str]:
    """Enumerate production files at HEAD that write a role-assignment row.

    Discovery is structural (git grep at HEAD), so a brand-new file containing
    an unguarded delegation write is picked up automatically instead of being
    silently skipped by a hardcoded file list.
    """
    pattern = (
        r"INSERT[[:space:]]+INTO[[:space:]]+(srv_member_roles|admin_role_assignments)"
        r"|UPDATE[[:space:]]+(srv_member_roles|admin_role_assignments)"
    )
    proc = _git("grep", "-l", "-E", pattern, commit, "--", "*.py")
    assert proc.returncode == 0, f"git grep failed: {proc.stderr.strip()}"
    paths = []
    for line in proc.stdout.splitlines():
        path = line.split(":", 1)[1] if line.startswith(f"{commit}:") else line
        if path.startswith("src/tests/"):
            continue  # test fixtures are not production delegation paths
        paths.append(path)
    return sorted(paths)


def _function_segments(source: str):
    """Yield ``(qualname, source_segment)`` for every function in ``source``."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # a file we cannot parse is itself a finding
        raise AssertionError(f"cannot parse source for structural analysis: {exc}")

    def walk(node, prefix: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                segment = ast.get_source_segment(source, child) or ""
                yield qualname, segment
                # Also descend into nested functions/methods.
                yield from walk(child, f"{qualname}.")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.")
            else:
                yield from walk(child, prefix)

    yield from walk(tree, "")


def _is_bound(segment: str) -> bool:
    """True when the write is tied to the authoritative payload.

    Accepted bindings:
      * an inline CAS where-guard ``permissions = ?`` that FAILS CLOSED on a
        missed rowcount (admin/roles.py::assign_role, whitelist path);
      * the shared CAS helper ``_role_permission_write_guard`` (verified in its
        own test to embed ``EXISTS (... permissions = ?)``) combined with a
        fail-closed ``_write_missed`` check (admin_users.py paths);
      * an under-lock authoritative re-read: the advisory lock the mutating
        role route also takes, plus a fresh ``permissions`` read inside it.
        Here the lock itself provides the compare-and-swap serialization, so a
        rowcount on the INSERT is not required.
    """
    if re.search(r"permissions\s*=\s*\?", segment, re.IGNORECASE) and any(
        marker in segment for marker in _FAIL_CLOSED_MARKERS
    ):
        return True
    if "_role_permission_write_guard" in segment and "_write_missed" in segment:
        return True
    if any(marker in segment for marker in _LOCK_MARKERS) and (
        "fresh_permissions" in segment or "SELECT permissions FROM srv_roles" in segment
    ):
        return True
    return False


def _analyze(sources: dict[str, str], allowlist: dict[str, str]):
    """Return ``(sites, violations)`` for a mapping of path -> source text.

    Pure and injectable: self-tests feed synthetic sources here so the guard's
    own discrimination can be proven without mutating the repository.
    """
    sites: dict[str, WriteSite] = {}
    violations: list[str] = []
    for path, source in sources.items():
        for qualname, segment in _function_segments(source):
            if not _WRITE_RE.search(segment):
                continue
            key = f"{path}::{qualname}"
            delegated = any(marker in segment for marker in _DELEGATION_MARKERS)
            bound = _is_bound(segment)
            sites[key] = WriteSite(key, path, qualname, delegated, bound)
            if delegated and not bound:
                violations.append(
                    f"UNGUARDED DELEGATED ROLE GRANT: {key} checks a role "
                    "permission payload/grantability and then writes a role "
                    "assignment without binding the write to that payload "
                    "(need permissions = ? / EXISTS (... permissions = ?) with "
                    "fail-closed rowcount, or an under-lock re-read)."
                )
            elif not delegated and key not in allowlist:
                violations.append(
                    f"UNDOCUMENTED ROLE-ASSIGNMENT WRITE: {key} writes a role "
                    "assignment but is neither a recognized delegated+guarded "
                    "grant nor an allowlisted bootstrap/self-test/default seed. "
                    "Add a binding guard or document it with a reason."
                )
    return sites, violations


def _read_head_sources(commit: str) -> dict[str, str]:
    reader = _make_blob_reader(commit)
    sources: dict[str, str] = {}
    for path in _candidate_paths(commit):
        sources[path] = reader(path)
    return sources


# ---------------------------------------------------------------------------
# Live committed-tree assertions
# ---------------------------------------------------------------------------


def test_head_enumeration_covers_every_delegated_and_non_delegated_write(
    commit_sha: str,
) -> None:
    """The structural sweep really finds all expected grant/seed sites."""
    sites, _ = _analyze(_read_head_sources(commit_sha), ALLOWED_NONDELEGATED_WRITES)
    delegated_keys = {k for k, s in sites.items() if s.delegated}
    nondelegated_keys = {k for k, s in sites.items() if not s.delegated}

    assert delegated_keys == set(DELEGATED_GUARDS), (
        "delegated grant enumeration drifted from the documented fix set; "
        f"missing={sorted(set(DELEGATED_GUARDS) - delegated_keys)}, "
        f"unexpected={sorted(delegated_keys - set(DELEGATED_GUARDS))}"
    )
    assert nondelegated_keys == set(ALLOWED_NONDELEGATED_WRITES), (
        "non-delegated assignment-write enumeration drifted from the allowlist; "
        f"missing={sorted(set(ALLOWED_NONDELEGATED_WRITES) - nondelegated_keys)}, "
        f"undocumented={sorted(nondelegated_keys - set(ALLOWED_NONDELEGATED_WRITES))}"
    )


def test_every_delegated_grant_is_bound_in_committed_tree(commit_sha: str) -> None:
    """No delegation write may be an unguarded INSERT after a snapshot check."""
    sources = _read_head_sources(commit_sha)
    sites, violations = _analyze(sources, ALLOWED_NONDELEGATED_WRITES)
    assert not violations, "committed-tree TOCTOU guard violations:\n" + "\n".join(
        violations
    )
    # Each documented delegated fix must be present AND bound, not merely avoid
    # a violation by disappearing from the enumeration.
    for key, reason in DELEGATED_GUARDS.items():
        assert key in sites, f"documented delegated grant missing from HEAD: {key}"
        assert sites[key].delegated, f"{key} no longer recognized as delegated"
        assert sites[key].bound, f"{key} lost its payload binding: {reason}"


def test_allowlist_entries_are_documented_and_still_present(commit_sha: str) -> None:
    """Every allowlisted seed has a non-empty reason and still exists at HEAD."""
    sites, _ = _analyze(_read_head_sources(commit_sha), ALLOWED_NONDELEGATED_WRITES)
    for key, reason in ALLOWED_NONDELEGATED_WRITES.items():
        assert reason.strip(), f"empty allowlist reason for {key}"
        assert key in sites, f"allowlisted seed missing from HEAD: {key}"
        assert not sites[key].delegated, (
            f"allowlisted seed {key} now performs a delegated grant; it must be "
            "guarded and moved out of the allowlist"
        )


def test_admin_users_cas_helper_embeds_the_payload_guard(commit_sha: str) -> None:
    """The helper treated as a binding really carries the CAS SQL (non-vacuity)."""
    src = _make_blob_reader(commit_sha)("src/api/routes/admin/users/admin_users.py")
    match = re.search(
        r"def _role_permission_write_guard\b.*?(?=\ndef |\Z)", src, re.DOTALL
    )
    assert match is not None, "admin_users CAS helper disappeared from HEAD"
    helper = match.group(0)
    assert "EXISTS (SELECT 1 FROM admin_roles pr" in helper
    assert re.search(r"pr\.permissions\s*=\s*\?", helper), (
        "admin_users CAS helper no longer binds the write to the snapshot payload"
    )


# ---------------------------------------------------------------------------
# Self-tests: the guard discriminates guarded from unguarded writes
# ---------------------------------------------------------------------------


_UNGUARDED = """
def grant(request):
    if not can_grant_existing_role(db, actor, role_id):
        raise HTTPException(status_code=403)
    db.execute(
        "INSERT INTO admin_role_assignments (admin_id, role_id) VALUES (?, ?)",
        (target, role_id),
    )
"""

_CAS = """
def grant(request):
    if not can_grant_existing_role(db, actor, role_id, _out=_snap):
        raise HTTPException(status_code=403)
    cursor = db.execute(
        "INSERT INTO admin_role_assignments (admin_id, role_id) "
        "SELECT ?, ? WHERE 1=1 AND EXISTS (SELECT 1 FROM admin_roles pr "
        "WHERE pr.id = ? AND pr.permissions = ?)",
        (target, role_id, role_id, _snap["permissions"]),
    )
    if getattr(cursor, "rowcount", 0) <= 0:
        raise HTTPException(status_code=409)
"""

_LOCKED = """
def grant(request):
    check_permissions_grantable(actor_perms, role_permissions)
    db.execute("SELECT pg_advisory_xact_lock(?, ?)", (142, server_id))
    fresh_row = db.fetch_one("SELECT permissions FROM srv_roles WHERE id = ?", (role_id,))
    fresh_permissions = json.loads(fresh_row["permissions"])
    check_permissions_grantable(actor_perms, fresh_permissions)
    db.execute(
        "INSERT INTO srv_member_roles (id, member_id, role_id) VALUES (?, ?, ?)",
        (mid, member_id, role_id),
    )
"""

_HELPER_CAS = """
def grant(request):
    if not can_grant_existing_role(db, actor, role_id, _out=_snap):
        raise HTTPException(status_code=403)
    guard_sql, guard_params = _role_permission_write_guard(role_id, _snap["permissions"])
    cursor = db.execute(
        "INSERT INTO admin_role_assignments (admin_id, role_id) "
        "SELECT ?, ? WHERE 1=1" + guard_sql,
        (target, role_id, *guard_params),
    )
    if _write_missed(cursor):
        raise HTTPException(status_code=409)
"""

_UNDELEGATED_SEED = """
def seed(db, admin_id):
    db.execute(
        "INSERT INTO admin_role_assignments (admin_id, role_id) VALUES (?, ?)",
        (admin_id, super_admin_id),
    )
"""


def test_synthetic_unguarded_delegation_is_flagged() -> None:
    """A new snapshot-check-then-unguarded-INSERT delegation fails closed."""
    sites, violations = _analyze({"src/core/new_grants.py": _UNGUARDED}, {})
    assert sites["src/core/new_grants.py::grant"].delegated
    assert not sites["src/core/new_grants.py::grant"].bound
    assert any("UNGUARDED DELEGATED ROLE GRANT" in v for v in violations), violations


def test_synthetic_cas_and_locked_delegations_pass() -> None:
    """Inline CAS, helper CAS, and under-lock re-read are all accepted."""
    sources = {
        "src/core/cas.py": _CAS,
        "src/core/locked.py": _LOCKED,
        "src/core/helper.py": _HELPER_CAS,
    }
    sites, violations = _analyze(sources, {})
    assert not violations, violations
    assert all(s.delegated and s.bound for s in sites.values())


def test_synthetic_undocumented_write_is_flagged() -> None:
    """A brand-new non-delegated assignment write must be documented."""
    _, violations = _analyze({"src/core/new_seed.py": _UNDELEGATED_SEED}, {})
    assert any("UNDOCUMENTED ROLE-ASSIGNMENT WRITE" in v for v in violations)


def test_blob_reader_returns_committed_blob_not_working_tree(tmp_path: Path) -> None:
    """Blob reads come from the commit, so a dirty working tree cannot mask a revert."""

    def run(*a):
        return _git(*a, cwd=tmp_path)

    assert run("init", "-q").returncode == 0
    assert run("config", "user.email", "guard@example.com").returncode == 0
    assert run("config", "user.name", "guard").returncode == 0
    target = tmp_path / "mod.py"
    target.write_text("MARKER = 'committed'\n", encoding="utf-8")
    assert run("add", "mod.py").returncode == 0
    assert run("commit", "-q", "-m", "seed").returncode == 0
    head = run("rev-parse", "HEAD").stdout.strip()
    # Diverge the working tree from the committed blob.
    target.write_text("MARKER = 'working-tree'\n", encoding="utf-8")
    reader = _make_blob_reader(head, tmp_path)
    assert "MARKER = 'committed'" in reader("mod.py")
    assert "working-tree" not in reader("mod.py")
