"""Committed-tree guards for the two DSAR/data-export ownership bindings.

The N869 audit found three export-path invariants that must survive a peer
staging a stale whole-file copy:

* (a) DSAR request lookups bind the row to the caller:
  ``DSARManager.get_request_status`` and ``DSARManager.get_export_file`` both
  run ``SELECT * FROM dsar_requests WHERE id = ? AND user_id = ?`` with the
  ``(request_id, user_id)`` parameters. A guessed id must never open another
  subject's export.
* (b) the one-time download ticket is claimed before ``sign_url`` mints a
  signed grant (N729 ``a36998e4``). This is *already* pinned by the committed-
  tree guard ``test_one_time_redemption_claim_before_side_effect_guard.py`` and
  is deliberately not duplicated here.
* (c) a signed media URL carrying a ``uid`` is only honoured for that user:
  ``UrlSigner.verify_url`` rejects the URL when ``current_user_id`` is absent
  or differs from the signed ``uid``.

The existing registry guard (``test_security_fix_marker_committed_tree_guard.py``)
pins the d3767f9c admin-notes redaction but has no entry for (a) or (c); the
behavioural tests (``test_export_file_serving_owner_bound.py``,
``test_signing.py``) only exercise the working tree. This module closes that
gap: both checks read the blob straight out of ``HEAD`` with ``git show``,
never the working tree, and HEAD is resolved once per run.

Non-vacuity is proven two ways for each invariant: a syntactically valid
synthetic revert of every pin is rejected by the exact same evaluator used on
the live committed blob, and a fully gutted blob is rejected too.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DSAR_TARGET = "src/core/dsar/manager.py"
_SIGNING_TARGET = "src/core/media/security/signing.py"

FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)

# The owner-bound SQL and its positional parameters, verbatim from the two
# subject-facing DSAR lookups at HEAD.
_OWNER_SQL = "SELECT * FROM dsar_requests WHERE id = ? AND user_id = ?"
_OWNER_PARAMS = "(request_id, user_id)"

# The uid-binding check in UrlSigner.verify_url, verbatim from HEAD.
_UID_PARSE = 'user_id_str = params.get("uid", [None])[0]'
_UID_INT = "user_id = int(user_id_str) if user_id_str else None"
_UID_GUARD_COMMENT = "# SECURITY: Verify that the current user matches the signed user_id"
_UID_GUARD_OUTER = "if user_id is not None:"
_UID_GUARD_INNER = "if current_user_id is None or int(current_user_id) != user_id:"
_UID_GUARD_RAISE = 'raise SignatureInvalidError("User not authorized to access this URL")'


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=_REPO_ROOT,
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


def _committed(commit: str, path: str) -> str:
    proc = _git("show", f"{commit}:{path}")
    assert proc.returncode == 0, (
        f"cannot read committed {path!r} from {commit}: {proc.stderr.strip()}"
    )
    return proc.stdout


def _find_class(tree: ast.AST, name: str) -> ast.ClassDef | None:
    return next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name),
        None,
    )


def _method(cls: ast.ClassDef | None, name: str) -> ast.AST | None:
    if cls is None:
        return None
    return next(
        (n for n in cls.body if isinstance(n, FUNC_TYPES) and n.name == name),
        None,
    )


def _source_of(source: str, node: ast.AST | None, label: str) -> str:
    assert node is not None, f"{label} missing from committed blob"
    segment = ast.get_source_segment(source, node)
    assert segment, f"cannot recover source of {label}"
    return segment


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
    return ""


def _literal_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_text(node.left), _literal_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


# ---------------------------------------------------------------------------
# (a) DSAR ownership binding
# ---------------------------------------------------------------------------

_DSAR_OWNER_METHODS = (
    ("get_request_status", "status lookup"),
    ("get_export_file", "storage/download lookup"),
)


def _owner_bound_fetch_one(fn: ast.AST) -> bool:
    """True if a fetch_one in ``fn`` binds the row to (request_id, user_id)."""
    for node in ast.walk(fn):
        if _call_name(node) != "fetch_one":
            continue
        call = node
        if len(call.args) < 2:
            continue
        sql = _literal_text(call.args[0])
        if sql is None:
            continue
        if not (
            "dsar_requests" in sql and "id = ?" in sql and "user_id = ?" in sql
        ):
            continue
        params = call.args[1]
        if not isinstance(params, (ast.Tuple, ast.List)):
            continue
        names = {e.id for e in params.elts if isinstance(e, ast.Name)}
        if {"request_id", "user_id"} <= names:
            return True
    return False


def _evaluate_dsar(source: str) -> tuple[bool, str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"committed DSAR manager does not parse: {exc}"

    cls = _find_class(tree, "DSARManager")
    if cls is None:
        return False, "DSARManager class missing from committed blob"

    for name, label in _DSAR_OWNER_METHODS:
        fn = _method(cls, name)
        if fn is None:
            return False, f"DSARManager.{name} missing from committed blob"
        segment = _source_of(source, fn, f"DSARManager.{name}")
        if _OWNER_SQL not in segment or _OWNER_PARAMS not in segment:
            return False, (
                f"{label} lost the owner-bound SELECT: DSARManager.{name} must "
                f"keep {_OWNER_SQL!r} with {_OWNER_PARAMS!r}"
            )
        if not _owner_bound_fetch_one(fn):
            return False, (
                f"{label} DSARManager.{name} has no fetch_one binding the row to "
                "both request_id and user_id"
            )
    return True, "both subject-facing DSAR lookups are bound by id and user_id"


def test_dsar_owner_binding_pinned_at_head(commit_sha: str) -> None:
    ok, detail = _evaluate_dsar(_committed(commit_sha, _DSAR_TARGET))
    assert ok, detail


# Each revert mutates ONE of the two owner-bound lookups (occurrence 0 =
# get_request_status, 1 = get_export_file), so both pins are independently
# falsifiable. The _OWNER_SQL and _OWNER_PARAMS literals appear exactly twice
# in the committed file (once per lookup).
_DSAR_REVERTS: tuple[tuple[str, int, str, str], ...] = (
    (
        "status-lookup-unbound",
        0,
        _OWNER_SQL,
        "SELECT * FROM dsar_requests WHERE id = ?",
    ),
    (
        "status-lookup-drops-user-param",
        0,
        _OWNER_PARAMS,
        "(request_id,)",
    ),
    (
        "storage-lookup-unbound",
        1,
        _OWNER_SQL,
        "SELECT * FROM dsar_requests WHERE id = ? AND status = 'ready'",
    ),
    (
        "storage-lookup-drops-user-param",
        1,
        _OWNER_PARAMS,
        "(request_id,)",
    ),
)


def _replace_nth(source: str, old: str, new: str, n: int) -> str:
    # _OWNER_PARAMS also appears on an unrelated admin path at line ~338, so a
    # whole-file count is not a reliable locator. Replace only inside the two
    # subject-facing methods by splitting on their definitions.
    assert source.count("def get_request_status(") == 1
    assert source.count("def get_export_file(") == 1
    marker = "def get_export_file("
    head, tail = source.split(marker, 1)
    if n == 0:
        assert old in head, "owner pin missing from get_request_status"
        head = head.replace(old, new, 1)
    else:
        assert old in tail, "owner pin missing from get_export_file"
        tail = tail.replace(old, new, 1)
    return head + marker + tail


@pytest.mark.parametrize(
    "name,occurrence,old,new", _DSAR_REVERTS, ids=[r[0] for r in _DSAR_REVERTS]
)
def test_each_dsar_pin_is_falsifiable(
    commit_sha: str, name: str, occurrence: int, old: str, new: str
) -> None:
    source = _committed(commit_sha, _DSAR_TARGET)
    assert old in source, f"revert fixture {name!r} no longer matches committed source"
    ok, detail = _evaluate_dsar(_replace_nth(source, old, new, occurrence))
    assert not ok, f"synthetic DSAR revert {name!r} was not caught: {detail}"


# ---------------------------------------------------------------------------
# (c) Signed-URL uid binding
# ---------------------------------------------------------------------------

_SIGNING_PINS: tuple[tuple[str, str], ...] = (
    ("uid query param parsed", _UID_PARSE),
    ("uid coerced to int", _UID_INT),
    ("uid-guard comment", _UID_GUARD_COMMENT),
    ("uid-present branch", _UID_GUARD_OUTER),
    ("user-id mismatch fail-closed", _UID_GUARD_INNER),
    ("unauthorized uid rejection", _UID_GUARD_RAISE),
)


def _evaluate_signing(source: str) -> tuple[bool, str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"committed signing module does not parse: {exc}"

    cls = _find_class(tree, "UrlSigner")
    if cls is None:
        return False, "UrlSigner class missing from committed blob"
    fn = _method(cls, "verify_url")
    segment = _source_of(source, fn, "UrlSigner.verify_url")

    for label, pin in _SIGNING_PINS:
        if pin not in segment:
            return False, f"signed-URL uid binding lost ({label}): {pin!r}"

    # Structural check: the "User not authorized" raise must actually live
    # inside a branch that compares current_user_id with the signed uid.
    guarded_raise = False
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        test = ast.unparse(node.test)
        if "current_user_id" not in test or "user_id" not in test:
            continue
        body = "\n".join(ast.unparse(n) for n in ast.walk(node) if isinstance(n, ast.Raise))
        if "SignatureInvalidError" in body and "User not authorized" in body:
            guarded_raise = True
    if not guarded_raise:
        return False, "uid rejection is not guarded by a current_user_id comparison"
    return True, "signed uid is checked against current_user_id before the URL is honoured"


def test_signed_url_uid_binding_pinned_at_head(commit_sha: str) -> None:
    ok, detail = _evaluate_signing(_committed(commit_sha, _SIGNING_TARGET))
    assert ok, detail


_SIGNING_REVERTS: tuple[tuple[str, str, str], ...] = (
    (
        "no-uid-param-read",
        _UID_PARSE,
        'user_id_str = params.get("uid_unused", [None])[0]',
    ),
    (
        "no-uid-guard",
        _UID_GUARD_INNER,
        "if False:",
    ),
    (
        "no-uid-rejection",
        _UID_GUARD_RAISE,
        "pass  # no uid rejection",
    ),
)


@pytest.mark.parametrize(
    "name,old,new", _SIGNING_REVERTS, ids=[r[0] for r in _SIGNING_REVERTS]
)
def test_each_signing_pin_is_falsifiable(
    commit_sha: str, name: str, old: str, new: str
) -> None:
    source = _committed(commit_sha, _SIGNING_TARGET)
    assert old in source, f"revert fixture {name!r} no longer matches committed source"
    ok, detail = _evaluate_signing(source.replace(old, new))
    assert not ok, f"synthetic signed-URL revert {name!r} was not caught: {detail}"


# ---------------------------------------------------------------------------
# Gutted blobs must never pass vacuously
# ---------------------------------------------------------------------------


def test_gutted_blobs_are_rejected() -> None:
    ok, detail = _evaluate_dsar("# gutted DSAR manager\n")
    assert not ok
    assert "DSARManager" in detail

    ok, detail = _evaluate_signing("# gutted signing module\n")
    assert not ok
    assert "UrlSigner" in detail
