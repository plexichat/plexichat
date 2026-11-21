"""Guard: production server code must not write debug prints to stdout.

Follow-up to ``8cdcddb0`` (2FA completion printed client IP and user-agent) and
``b6b730e8`` (guard scoped to ``src/core/auth/managers``). A bare ``print`` in a
request path bypasses structured logging and writes straight to the process
stdout, which gunicorn/uvicorn capture and log collectors ship off-box.

This guard widens the scan to the whole production ``src/`` tree, with two
classes of explicit, reviewed exceptions:

* console-oriented entry points (``src/cli/**``, ``src/core/migrations/cli.py``,
  ``src/server/lifecycle.py``) and test/self-test trees, where stdout is the
  intended user interface; and
* ``DOCUMENTED_PRINTS`` below: deliberate operator-console output, each with a
  reason. A new print in any other production module fails the build.

Like the auth-manager guard, blobs are read from COMMITTED HEAD
(``git show HEAD:<path>``), not the working tree. Several production files are
contested by parallel agents whose stale copies can still contain an already
removed print; scanning the committed tree avoids clobbering that work while
still catching a later whole-file commit that silently reintroduces it.

Self-tests prove the detector flags bare, nested, ``builtins.print`` and
``pprint`` calls, ignores logger calls and print-shaped strings, honours only
explicit exemptions, and that the exclusions cannot hide a request handler.
The committed production tree itself must have zero undocumented offenders.
"""

import ast
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

# Path components whose files are console-oriented by design. ``tests`` also
# covers ``src/core/tests``; ``selftest`` covers ``src/core/selftest``.
EXCLUDED_PATH_COMPONENTS = frozenset({"tests", "selftest", "cli"})

# CLI/startup entry points that live outside ``src/cli``.
EXCLUDED_FILES = frozenset(
    {
        "src/core/migrations/cli.py",
        "src/server/lifecycle.py",
    }
)

# (repo-relative path, exact print-call source, reason). Empty default: every
# other production module has structured logging available and no legitimate
# reason to write to stdout.
DOCUMENTED_PRINTS: tuple[tuple[str, str, str], ...] = (
    (
        "src/core/admin/auth/helpers.py",
        r"print('\n' + '=' * 60 + '\nPLEXICHAT FIRST BOOT: generated admin credentials' + '\n  Username: admin' + f'\n  Password: {password}' + '\n  Saved to ~/.plexichat/admin_credentials.txt (mode 0600)' + '\n  Set PLEXICHAT_NO_SAVE_ADMIN_CREDENTIALS=1 to disable disk write.' + '\n' + '=' * 60 + '\n', flush=True)",
        "First-boot bootstrap hands the generated admin password to the local "
        "operator console exactly once. Intentional operator UX, documented in "
        "findings.md H5 and asserted by test_admin_bootstrap_credentials.py; "
        "it is not on a request path. Console output is always emitted by "
        "design, even when the at-rest credential file is opted out.",
    ),
)


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


def _is_excluded(path: str) -> bool:
    parts = path.split("/")
    if any(part in EXCLUDED_PATH_COMPONENTS for part in parts[:-1]):
        return True
    return path in EXCLUDED_FILES


def _production_files(commit: str) -> tuple[str, ...]:
    proc = _git("ls-tree", "-r", "--name-only", commit, "--", "src")
    assert proc.returncode == 0, f"cannot list src: {proc.stderr.strip()}"
    files = tuple(
        p
        for p in proc.stdout.splitlines()
        if p.endswith(".py") and not _is_excluded(p)
    )
    assert files, "no production python modules found in the committed tree"
    return files


def _print_calls(tree: ast.AST) -> list[ast.Call]:
    """Bare ``print`` / ``pprint`` / ``builtins.print`` calls in a module."""
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

    def handle(msg):
        logger.info("auth manager entered")
        note = "print('this is only a string')"
        return note
    """
    bare = """
    def handle(msg):
        print("DBG logging middleware", repr(msg))
    """
    nested = """
    def outer():
        def inner():
            print("nested debug")
        return inner
    """
    builtins_print = """
    def handle():
        builtins.print("debug")
    """
    pprint_call = """
    def handle(payload):
        pprint(payload)
    """
    assert _print_calls(ast.parse(textwrap.dedent(clean))) == []
    for source in (bare, nested, builtins_print, pprint_call):
        assert len(_print_calls(ast.parse(textwrap.dedent(source)))) == 1, source


def test_self_test_documenting_a_print_is_required_to_allow_it():
    source = """
    def handle(msg):
        print('DBG', repr(msg))
    """
    files = ("src/api/middleware/_synthetic.py",)
    reader = lambda path: textwrap.dedent(source)  # noqa: E731 - synthetic reader
    assert _offenders(files, reader)  # undocumented: flagged
    documented = (
        ("src/api/middleware/_synthetic.py", "print('DBG', repr(msg))", "CLI entry point"),
    )
    assert _offenders(files, reader, documented) == []


def test_self_test_exclusions_cannot_hide_a_request_handler():
    candidates = (
        "src/tests/test_example.py",
        "src/core/tests/test_example.py",
        "src/core/selftest/_probe.py",
        "src/cli/main.py",
        "src/core/migrations/cli.py",
        "src/server/lifecycle.py",
        "src/api/middleware/logging.py",
        "src/core/auth/managers/twofactor.py",
    )
    scanned = tuple(p for p in candidates if not _is_excluded(p))
    assert scanned == (
        "src/api/middleware/logging.py",
        "src/core/auth/managers/twofactor.py",
    )


# ---------------------------------------------------------------------------
# Real committed tree
# ---------------------------------------------------------------------------


def test_scan_is_not_vacuous(commit_sha):
    files = _production_files(commit_sha)
    assert len(files) >= 1000, f"only {len(files)} production modules scanned"
    for required in (
        "src/api/middleware/logging.py",
        "src/core/admin/auth/helpers.py",
        "src/core/auth/managers/twofactor.py",
    ):
        assert required in files, f"{required} unexpectedly excluded from scan"
    # The reader really returns committed content: the middleware must still
    # contain its class even though local copies may be stale/contested.
    source = _make_blob_reader(commit_sha)("src/api/middleware/logging.py")
    assert "class LoggingMiddleware" in source
    assert ast.parse(source)


def test_no_bare_print_in_committed_production_src(commit_sha):
    offenders = _offenders(_production_files(commit_sha), _make_blob_reader(commit_sha))
    assert not offenders, (
        "Debug prints in production server code (stdout/PII leak):\n  "
        + "\n  ".join(sorted(offenders))
    )
