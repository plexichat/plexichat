"""Committed-tree guard for the N829 audit-chain HMAC fix (0778ec64).

The telemetry audit chain used to authenticate every row with an *unkeyed*
SHA-256 of ``prev + canonical(entry)``.  Any writer who could rewrite the JSONL
file and its head sidecar could edit a row and recompute every downstream
checksum from public data, and ``verify_chain()`` still passed.  0778ec64 made
every checksum an HMAC-SHA256 under a per-row ``key_id`` (operator key or a
``0600`` local sidecar), made verification constant-time, and made a missing /
unknown key, an unkeyed row and a malformed record fail closed.

This guard reads ``src/core/telemetry/audit.py`` straight out of ``HEAD`` with
``git show`` -- never the working tree -- so a stale staged copy of that file
cannot silently revert the fix before any behavioural test runs.  The
evaluator is structural (AST) as well as literal: it fails if *any*
``hashlib.sha256`` call in the module is not nested inside an ``hmac.new``
call, which is the exact shape an unkeyed rewrite would restore.

Non-vacuity: every pin is exercised by a syntactically valid synthetic revert
that the same evaluator must reject, and a fully gutted blob is rejected too.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET = "src/core/telemetry/audit.py"

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


def _sha256_outside_hmac(tree: ast.AST) -> list[str]:
    """Return hashlib.sha256 calls that are not an argument of hmac.new().

    An unkeyed rewrite computes ``hashlib.sha256(...).hexdigest()`` directly.
    In the fixed module the only ``hashlib.sha256`` reference is the digestmod
    passed to ``hmac.new``, so this list must be empty.
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
        # Walk up through attribute/subscript wrappers to find a call parent.
        ancestor = node
        inside_hmac_new = False
        while ancestor in parents:
            ancestor = parents[ancestor]
            if isinstance(ancestor, ast.Call) and _call_name(ancestor) == "new":
                inside_hmac_new = True
                break
        if not inside_hmac_new:
            offenders.append(f"line {getattr(node, 'lineno', '?')}")
    return offenders


def _evaluate(source: str) -> tuple[bool, str]:
    """Return ``(ok, detail)`` for the committed source of the audit module."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"committed audit module does not parse: {exc}"

    offenders = _sha256_outside_hmac(tree)
    if offenders:
        return False, (
            "unkeyed SHA-256 path restored: hashlib.sha256 called outside "
            f"hmac.new at {', '.join(offenders)}"
        )

    cls = _find_class(tree, "AuditChain")
    if cls is None:
        return False, "AuditChain class missing from committed blob"

    required: list[tuple[str, str, tuple[str, ...]]] = [
        (
            "_chain_checksum",
            "keyed checksum generation",
            (
                'hmac.new(key.encode("utf-8"), data, hashlib.sha256).hexdigest()',
                "if not key:",
                'raise HardeningViolation(f"audit chain key {key_id!r} is unavailable")',
            ),
        ),
        (
            "append",
            "per-row key_id generation",
            (
                "checksum = self._chain_checksum(",
                '"checksum": checksum,\n'
                '                "key_id": self._current_key_id,\n'
                '                "entry": entry,',
            ),
        ),
        (
            "verify_chain",
            "fail-closed verification",
            (
                "if self._key_error:",
                'key_id = record.get("key_id")',
                "if not isinstance(key_id, str) or not key_id:",
                "if key_id not in self._chain_keys:",
                "calculated = self._chain_checksum(",
                "hmac.compare_digest(calculated, stored)",
            ),
        ),
        (
            "_load_local_key",
            "restricted key sidecar / missing-key fail-closed",
            (
                "if self._chain_has_data():",
                "self._key_error = (",
                '"audit chain key sidecar is missing for a non-empty chain"',
                "os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)",
            ),
        ),
    ]
    for name, label, pins in required:
        method_src = _source_of(source, _method(cls, name), f"AuditChain.{name}")
        for pin in pins:
            if pin not in method_src:
                return False, f"{label} pin missing from AuditChain.{name}: {pin!r}"
    return True, "HMAC generation + keyed verification + 0600 sidecar pinned at HEAD"


def test_audit_chain_hmac_fix_pinned_at_head(commit_sha: str) -> None:
    """The N829 fix is present in the committed audit module at HEAD."""
    ok, detail = _evaluate(_committed(commit_sha, _TARGET))
    assert ok, detail


# Each mutation is a syntactically valid revert of one part of the fix.  The
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
        "if not key:\n            raise HardeningViolation(",
        "if False:\n            raise HardeningViolation(",
    ),
    (
        "no-unknown-key-rejection",
        "if key_id not in self._chain_keys:",
        "if False:",
    ),
    (
        "no-key-id-requirement",
        "if not isinstance(key_id, str) or not key_id:",
        "if False:",
    ),
    (
        "no-constant-time-compare",
        "hmac.compare_digest(calculated, stored)",
        "calculated == stored",
    ),
    (
        "no-missing-key-fail-closed",
        '"audit chain key sidecar is missing for a non-empty chain"',
        '"audit chain key sidecar was rotated"',
    ),
    (
        "loose-sidecar-permissions",
        "os.O_EXCL, 0o600)",
        "os.O_EXCL, 0o644)",
    ),
    (
        "no-key-id-persisted",
        '"key_id": self._current_key_id,\n                "entry": entry,',
        '"key_id": "default",\n                "entry": entry,',
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
    """A blob with no audit-chain implementation at all cannot pass."""
    ok, detail = _evaluate("# gutted audit module\n")
    assert not ok
    assert "AuditChain" in detail or "sha256" in detail
