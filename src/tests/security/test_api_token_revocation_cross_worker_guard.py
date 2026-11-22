"""API access-token revocation must propagate across workers (fail closed).

``verify_api_access_token`` memoises its read-only verdict in a *process-local*
``BoundedLRU`` (``_API_TOKEN_VERIFY_CACHE``). Unlike the session/bot-token
``token_verify:*`` cache, which is stored in shared Valkey and invalidated with
``invalidate_pattern`` (visible to every worker), this cache is cleared only by
``_invalidate_api_token_verify_cache()`` -- an in-process ``.clear()``. When a
token is revoked (or rotated, which revokes the old row) on worker A, worker B
keeps a warm ``authentic=True`` verdict for the full 10s TTL and, because the
cache-hit path never re-read the authoritative ``revoked`` flag, keeps
authenticating a dead credential.

This guard:
  1. proves the gap with a non-vacuous two-worker test (two distinct cache
     instances, one shared authoritative DB): a revoke on worker A must not
     leave worker B authenticating from its warm verdict;
  2. pins the fail-closed re-check in the *committed* source tree (via
     ``git show HEAD:<blob>``, never the working tree) so a future edit cannot
     silently drop the cross-worker re-check.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from src.core.auth._lazy import _get_auth_manager
import src.core.auth.managers.api_token as api_token_mod
from src.core.bounded_lru import BoundedLRU

pytestmark = [pytest.mark.auth]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_API_TOKEN_PATH = "src/core/auth/managers/api_token.py"

_IP = "203.0.113.10"
_UA = "cross-worker-guard/1.0"


def _fresh_worker_cache() -> BoundedLRU:
    """A distinct process-local verification cache (one per simulated worker)."""
    return BoundedLRU(
        max_entries=api_token_mod._API_TOKEN_VERIFY_MAX_ENTRIES,
        max_bytes=api_token_mod._API_TOKEN_VERIFY_MAX_BYTES,
        ttl_seconds=api_token_mod._API_TOKEN_VERIFY_TTL_SECONDS,
    )


# ---------------------------------------------------------------------------
# 1. Behavioural cross-worker proof (uses the real verifier + shared DB)
# ---------------------------------------------------------------------------


def test_revoke_on_one_worker_denies_warm_verdict_on_another(auth_manager, monkeypatch):
    """A warm 'valid' verdict on worker B cannot outlive a revoke on worker A."""
    mgr = _get_auth_manager().get_instance()
    token = mgr.create_api_access_token("cross-worker", created_by=1)

    worker_b = _fresh_worker_cache()
    worker_a = _fresh_worker_cache()

    # Worker B verifies the live token -> its local cache holds a positive
    # verdict (non-vacuity: the cache is actually warm, not a cold DB hit).
    monkeypatch.setattr(api_token_mod, "_API_TOKEN_VERIFY_CACHE", worker_b)
    assert (
        mgr.verify_api_access_token(token.token, ip_address=_IP, user_agent=_UA) is True
    )
    assert len(worker_b) == 1, "worker B must hold a warm cached verdict"

    # Worker A revokes. Its own local cache is cleared, but worker B's is not
    # (the invalidation hook is process-local). Prove no eager cross-worker
    # invalidation reached worker B's BoundedLRU.
    monkeypatch.setattr(api_token_mod, "_API_TOKEN_VERIFY_CACHE", worker_a)
    assert mgr.revoke_api_access_token(token.id, revoked_by=1) is True
    assert len(worker_a) == 0, "worker A's local cache must be cleared on revoke"
    assert len(worker_b) == 1, "revoke must not magically clear worker B's cache"

    # Worker B now replays its warm verdict. With a cache-hit authoritative
    # re-check this is denied; without it, the revoked token authenticates for
    # the rest of the TTL. This is the stale-verdict gap.
    monkeypatch.setattr(api_token_mod, "_API_TOKEN_VERIFY_CACHE", worker_b)
    assert (
        mgr.verify_api_access_token(token.token, ip_address=_IP, user_agent=_UA)
        is False
    ), "revoked token was replayed from a warm verdict on another worker"


def test_rotation_on_one_worker_denies_old_secret_on_another(auth_manager, monkeypatch):
    """Rotating (which revokes the old row) must kill the old secret everywhere."""
    mgr = _get_auth_manager().get_instance()
    token = mgr.create_api_access_token("rotate-cross-worker", created_by=1)

    worker_b = _fresh_worker_cache()
    monkeypatch.setattr(api_token_mod, "_API_TOKEN_VERIFY_CACHE", worker_b)
    assert (
        mgr.verify_api_access_token(token.token, ip_address=_IP, user_agent=_UA) is True
    )
    assert len(worker_b) == 1

    # Rotation happens on another worker (fresh local cache).
    monkeypatch.setattr(api_token_mod, "_API_TOKEN_VERIFY_CACHE", _fresh_worker_cache())
    rotated = mgr.rotate_api_access_token(token.id, rotated_by=1)
    assert rotated is not None

    # No grace window: the old secret's warm verdict must fail closed.
    monkeypatch.setattr(api_token_mod, "_API_TOKEN_VERIFY_CACHE", worker_b)
    assert (
        mgr.verify_api_access_token(token.token, ip_address=_IP, user_agent=_UA)
        is False
    ), "old secret from a rotated token authenticated during a grace window"


# ---------------------------------------------------------------------------
# 2. Committed-tree pin of the fail-closed re-check
# ---------------------------------------------------------------------------

# Distinctive source markers that a stale/removed re-check would not contain.
REQUIRED_MARKERS: dict[str, dict[str, str]] = {
    "_api_token_verdict_still_authoritative": {
        "authoritative revoked re-read by id": (
            "SELECT revoked FROM auth_api_access_tokens WHERE id = ?"
        ),
        "revoked/missing row deny": 'if not row or row["revoked"]:',
        "db-error fail closed": "return False",
    },
    "verify_api_access_token": {
        "cache-hit authoritative recheck": (
            "_api_token_verdict_still_authoritative(token, verdict)"
        ),
        "stale verdict eviction": ".invalidate_where(",
    },
}


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
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _committed_blob(commit: str, path: str) -> str:
    proc = _git("show", f"{commit}:{path}")
    assert proc.returncode == 0, (
        f"cannot read committed blob {path!r}: {proc.stderr.strip()}"
    )
    return proc.stdout


def _functions_in_blob(blob: str, path: str) -> dict[str, str]:
    tree = ast.parse(blob, filename=path)
    lines = blob.splitlines(keepends=True)
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = "".join(lines[node.lineno - 1 : node.end_lineno])
    return found


def _problems(functions: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for name, markers in REQUIRED_MARKERS.items():
        src = functions.get(name)
        if src is None:
            problems.append(f"MISSING FUNCTION in committed tree: {name}")
            continue
        for label, marker in markers.items():
            if marker not in src:
                problems.append(f"{name}: missing marker {label!r} ({marker!r})")
    return problems


def test_committed_tree_pins_cross_worker_fail_closed(commit_sha: str) -> None:
    """The committed api_token.py carries the fail-closed re-check markers."""
    functions = _functions_in_blob(_committed_blob(commit_sha, _API_TOKEN_PATH), _API_TOKEN_PATH)
    assert set(REQUIRED_MARKERS) <= functions.keys(), (
        "guard is vacuous: expected functions not found in committed blob"
    )
    problems = _problems(functions)
    assert not problems, "\n".join(problems)


def test_pins_are_load_bearing(commit_sha: str) -> None:
    """Self-test: removing each marker from the committed source is detected."""
    functions = _functions_in_blob(_committed_blob(commit_sha, _API_TOKEN_PATH), _API_TOKEN_PATH)
    probes = 0
    for name, markers in REQUIRED_MARKERS.items():
        src = functions[name]
        for label, marker in markers.items():
            assert marker in src, f"probe marker absent from committed {name}: {label}"
            weakened = dict(functions)
            weakened[name] = src.replace(marker, "NEUTRALIZED", 1)
            problems = _problems(weakened)
            assert any(name in p and label in p for p in problems), (
                f"removing marker {label!r} from {name} was not flagged"
            )
            probes += 1
    assert probes >= 6, f"too few marker probes ({probes}) - guard near-vacuous"
