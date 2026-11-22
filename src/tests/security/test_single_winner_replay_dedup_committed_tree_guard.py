"""Committed-tree guard for the remaining single-winner replay / dedup fixes.

N829's one-time-redemption claim-before-side-effect guard
(``test_one_time_redemption_claim_before_side_effect_guard.py``) already pins
that every redemption route claims before ``sign_url`` / file-write /
quota-reserve.  This guard covers the OTHER two "exactly one caller wins"
primitives that the same audit relies on, neither of which had a committed-tree
pin (only behavioral tests under ``src/tests/webhooks`` and ``src/tests/events``):

* webhook signature replay consumes a signature-keyed marker with an atomic
  ``SET NX`` (``set_nx(..., ttl=...)``) and compares signatures / stored markers
  in constant time (``hmac.compare_digest``) -- so two workers behind a load
  balancer cannot both accept one captured signature inside the replay window;
* cross-worker event dedup uses a single bounded (``max_entries``, FIFO
  eviction), TTL-expiring (``monotonic() - _ttl_sec``) set of event ids guarded
  by a ``threading.Lock`` held across check-and-record -- so a self-echo / retry
  cannot produce a duplicate delivery and the registry cannot leak memory.

Like the sibling guards, every check reads blobs straight out of ``HEAD``
(``git show <sha>:<path>``), never the working tree, so a peer staging a stale
whole-file copy cannot silently regress a fix before any behavioral test runs.
HEAD is resolved once per run.

Non-vacuity: each evaluator is shared by the real committed-blob checks and by
synthetic self-tests that erase one guard at a time (SET NX, TTL kwarg,
compare_digest, claim call, lock, size bound, TTL sweep, sweep call). A detector
that never fires cannot pass the real scan.
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]

FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


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


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _find_function(tree: ast.AST, name: str):
    return next(
        (n for n in ast.walk(tree) if isinstance(n, FUNC_TYPES) and n.name == name),
        None,
    )


def _call_ident(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _call_idents(node: ast.AST) -> set[str]:
    return {
        ident
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and (ident := _call_ident(n))
    }


def _set_nx_with_ttl(node: ast.AST) -> bool:
    """True if ``node`` contains a ``set_nx(..., ttl=...)`` claim call."""
    return any(
        _call_ident(n) == "set_nx" and any(kw.arg == "ttl" for kw in n.keywords)
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
    )


def _holds_self_lock(fn) -> bool:
    """True if ``fn`` enters ``with self._lock:`` (attribute on Name ``self``)."""
    for node in ast.walk(fn):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                ctx = item.context_expr
                if (
                    isinstance(ctx, ast.Attribute)
                    and ctx.attr == "_lock"
                    and isinstance(ctx.value, ast.Name)
                    and ctx.value.id == "self"
                ):
                    return True
    return False


def _find_class(tree: ast.AST, name: str):
    return next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name),
        None,
    )


def _method(cls, name: str):
    return next(
        (n for n in cls.body if isinstance(n, FUNC_TYPES) and n.name == name),
        None,
    )


def _self_attrs_assigned(fn) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(fn):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                out.add(target.attr)
    return out


# ---------------------------------------------------------------------------
# Guard 2: webhook signature replay uses set_nx + compare_digest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebhookReplaySurface:
    key: str
    path: str
    claim_helper: str
    verifier: str
    why: str


WEBHOOK_REPLAY_SURFACES = (
    WebhookReplaySurface(
        key="webhook-ed25519-signature-replay",
        path="src/core/webhooks/manager/signature.py",
        claim_helper="_claim_shared_replay_key",
        verifier="verify_payload",
        why=(
            "an Ed25519-signed webhook must be single-use inside the replay "
            "window; SET NX is the single-winner decision across workers and "
            "compare_digest is the constant-time marker/signature check; a "
            "stale staged copy drops either and lets a captured signature be "
            "accepted twice"
        ),
    ),
    WebhookReplaySurface(
        key="application-hmac-webhook-replay",
        path="src/core/applications/manager/webhook.py",
        claim_helper="_claim_replay_key",
        verifier="verify_webhook_signature",
        why=(
            "a shared-secret application webhook must be single-use inside "
            "the skew window; SET NX claims the signature key atomically and "
            "compare_digest checks the HMAC in constant time; a stale staged "
            "copy drops either and reopens replay"
        ),
    ),
)


def _evaluate_webhook_replay(
    source: str, surface: WebhookReplaySurface
) -> tuple[bool, str]:
    tree = ast.parse(source)
    claim = _find_function(tree, surface.claim_helper)
    verifier = _find_function(tree, surface.verifier)
    if claim is None:
        return False, f"{surface.claim_helper}() missing from blob"
    if verifier is None:
        return False, f"{surface.verifier}() missing from blob"
    if not _set_nx_with_ttl(claim):
        return (
            False,
            f"{surface.claim_helper}() lost its atomic set_nx(..., ttl=...) claim",
        )
    calls = _call_idents(verifier)
    if "compare_digest" not in calls:
        return False, f"{surface.verifier}() lost its hmac.compare_digest check"
    if surface.claim_helper not in calls:
        return (
            False,
            f"{surface.verifier}() no longer consumes {surface.claim_helper}()",
        )
    return True, f"{surface.key}: set_nx(ttl=) claim + compare_digest present"


# ---------------------------------------------------------------------------
# Guard 3: cross-worker dedup uses a bounded TTL set under lock
# ---------------------------------------------------------------------------


DEDUP_PATH = "src/core/events/dedupe.py"
DEDUP_CLASS = "CrossWorkerDedupe"
_DEDUP_METHODS = (
    "__init__",
    "remember",
    "is_duplicate",
    "_store",
    "_sweep_expired_locked",
)


def _evaluate_dedupe(source: str) -> tuple[bool, str]:
    tree = ast.parse(source)
    cls = _find_class(tree, DEDUP_CLASS)
    if cls is None:
        return False, f"{DEDUP_CLASS} missing from blob"
    methods = {}
    for name in _DEDUP_METHODS:
        method = _method(cls, name)
        if method is None:
            return False, f"{DEDUP_CLASS}.{name} missing from blob"
        methods[name] = method

    attrs = _self_attrs_assigned(methods["__init__"])
    for required in ("_lock", "_seen", "_max_entries", "_ttl_sec"):
        if required not in attrs:
            return False, f"__init__ lost self.{required}"

    for name in ("remember", "is_duplicate"):
        if not _holds_self_lock(methods[name]):
            return False, f"{name}() no longer holds self._lock"

    dup_calls = _call_idents(methods["is_duplicate"])
    for required in ("_sweep_expired_locked", "_store"):
        if required not in dup_calls:
            return False, f"is_duplicate() no longer calls {required}()"

    bounded = False
    for node in ast.walk(methods["_store"]):
        if not isinstance(node, ast.While):
            continue
        test = ast.dump(node.test)
        if "_max_entries" not in test or "len" not in test:
            continue
        if any(
            _call_ident(sub) == "popitem"
            and any(kw.arg == "last" for kw in sub.keywords)
            for sub in ast.walk(node)
            if isinstance(sub, ast.Call)
        ):
            bounded = True
    if not bounded:
        return (
            False,
            "_store() lost the bounded FIFO eviction "
            "(len(_seen) > _max_entries / popitem(last=False))",
        )

    ttl_ok = any(
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Sub)
        and "monotonic" in ast.dump(node.left)
        and "_ttl_sec" in ast.dump(node.right)
        for node in ast.walk(methods["_sweep_expired_locked"])
    )
    if not ttl_ok:
        return (
            False,
            "_sweep_expired_locked() lost the TTL expiry (monotonic() - _ttl_sec)",
        )
    return True, f"{DEDUP_CLASS}: bounded FIFO + TTL sweep + self._lock"


# ---------------------------------------------------------------------------
# Real committed-tree checks
# ---------------------------------------------------------------------------


def test_webhook_replay_single_winner_guards(commit_sha: str) -> None:
    assert len(WEBHOOK_REPLAY_SURFACES) >= 2, "the webhook inventory was gutted"
    for surface in WEBHOOK_REPLAY_SURFACES:
        ok, detail = _evaluate_webhook_replay(
            _committed(commit_sha, surface.path), surface
        )
        assert ok, f"{surface.key}: {detail}\n{surface.why}"


def test_cross_worker_dedupe_bounded_ttl_lock_guard(commit_sha: str) -> None:
    ok, detail = _evaluate_dedupe(_committed(commit_sha, DEDUP_PATH))
    assert ok, detail


# ---------------------------------------------------------------------------
# Self-tests -- prove each evaluator fires when its guard is erased
# ---------------------------------------------------------------------------


_GOOD_WEBHOOK = """\
import hmac

def _claim_shared_replay_key(key):
    set_nx = getattr(client, "set_nx", None)
    if not callable(set_nx):
        return None
    return bool(set_nx(key, "1", ttl=600))

def verify_payload(payload, signature):
    if not hmac.compare_digest(signature, "expected"):
        return False
    claimed = _claim_shared_replay_key("replay:key")
    return claimed is not False
"""

_WEBHOOK_BAD = {
    "no-set-nx": _GOOD_WEBHOOK.replace(
        'set_nx(key, "1", ttl=600)', 'cache_set(key, "1", ttl=600)'
    ),
    "no-ttl": _GOOD_WEBHOOK.replace('set_nx(key, "1", ttl=600)', 'set_nx(key, "1")'),
    "no-compare-digest": _GOOD_WEBHOOK.replace(
        'if not hmac.compare_digest(signature, "expected"):',
        'if signature != "expected":',
    ),
    "no-claim-consumed": _GOOD_WEBHOOK.replace(
        'claimed = _claim_shared_replay_key("replay:key")',
        'claimed = cache_get("replay:key")',
    ),
}


def test_webhook_replay_evaluator_non_vacuous() -> None:
    surface = WEBHOOK_REPLAY_SURFACES[0]
    ok, detail = _evaluate_webhook_replay(_GOOD_WEBHOOK, surface)
    assert ok, f"synthetic good blob not accepted: {detail}"
    assert _WEBHOOK_BAD["no-set-nx"] != _GOOD_WEBHOOK
    for kind, blob in _WEBHOOK_BAD.items():
        ok, detail = _evaluate_webhook_replay(blob, surface)
        assert not ok, f"webhook replay evaluator did not flag {kind}: {detail}"


def test_webhook_replay_pins_are_distinctive() -> None:
    """A naive verifier with no SET NX / CT compare is rejected outright."""
    stale = (
        "def verify_payload(payload, signature):\n"
        "    if signature != 'expected':\n"
        "        return False\n"
        "    cache_set('replay:key', '1')\n"
        "    return True\n"
    )
    ok, _ = _evaluate_webhook_replay(stale, WEBHOOK_REPLAY_SURFACES[0])
    assert not ok


_GOOD_DEDUP = """\
import threading
import time
from collections import OrderedDict

class CrossWorkerDedupe:
    def __init__(self, max_entries=4096, ttl_sec=300.0):
        self._max_entries = max(1, int(max_entries))
        self._ttl_sec = float(ttl_sec)
        self._seen = OrderedDict()
        self._lock = threading.Lock()

    def remember(self, event_id):
        with self._lock:
            self._store(event_id)

    def is_duplicate(self, event_id):
        with self._lock:
            self._sweep_expired_locked()
            if event_id in self._seen:
                return True
            self._store(event_id)
            return False

    def _store(self, event_id):
        self._seen[event_id] = time.monotonic()
        while len(self._seen) > self._max_entries:
            self._seen.popitem(last=False)

    def _sweep_expired_locked(self):
        cutoff = time.monotonic() - self._ttl_sec
        while self._seen and next(iter(self._seen.values())) < cutoff:
            self._seen.popitem(last=False)
"""

_DEDUP_BAD = {
    "no-lock": _GOOD_DEDUP.replace("with self._lock:", "if True:"),
    "unbounded": _GOOD_DEDUP.replace(
        "while len(self._seen) > self._max_entries:", "while False:"
    ),
    "no-ttl": _GOOD_DEDUP.replace(
        "time.monotonic() - self._ttl_sec", "time.monotonic() - 0"
    ),
    "no-sweep-call": _GOOD_DEDUP.replace("self._sweep_expired_locked()", "pass"),
}


def test_dedupe_evaluator_non_vacuous() -> None:
    ok, detail = _evaluate_dedupe(_GOOD_DEDUP)
    assert ok, f"synthetic good blob not accepted: {detail}"
    for kind, blob in _DEDUP_BAD.items():
        ok, detail = _evaluate_dedupe(blob)
        assert not ok, f"dedupe evaluator did not flag {kind}: {detail}"


def test_dedupe_pins_are_distinctive() -> None:
    """An unbounded, lock-free seen-set is rejected outright."""
    stale = (
        "class CrossWorkerDedupe:\n"
        "    def __init__(self):\n"
        "        self._seen = set()\n"
        "    def is_duplicate(self, event_id):\n"
        "        if event_id in self._seen:\n"
        "            return True\n"
        "        self._seen.add(event_id)\n"
        "        return False\n"
    )
    ok, _ = _evaluate_dedupe(stale)
    assert not ok
