"""Structural guard: every cookie-clearing site mirrors its issuance attributes.

Follow-up to the OAuth state-cookie clear fix (N655, ``e85eab23``). That commit
hardened a ``response.delete_cookie(...)`` which had been emitting a clear with
only ``Path``: because Starlette's ``delete_cookie`` defaults are
``secure=False`` / ``httponly=False``, the deletion ``Set-Cookie`` went out
*without* ``Secure`` or ``HttpOnly``. A browser that keeps Secure and
non-Secure variants of the same cookie name in separate stores then fails to
remove the real credential, and the unhardened auth-cookie header is itself a
hygiene bug.

The sweep behind this guard found exactly two production clear sites in the
committed tree, and both already carry the full attribute set:

* ``src/api/routes/auth/cookies.py`` — ``clear_session_cookie`` (logout / bulk
  revoke) mirrors ``set_session_cookie``: ``Path=/``, ``HttpOnly``,
  ``SameSite=Lax`` and ``secure=session_cookie_secure()``.
* ``src/api/routes/auth/oauth.py`` — ``_clear_oauth_state_cookie`` (failed and
  successful callbacks) mirrors the ``set_cookie`` at login-init via
  ``_oauth_cookie_params``: OAuth-scoped ``Path``, ``HttpOnly``,
  ``SameSite=Lax`` and ``secure=session_cookie_secure()``.

There is no session/logout, CSRF, admin or remember-me cookie clear outside
those helpers — the backend issues only the session cookie and the OAuth
state cookie, and both are cleared through the helpers above.

Runtime tests cover the two known helpers individually, but nothing stopped a
later edit from adding a *third* bare ``delete_cookie`` (or a manual
``set_cookie(..., max_age=0)``) that omits the protections. This is a pure AST
ratchet (no app import/config/DB) over every production module in the
committed tree. It fails closed when a clear omits any of ``path``,
``httponly``, ``secure`` or ``samesite``. Blobs are read from committed HEAD
(``git show HEAD:<path>``), not the working tree: many production files are
contested by parallel agents whose stale copies could mislead a working-tree
scan.

Non-vacuity is proven four ways: the sweep must still find both documented
clear sites; the HEAD blob reader must really return committed content; the
detector must flag a bare clear and a manual ``max_age=0`` clear; and it must
accept a fully hardened clear.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

# Committed-tree sweep: ~5.5k HEAD blobs per test (20-30s here, and slower on
# a shared runner), so the suite-wide per-test timeout is too tight.
pytestmark = [
    pytest.mark.security,
    pytest.mark.unit,
    pytest.mark.timeout(180),
]

ROOT = Path(__file__).resolve().parents[3]

# The four attributes a clear must carry to match issuance. Starlette's
# delete_cookie defaults (secure=False, httponly=False, path="/", samesite="lax")
# are *not* safe for auth cookies: Secure and HttpOnly must be explicit.
REQUIRED_CLEAR_KWARGS = frozenset({"path", "httponly", "secure", "samesite"})

DOCUMENTED_CLEAR_FILES = (
    "src/api/routes/auth/cookies.py",
    "src/api/routes/auth/oauth.py",
)


# ---------------------------------------------------------------------------
# Committed-tree plumbing (mirrors the other structural guards in this suite)
# ---------------------------------------------------------------------------


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
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


def _make_blob_reader(commit: str):
    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}")
        assert proc.returncode == 0, f"blob missing from {commit}: {path}"
        return proc.stdout

    return read


_EXCLUDED_COMPONENTS = frozenset({"tests", "selftest"})


def _is_excluded(path: str) -> bool:
    return any(part in _EXCLUDED_COMPONENTS for part in path.split("/")[:-1])


@pytest.fixture(scope="module")
def production_files(commit_sha: str) -> tuple[str, ...]:
    proc = _git("ls-tree", "-r", "--name-only", commit_sha, "--", "src")
    assert proc.returncode == 0, f"cannot list src: {proc.stderr.strip()}"
    files = tuple(
        p for p in proc.stdout.splitlines() if p.endswith(".py") and not _is_excluded(p)
    )
    assert files, "no production python modules found in the committed tree"
    return files


# ---------------------------------------------------------------------------
# AST detection of cookie clears
# ---------------------------------------------------------------------------


def _method_name(node: ast.Call) -> str:
    return node.func.attr if isinstance(node.func, ast.Attribute) else ""


def _is_zero_constant(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value == 0


class _ClearCollector(ast.NodeVisitor):
    """Collect cookie-clearing calls: ``delete_cookie`` and manual expiries."""

    def __init__(self) -> None:
        self.clears: list[tuple[ast.Call, str, set[str]]] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast API
        method = _method_name(node)
        kwargs = {kw.arg for kw in node.keywords if kw.arg is not None}
        if method == "delete_cookie":
            self.clears.append((node, "delete_cookie", kwargs))
        elif method == "set_cookie" and any(
            kw.arg in {"max_age", "expires"} and _is_zero_constant(kw.value)
            for kw in node.keywords
            if kw.arg is not None
        ):
            # A hand-rolled clear (set_cookie with Max-Age=0 / Expires=0) is
            # the same hazard and must satisfy the same attribute invariant.
            self.clears.append((node, "set_cookie(max_age=0)", kwargs))
        self.generic_visit(node)


def _collect(tree: ast.AST) -> list[tuple[ast.Call, str, set[str]]]:
    collector = _ClearCollector()
    collector.visit(tree)
    return collector.clears


def _unhardened(tree: ast.AST) -> list[tuple[int, str, list[str]]]:
    offenders: list[tuple[int, str, list[str]]] = []
    for node, kind, kwargs in _collect(tree):
        missing = sorted(REQUIRED_CLEAR_KWARGS - kwargs)
        if missing:
            offenders.append((node.lineno, kind, missing))
    return offenders


# ---------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------


def test_every_cookie_clear_carries_issuance_attributes(
    commit_sha: str, production_files: tuple[str, ...]
) -> None:
    """No production clear may omit Path/HttpOnly/Secure/SameSite."""
    read = _make_blob_reader(commit_sha)
    offenders: list[tuple[str, int, str, list[str]]] = []
    for path in production_files:
        tree = ast.parse(read(path), filename=path)
        for lineno, kind, missing in _unhardened(tree):
            offenders.append((path, lineno, kind, missing))
    assert offenders == [], (
        "cookie clear(s) missing issuance attributes — a bare clear can leave a "
        "Secure/non-Secure variant of the cookie in the browser and emits an "
        "unprotected auth-cookie Set-Cookie: "
        + "; ".join(
            f"{p}:{line} {kind} missing {', '.join(m)}"
            for p, line, kind, m in offenders
        )
    )


# ---------------------------------------------------------------------------
# Non-vacuity evidence
# ---------------------------------------------------------------------------


def test_sweep_still_sees_both_documented_clear_sites(
    commit_sha: str,
) -> None:
    """The scanner must actually reach both known clears, not scan nothing."""
    read = _make_blob_reader(commit_sha)
    found: set[tuple[str, str]] = set()
    for path in DOCUMENTED_CLEAR_FILES:
        for _node, kind, _kwargs in _collect(ast.parse(read(path), filename=path)):
            if kind == "delete_cookie":
                found.add((path, kind))
    assert found == {(p, "delete_cookie") for p in DOCUMENTED_CLEAR_FILES}, (
        f"expected both documented delete_cookie clears, saw {sorted(found)}"
    )


def test_head_reader_returns_committed_blob(commit_sha: str) -> None:
    """The reader must read HEAD blobs, not an empty/stale string."""
    read = _make_blob_reader(commit_sha)
    source = read("src/api/routes/auth/cookies.py")
    assert "def clear_session_cookie" in source
    assert "delete_cookie(" in source


def test_detector_flags_bare_and_manual_clears() -> None:
    """A bare delete_cookie and a manual max_age=0 clear are both rejected."""
    bare = "def clear(resp):\n    resp.delete_cookie('k', path='/')\n"
    manual = "def clear(resp):\n    resp.set_cookie('k', '', max_age=0, path='/')\n"
    for source in (bare, manual):
        offenders = _unhardened(ast.parse(source))
        assert offenders, f"detector missed a clear in: {source!r}"
        assert offenders[0][2] == ["httponly", "samesite", "secure"]


def test_detector_accepts_fully_hardened_clear() -> None:
    """A clear mirroring all four issuance attributes passes the ratchet."""
    hardened = (
        "def clear(resp):\n"
        "    resp.delete_cookie('k', path='/', httponly=True, secure=True, "
        "samesite='lax')\n"
    )
    assert _unhardened(ast.parse(hardened)) == []
