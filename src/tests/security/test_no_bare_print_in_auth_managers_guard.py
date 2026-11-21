"""Guard: production auth managers must not write debug prints to stdout.

A bare ``print`` inside a manager bypasses structured logging and writes
straight to the process stdout, which is captured by gunicorn/uvicorn, shipped
by container log collectors and preserved in crash dumps. The 2FA completion
path briefly carried::

    print("DBG manager complete_2fa", repr(ip_address), repr(user_agent), flush=True)

which copied client IP and user-agent into every captured log line. It has no
diagnostic value the existing audit events do not already provide, so it was
removed outright rather than downgraded to a logger call.

The scan reads blobs from COMMITTED HEAD (``git show HEAD:<path>``), not the
working tree. Contested auth-manager files are repaired through private-index
commits, so a peer's stale working copy can still contain the old print; a
committed-tree scan both avoids clobbering that work and catches a later stale
whole-file commit that would silently reintroduce the artifact.

Self-tests prove the detector flags bare prints (including nested ones and
``builtins.print``) while ignoring logger calls and print-shaped strings, and
that documented exceptions are honoured. The committed tree itself must have
zero offenders.
"""

import ast
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]
SCAN_DIR = "src/core/auth/managers"

# (repo-relative path, exact print-call source, reason). Empty: every module
# under the auth managers package is server code with structured logging
# available, so there is no legitimate reason to write to stdout.
DOCUMENTED_PRINTS: tuple[tuple[str, str, str], ...] = ()


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
    """Resolve HEAD once per run and pin every blob read to that commit."""
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


def _committed_files(commit: str) -> tuple[str, ...]:
    proc = _git("ls-tree", "-r", "--name-only", commit, "--", SCAN_DIR)
    assert proc.returncode == 0, f"cannot list {SCAN_DIR}: {proc.stderr.strip()}"
    files = tuple(p for p in proc.stdout.splitlines() if p.endswith(".py"))
    assert files, f"no python files found under {SCAN_DIR} in {commit}"
    return files


def _print_calls(tree: ast.AST) -> list[ast.Call]:
    """Bare ``print`` / ``builtins.print`` / ``pprint`` calls in a module."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in {"print", "pprint"}:
            found.append(node)
        elif isinstance(func, ast.Attribute) and func.attr in {"print", "pprint"}:
            receiver = ast.unparse(func.value)
            if receiver in {"builtins", "pprint"}:
                found.append(node)
    return found


def _offenders(files, reader, documented=DOCUMENTED_PRINTS) -> list[str]:
    offenders = []
    for path in files:
        tree = ast.parse(reader(path), filename=path)
        for call in _print_calls(tree):
            source = ast.unparse(call)
            if any(p == path and text == source for p, text, _why in documented):
                continue
            offenders.append(f"{path}:{call.lineno}: {source}")
    return offenders


# ---------------------------------------------------------------------------
# Self-tests: a detector that never fires must not let the real scan pass
# ---------------------------------------------------------------------------


def test_self_test_detector_flags_prints_but_not_loggers_or_strings():
    clean = """
    import logging
    logger = logging.getLogger(__name__)

    def complete(ip_address):
        logger.debug("auth manager entered")
        note = "print('this is only a string')"
        return note
    """
    bare = """
    def complete(ip_address, user_agent):
        print("DBG manager complete_2fa", repr(ip_address), repr(user_agent), flush=True)
    """
    nested = """
    def outer():
        def inner():
            print("nested debug")
        return inner
    """
    builtins_print = """
    def complete():
        builtins.print("debug")
    """
    assert _print_calls(ast.parse(textwrap.dedent(clean))) == []
    for source in (bare, nested, builtins_print):
        assert len(_print_calls(ast.parse(textwrap.dedent(source)))) == 1, source


def test_self_test_documenting_a_print_is_required_to_allow_it():
    source = """
    def complete(code):
        print("DBG", repr(code))
    """
    files = ("src/core/auth/managers/_synthetic.py",)
    reader = lambda path: textwrap.dedent(source)  # noqa: E731 - synthetic reader
    assert _offenders(files, reader)  # undocumented: flagged
    documented = (("src/core/auth/managers/_synthetic.py", "print('DBG', repr(code))", "CLI entry point"),)
    assert _offenders(files, reader, documented) == []


# ---------------------------------------------------------------------------
# Real committed tree
# ---------------------------------------------------------------------------


def test_scan_is_not_vacuous(commit_sha):
    files = _committed_files(commit_sha)
    assert "src/core/auth/managers/twofactor.py" in files
    assert len(files) >= 5, f"only {len(files)} manager modules scanned"
    # The reader really returns committed content: the 2FA manager must still
    # contain its class even though the local working copy is stale/contested.
    source = _make_blob_reader(commit_sha)("src/core/auth/managers/twofactor.py")
    assert "class TwoFactorMixin" in source
    assert ast.parse(source)


def test_no_bare_print_in_committed_auth_managers(commit_sha):
    offenders = _offenders(_committed_files(commit_sha), _make_blob_reader(commit_sha))
    assert not offenders, (
        "Debug prints in production auth managers (stdout/PII leak):\n  "
        + "\n  ".join(sorted(offenders))
    )
