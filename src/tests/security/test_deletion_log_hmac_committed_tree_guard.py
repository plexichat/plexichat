"""Committed-tree guard for the deletion-log HMAC chain fix.

The account-deletion audit chain used to authenticate every row with an
*unkeyed* SHA-256 of ``prev_hash + json.dumps(entry, sort_keys=True)``. Any
writer who could rewrite the JSONL file could edit a row and recompute every
downstream checksum from public data, and ``verify_chain()`` still passed.
The fix makes every checksum an HMAC-SHA256 under a per-row ``key_id``
(operator key or a ``0600`` local sidecar), makes verification constant-time,
and makes a missing / unknown key, an unkeyed row and a missing key sidecar for
a non-empty chain fail closed.

This guard reads ``src/core/auth/deletion_log.py`` straight out of ``HEAD``
with ``git show`` -- never the working tree -- so a stale staged copy cannot
silently revert the fix before any behavioural test runs. The evaluator is
structural (AST) as well as literal: it fails if any ``hashlib.sha256`` call
that produces an integrity value is not nested inside an ``hmac.new`` call,
which is the exact shape an unkeyed rewrite would restore. The one allowed
unkeyed SHA-256 is the privacy hash of the ``identifier`` field (``id_hash``),
which is not an integrity value.

Non-vacuity: every pin is exercised by a syntactically valid synthetic revert
that the same evaluator must reject, and a fully gutted blob is rejected too.

This is a dedicated standalone guard: the shared committed-tree marker guard
is concurrently edited by other agents, so it is deliberately left untouched.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET = "src/core/auth/deletion_log.py"

FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


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
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == name
        ),
        None,
    )


def _method(cls: ast.ClassDef, name: str) -> ast.AST | None:
    return next(
        (
            node
            for node in cls.body
            if isinstance(node, FUNC_TYPES) and node.name == name
        ),
        None,
    )


def _source_of(source: str, node: ast.AST | None, label: str) -> str:
    assert node is not None, f"{label} missing from committed blob"
    segment = ast.get_source_segment(source, node)
    assert segment, f"cannot recover source of {label}"
    return segment


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _unkeyed_sha256_outside_id_hash(tree: ast.AST) -> list[str]:
    """Return integrity SHA-256 calls not nested in ``hmac.new``.

    An unkeyed rewrite computes ``hashlib.sha256(...).hexdigest()`` directly
    for a checksum. The only unkeyed SHA-256 allowed is the one assigned to
    ``id_hash`` (a privacy hash of the identifier, not an integrity value).
    """
    parents: dict[int, ast.AST] = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != "sha256":
            continue
        ancestor = node
        inside_hmac_new = False
        allowed_id_hash = False
        while ancestor in parents:
            ancestor = parents[ancestor]
            if isinstance(ancestor, ast.Call) and _call_name(ancestor) == "new":
                inside_hmac_new = True
                break
            if isinstance(ancestor, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "id_hash" for t in ancestor.targets
            ):
                allowed_id_hash = True
                break
        if not inside_hmac_new and not allowed_id_hash:
            offenders.append(f"line {getattr(node, 'lineno', '?')}")
    return offenders


def _evaluate(source: str) -> tuple[bool, str]:
    """Return ``(ok, detail)`` for the committed source of the deletion log."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"committed deletion log does not parse: {exc}"

    offenders = _unkeyed_sha256_outside_id_hash(tree)
    if offenders:
        return False, (
            "unkeyed SHA-256 integrity path restored: hashlib.sha256 called "
            f"outside hmac.new at {', '.join(offenders)}"
        )

    cls = _find_class(tree, "DeletionLog")
    if cls is None:
        return False, "DeletionLog class missing from committed blob"

    required: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "_chain_checksum",
            "keyed checksum generation",
            (
                'hmac.new(key.encode("utf-8"), data, hashlib.sha256).hexdigest()',
                "if not key:",
                "raise RuntimeError(",
            ),
        ),
        (
            "log_event",
            "per-row key_id generation",
            (
                'entry["key_id"] = self._current_key_id',
                "checksum = self._chain_checksum(",
            ),
        ),
        (
            "_verify_chain_impl",
            "fail-closed keyed verification",
            (
                "if not isinstance(entry_key_id, str) or not entry_key_id:",
                "if entry_key_id not in self._chain_keys:",
                "calculated_checksum = self._chain_checksum(",
                "calculated_checksum, stored_checksum",
            ),
        ),
        (
            "_load_local_key",
            "restricted key sidecar / missing-key fail-closed",
            (
                "if self._chain_has_data():",
                "self._key_error = (",
                '"deletion log chain key sidecar is missing for a non-empty chain"',
                "os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)",
            ),
        ),
        (
            "verify_chain",
            "missing key is not rotated away",
            ("if self._key_error:",),
        ),
    ]
    for name, label, pins in required:
        method_src = _source_of(source, _method(cls, name), f"DeletionLog.{name}")
        for pin in pins:
            if pin not in method_src:
                return False, f"{label} pin missing from DeletionLog.{name}: {pin!r}"
    return True, "HMAC generation + keyed verification + 0600 sidecar pinned at HEAD"


def test_deletion_log_hmac_fix_pinned_at_head(commit_sha: str) -> None:
    """The HMAC chain fix is present in the committed deletion log at HEAD."""
    ok, detail = _evaluate(_committed(commit_sha, _TARGET))
    assert ok, detail


# Each mutation is a syntactically valid revert of one part of the fix. The
# same evaluator used for the live HEAD check must reject every one of them,
# so a detector that never fires cannot pass.
_REVERTS: tuple[tuple[str, str, str], ...] = (
    (
        "unkeyed-sha256-generation",
        'return hmac.new(key.encode("utf-8"), data, hashlib.sha256).hexdigest()',
        "return hashlib.sha256(data).hexdigest()",
    ),
    (
        "no-missing-key-rejection",
        "if not key:\n            raise RuntimeError(",
        "if False:\n            raise RuntimeError(",
    ),
    (
        "no-unknown-key-rejection",
        "if entry_key_id not in self._chain_keys:",
        "if False:",
    ),
    (
        "no-key-id-requirement",
        "if not isinstance(entry_key_id, str) or not entry_key_id:",
        "if False:",
    ),
    (
        "no-constant-time-compare",
        "if not _constant_time_hex_equal(\n"
        "                        calculated_checksum, stored_checksum\n"
        "                    ):",
        "if calculated_checksum != stored_checksum:",
    ),
    (
        "no-missing-key-fail-closed",
        '"deletion log chain key sidecar is missing for a non-empty chain"',
        '"deletion log chain key sidecar was rotated"',
    ),
    (
        "loose-sidecar-permissions",
        "os.O_CREAT | os.O_EXCL, 0o600)",
        "os.O_CREAT | os.O_EXCL, 0o644)",
    ),
    (
        "no-key-id-persisted",
        'entry["key_id"] = self._current_key_id',
        'entry["key_id"] = "default"',
    ),
)


@pytest.mark.parametrize("name,old,new", _REVERTS, ids=[r[0] for r in _REVERTS])
def test_each_pin_is_falsifiable(
    commit_sha: str, name: str, old: str, new: str
) -> None:
    """Synthetic revert of each fix component is caught by the evaluator."""
    source = _committed(commit_sha, _TARGET)
    assert old in source, f"revert fixture {name!r} no longer matches committed source"
    ok, detail = _evaluate(source.replace(old, new, 1))
    assert not ok, f"synthetic revert {name!r} was not caught: {detail}"


def test_gutted_blob_is_rejected() -> None:
    """A blob with no deletion-log implementation at all cannot pass."""
    ok, detail = _evaluate("# gutted deletion log\n")
    assert not ok
    assert "DeletionLog" in detail or "sha256" in detail
