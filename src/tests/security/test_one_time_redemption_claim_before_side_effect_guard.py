"""Static guard: one-time redemption must CLAIM before the expensive side effect.

Two recent fixes closed the same TOCTOU shape from opposite ends:

* N729 (``a36998e4``) -- ``redeem_download_ticket`` used to resolve the ticket
  (a non-consuming read), mint a signed download URL, and only then perform the
  atomic ``redeemed_at IS NULL`` CAS. Two concurrent callers both reached
  ``sign_url`` and produced two usable bearer grants from a strictly single-use
  ticket. The fix claims the ticket FIRST and signs only for the winner.
* N710 (``81cfd505``) -- ``complete_upload_session`` used to hand the assembled
  temp file to the durable-upload pipeline (a file write + quota reserve) after
  a *read* of the upload session. Two callers both reserved quota for one
  upload. The fix routes the claim through the manager's single-use CAS
  (``COMPLETED -> REDEEMING``, owner + ``updated_at`` bound) which now runs
  before the route's ``upload_stream`` side effect.

This guard is the committed-tree counterpart of those behavioral tests. It
reads blobs straight out of ``HEAD`` (``git show <sha>:<path>``), never the
working tree, so a peer staging a stale whole-file copy cannot silently revert
the ordering before any behavioral test runs. HEAD is resolved once per run.

Three layers make it non-vacuous:

1. ``REDEMPTION_SURFACES`` -- an explicit inventory of the redemption routes
   with the exact claim call and irreversible side-effect call each one must
   keep in order. Each is parsed and the claim must precede EVERY side effect
   in the same function (statement/line ordering over the live source).
2. ``DOCUMENTED_ALLOWANCES`` -- side effects that are harmless/idempotent may
   precede the claim, but only with a written reason. The stash guess-budget
   counter spend is the one documented case.
3. ``_discover_bad_orderings`` -- a live pass over the redemption-relevant
   roots: any function with BOTH a single-use-claim call and an irreversible
   side effect whose side effect precedes the claim fails closed unless it is
   allowlisted. A *new* redemption with the bad ordering is therefore caught
   without editing this file.

The ordering evaluator is shared by the real committed-blob checks and the
synthetic self-tests, so a detector that never fires cannot pass the real scan.
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Committed-blob reader
# ---------------------------------------------------------------------------


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
    """Resolve HEAD once per run so every blob read is pinned to one commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _committed(commit: str, path: str) -> str:
    proc = _git("show", f"{commit}:{path}")
    assert proc.returncode == 0, (
        f"cannot read committed {path!r}: {proc.stderr.strip()}"
    )
    return proc.stdout


def _tracked_py_files(commit: str, root: str) -> list[str]:
    """Tracked .py paths under ``root`` (a file or directory) at ``commit``."""
    proc = _git("ls-tree", "-r", "--name-only", commit, root)
    assert proc.returncode == 0, f"cannot list {root!r} at HEAD: {proc.stderr.strip()}"
    paths = [p for p in proc.stdout.splitlines() if p.endswith(".py")]
    if not paths and root.endswith(".py"):
        paths = [root]
    return paths


# ---------------------------------------------------------------------------
# Inventory / policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RedemptionSurface:
    """A redemption route whose single-use claim must precede a side effect."""

    key: str
    path: str
    qualname: str
    claim_calls: tuple[str, ...]
    side_effect_calls: tuple[str, ...]
    why: str


@dataclass(frozen=True)
class Allowance:
    """A documented side effect allowed to precede the claim, with a reason."""

    key: str
    side_effect_call: str
    reason: str


REDEMPTION_SURFACES = (
    RedemptionSurface(
        key="stash-download-ticket-sign",
        path="src/api/routes/suite/stash/grants.py",
        qualname="redeem_download_ticket",
        claim_calls=("redeem_download_ticket",),
        side_effect_calls=(
            "sign_url",  # irreversible: mints a short-lived bearer download grant
            "_enforce_download_ticket_redeem_guess_limit",  # allowlisted below
        ),
        why=(
            "N729 a36998e4: the atomic redeemed_at IS NULL CAS must win before "
            "sign_url mints a signed download grant, else two racers each get a "
            "usable grant from one strictly single-use ticket"
        ),
    ),
    RedemptionSurface(
        key="chunked-upload-redemption",
        path="src/api/routes/media.py",
        qualname="complete_upload_session",
        claim_calls=("complete_upload_session_stream",),
        side_effect_calls=(
            "upload_stream",  # file write + storage-quota reserve
        ),
        why=(
            "N710 81cfd505: the route's claim resolves to the manager's "
            "COMPLETED -> REDEEMING single-use CAS and must run before "
            "upload_stream writes the file / reserves quota, else two callers "
            "reserve quota once for one upload and race read vs. write"
        ),
    ),
)

DOCUMENTED_ALLOWANCES = (
    Allowance(
        key="stash-download-ticket-sign",
        side_effect_call="_enforce_download_ticket_redeem_guess_limit",
        reason=(
            "Bounded shared rate-limit counter spend for the wrong-guess "
            "budget; harmless/idempotent from the single-use perspective and "
            "intentionally runs before any ticket lookup so a throttled answer "
            "never discloses ticket state (fails closed on limiter fault)"
        ),
    ),
)

# Master set used by the discovery pass: irreversible/non-idempotent side
# effects that must never precede a single-use claim.
IRREVERSIBLE_SIDE_EFFECTS = frozenset(
    {
        "sign_url",  # signed bearer capability / grant
        "upload_stream",  # durable file write + quota reserve
        "write_file",
        "put_object",
        "reserve_quota",
        "reserve_storage",
        "grant",
        "send",
        "send_email",
    }
)

# Substrings that mark a call as a single-use claim in the discovery pass.
# Guard/limit helpers are explicitly excluded so a rate-limit check named
# "..._redeem_guess_limit" is not mistaken for the one-time claim.
CLAIM_VERBS = (
    "redeem",
    "consume",
    "claim",
    "exchange_code",
    "verify_state",
    "complete_upload_session_stream",
    "complete_session_stream",
    "spend",
)
CLAIM_EXCLUSIONS = ("guess", "limit", "budget", "enforce", "_check", "rate")

SCAN_ROOTS = (
    "src/api/routes/suite/stash",
    "src/api/routes/media.py",
    "src/core/media",
    "src/core/suite/stash/stash_manager_items",
)

FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _find_function(tree: ast.AST, qualname: str):
    """Find a module function or ``Class.method`` in a parsed module."""
    parts = qualname.split(".")
    if len(parts) == 1:
        return next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, FUNC_TYPES) and n.name == parts[0]
            ),
            None,
        )
    owner = next(
        (
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == parts[0]
        ),
        None,
    )
    if owner is None:
        return None
    return next(
        (n for n in owner.body if isinstance(n, FUNC_TYPES) and n.name == parts[1]),
        None,
    )


def _functions_with_qualname(tree: ast.AST):
    """Yield ``(qualname, node)`` for module functions and class methods."""
    for node in tree.body:
        if isinstance(node, FUNC_TYPES):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, FUNC_TYPES):
                    yield f"{node.name}.{method.name}", method


def _call_ident(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _calls_with_lines(fn) -> dict[str, int]:
    """Map each call identifier to its first (lowest) line in ``fn``.

    Nested helper bodies are included on purpose: the chunked route performs
    its file write inside a nested ``_upload_complete_file`` passed to
    ``run_in_threadpool``; its source line is still after the claim.
    """
    out: dict[str, int] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            ident = _call_ident(node)
            if ident:
                out[ident] = min(out.get(ident, node.lineno), node.lineno)
    return out


def _literal_text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_text(node.left), _literal_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _is_claim_call(ident: str) -> bool:
    low = ident.lower()
    if any(ex in low for ex in CLAIM_EXCLUSIONS):
        return False
    return any(verb in low for verb in CLAIM_VERBS)


def _allowances_for(surface: RedemptionSurface) -> dict[str, str]:
    return {
        a.side_effect_call: a.reason
        for a in DOCUMENTED_ALLOWANCES
        if a.key == surface.key
    }


def _allowances_by_key() -> dict[tuple[str, str], dict[str, str]]:
    keys = {(s.path, s.qualname): s.key for s in REDEMPTION_SURFACES}
    out: dict[tuple[str, str], dict[str, str]] = {}
    for a in DOCUMENTED_ALLOWANCES:
        for (path, qualname), key in keys.items():
            if key == a.key:
                out.setdefault((path, qualname), {})[a.side_effect_call] = a.reason
    return out


# ---------------------------------------------------------------------------
# Evaluators -- shared by the real committed-blob checks and the self-tests
# ---------------------------------------------------------------------------


def _evaluate_order(
    source: str,
    qualname: str,
    claim_calls: tuple[str, ...],
    side_effect_calls: tuple[str, ...],
    allowances: dict[str, str],
) -> tuple[bool, str]:
    """A claim call must precede every side effect in the same function."""
    tree = ast.parse(source)
    fn = _find_function(tree, qualname)
    if fn is None:
        return False, f"redemption surface {qualname!r}: function missing from blob"
    calls = _calls_with_lines(fn)

    missing_claim = [c for c in claim_calls if c not in calls]
    missing_side = [s for s in side_effect_calls if s not in calls]
    if missing_claim or missing_side:
        return False, (
            f"redemption surface {qualname!r} lost a pinned call: "
            f"missing claim={missing_claim} side_effect={missing_side}"
        )

    claim_line = min(calls[c] for c in claim_calls)
    violations: list[str] = []
    for side in side_effect_calls:
        if calls[side] <= claim_line and side not in allowances:
            violations.append(
                f"{side}() at line {calls[side]} precedes claim "
                f"{claim_calls[0]}() at line {claim_line}"
            )
    if violations:
        return False, (
            "CLAIM-BEFORE-SIDE-EFFECT VIOLATION in "
            f"{qualname!r}: " + "; ".join(violations)
        )
    return True, f"{qualname!r}: claim precedes all irreversible side effects"


def _evaluate_manager_cas(source: str) -> tuple[bool, str]:
    """The chunked manager's single-use CAS must precede session handoff."""
    tree = ast.parse(source)
    fn = _find_function(tree, "ChunkedUploadManager.complete_session_stream")
    if fn is None:
        return False, "ChunkedUploadManager.complete_session_stream missing from blob"

    cas_lines: list[int] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or _call_ident(node) != "execute":
            continue
        for arg in node.args:
            sql = _literal_text(arg)
            if not sql:
                continue
            if (
                "redeeming" in sql
                and "status = 'completed'" in sql
                and "updated_at = ?" in sql
                and "user_id = ?" in sql
            ):
                cas_lines.append(node.lineno)
    if not cas_lines:
        return False, "complete_session_stream lost its single-use REDEEMING CAS"

    has_rowcount = any(
        (isinstance(n, ast.Attribute) and n.attr == "rowcount")
        or (isinstance(n, ast.Constant) and n.value == "rowcount")
        for n in ast.walk(fn)
    )
    if not has_rowcount:
        return False, "complete_session_stream lost the rowcount fail-closed arm"

    # A handoff is a return of a live session object (``return session``), not
    # the ``return None`` fail-closed arms. Every such handoff must come after
    # the claim; an early one would give a caller an unredeemed temp path.
    handoffs = [
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Name)
    ]
    if not handoffs or min(cas_lines) >= min(handoffs):
        return False, (
            "complete_session_stream hands the redeemable session back before "
            "the single-use CAS runs"
        )
    return True, "complete_session_stream claims (CAS) before handing off the session"


def _discover_bad_orderings(
    source: str,
    path: str,
    inventory_keys: set[tuple[str, str]],
    allowances: dict[tuple[str, str], dict[str, str]],
) -> list[str]:
    """Find unlisted redemptions whose irreversible side effect precedes claim."""
    tree = ast.parse(source)
    findings: list[str] = []
    for qualname, fn in _functions_with_qualname(tree):
        key = (path, qualname)
        if key in inventory_keys:
            continue  # covered by the pinned curated surface instead
        calls = _calls_with_lines(fn)
        claim_ids = sorted(i for i in calls if _is_claim_call(i))
        side_ids = sorted(i for i in calls if i in IRREVERSIBLE_SIDE_EFFECTS)
        if not claim_ids or not side_ids:
            continue
        claim_line = min(calls[c] for c in claim_ids)
        allowed = allowances.get(key, {})
        for side in side_ids:
            if calls[side] <= claim_line and side not in allowed:
                findings.append(
                    f"{path}:{qualname}: side effect {side}() at line {calls[side]} "
                    f"precedes claim {claim_ids[0]}() at line {claim_line}"
                )
    return findings


# ---------------------------------------------------------------------------
# Real committed-tree checks
# ---------------------------------------------------------------------------


def test_curated_redemption_surfaces_claim_before_side_effect(commit_sha: str) -> None:
    """Both pinned redemptions keep the claim before the irreversible side effect."""
    assert len(REDEMPTION_SURFACES) >= 2, "the inventory was gutted"
    for surface in REDEMPTION_SURFACES:
        blob = _committed(commit_sha, surface.path)
        ok, detail = _evaluate_order(
            blob,
            surface.qualname,
            surface.claim_calls,
            surface.side_effect_calls,
            _allowances_for(surface),
        )
        assert ok, f"{surface.key}: {detail}\n{surface.why}"


def test_chunked_manager_claims_before_session_handoff(commit_sha: str) -> None:
    """The route's claim really is a single-use CAS that runs before handoff."""
    blob = _committed(commit_sha, "src/core/media/chunked.py")
    ok, detail = _evaluate_manager_cas(blob)
    assert ok, detail


def test_no_new_redemption_side_effect_before_claim(commit_sha: str) -> None:
    """Fail closed on any new redemption with the side effect before the claim."""
    inventory_keys = {(s.path, s.qualname) for s in REDEMPTION_SURFACES}
    findings: list[str] = []
    for root in SCAN_ROOTS:
        for path in _tracked_py_files(commit_sha, root):
            findings.extend(
                _discover_bad_orderings(
                    _committed(commit_sha, path),
                    path,
                    inventory_keys,
                    _allowances_by_key(),
                )
            )
    assert not findings, "NEW REDEMPTION WITH SIDE EFFECT BEFORE CLAIM:\n" + "\n".join(
        findings
    )


# ---------------------------------------------------------------------------
# Self-tests -- prove the evaluator fires on bad orderings and accepts good ones
# ---------------------------------------------------------------------------


def test_order_evaluator_flags_bad_ordering() -> None:
    bad = (
        "async def redeem_download_ticket(body, user):\n"
        "    signed = media.sign_url(1)\n"  # side effect FIRST
        "    result = mgr.redeem_download_ticket(user, body.ticket)\n"
        "    return signed\n"
    )
    ok, detail = _evaluate_order(
        bad,
        "redeem_download_ticket",
        ("redeem_download_ticket",),
        ("sign_url",),
        {},
    )
    assert not ok and "CLAIM-BEFORE-SIDE-EFFECT VIOLATION" in detail, detail


def test_order_evaluator_accepts_correct_ordering() -> None:
    good = (
        "async def redeem_download_ticket(body, user):\n"
        "    result = mgr.redeem_download_ticket(user, body.ticket)\n"
        "    signed = media.sign_url(1)\n"  # side effect AFTER the claim
        "    return signed\n"
    )
    ok, detail = _evaluate_order(
        good,
        "redeem_download_ticket",
        ("redeem_download_ticket",),
        ("sign_url",),
        {},
    )
    assert ok, detail


def test_order_evaluator_allowlist_and_missing_claim() -> None:
    bad = (
        "def redeem_download_ticket(body, user):\n"
        "    media.sign_url(1)\n"
        "    mgr.redeem_download_ticket(user, body.ticket)\n"
    )
    # Undocumented bad ordering is flagged; a documented harmless/idempotent
    # side effect with a reason is accepted.
    ok, detail = _evaluate_order(
        bad,
        "redeem_download_ticket",
        ("redeem_download_ticket",),
        ("sign_url",),
        {},
    )
    assert not ok, detail
    ok, detail = _evaluate_order(
        bad,
        "redeem_download_ticket",
        ("redeem_download_ticket",),
        ("sign_url",),
        {"sign_url": "read-only signature preview; idempotent"},
    )
    assert ok, detail
    # A surface that lost its claim call fails closed rather than passing vacuously.
    ok, detail = _evaluate_order(
        "def redeem_download_ticket(body, user):\n    media.sign_url(1)\n",
        "redeem_download_ticket",
        ("redeem_download_ticket",),
        ("sign_url",),
        {},
    )
    assert not ok and "lost a pinned call" in detail, detail


def test_curated_surface_call_names_are_flagged_when_reordered() -> None:
    """Each real inventory entry is detector-visible, not a decorative pin."""
    for surface in REDEMPTION_SURFACES:
        allowances = _allowances_for(surface)
        critical = next(s for s in surface.side_effect_calls if s not in allowances)
        bad = (
            f"def f():\n"
            f"    svc.{critical}(...)\n"  # irreversible side effect FIRST
            f"    svc.{surface.claim_calls[0]}(...)\n"
        )
        ok, detail = _evaluate_order(
            bad,
            "f",
            surface.claim_calls,
            surface.side_effect_calls,
            allowances,
        )
        assert not ok, f"{surface.key}: synthetic bad ordering not flagged"


def test_manager_cas_evaluator_self_test() -> None:
    good = (
        "class ChunkedUploadManager:\n"
        "    def complete_session_stream(self, sid, uid):\n"
        "        session = self.get_session(sid, uid)\n"
        "        cursor = self._db.execute(\n"
        "            \"UPDATE media_upload_sessions SET status = 'redeeming' \"\n"
        "            \"WHERE id = ? AND user_id = ? AND status = 'completed' \"\n"
        "            \"AND updated_at = ?\",\n"
        "            (uid, sid),\n"
        "        )\n"
        "        if getattr(cursor, \"rowcount\", 1) == 0:\n"
        "            return None\n"
        "        return session\n"
    )
    ok, detail = _evaluate_manager_cas(good)
    assert ok, detail
    # Reordering the handoff before the CAS is flagged (rowcount arm retained
    # so the check reaches the ordering assertion rather than failing earlier).
    bad = (
        "class ChunkedUploadManager:\n"
        "    def complete_session_stream(self, sid, uid):\n"
        "        session = self.get_session(sid, uid)\n"
        "        return session\n"
        "        cursor = self._db.execute(\n"
        "            \"UPDATE media_upload_sessions SET status = 'redeeming' \"\n"
        "            \"WHERE id = ? AND user_id = ? AND status = 'completed' \"\n"
        "            \"AND updated_at = ?\",\n"
        "            (uid, sid),\n"
        "        )\n"
        "        if getattr(cursor, \"rowcount\", 1) == 0:\n"
        "            return None\n"
    )
    ok, detail = _evaluate_manager_cas(bad)
    assert not ok and "before" in detail, detail


def test_discovery_flags_new_bad_redemption_and_accepts_good() -> None:
    bad = (
        "async def redeem_invite_once(code, user):\n"
        "    media.sign_url(7)\n"  # side effect FIRST -> must fail closed
        "    mgr.redeem_invite(code, user)\n"
    )
    assert _discover_bad_orderings(bad, "new.py", set(), {}) != []
    good = (
        "async def redeem_invite_once(code, user):\n"
        "    mgr.redeem_invite(code, user)\n"
        "    media.sign_url(7)\n"
    )
    assert _discover_bad_orderings(good, "new.py", set(), {}) == []
    # A documented allowance suppresses the finding with a reason.
    assert (
        _discover_bad_orderings(
            bad,
            "new.py",
            set(),
            {("new.py", "redeem_invite_once"): {"sign_url": "read-only preview"}},
        )
        == []
    )
