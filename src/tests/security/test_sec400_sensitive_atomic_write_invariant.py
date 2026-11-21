"""SEC400 invariant: security-sensitive file writers use mkstemp + os.replace.

Writing a credential, keyring, KEK, config, audit journal or export record via
``open(path, "w")`` follows symlinks, uses the process umask and can leave a
half-written file after a crash. The prior hardening replaced those writes with
a unique, private temp file (``tempfile.mkstemp`` -> O_CREAT|O_EXCL, 0600) in
the destination directory followed by an atomic ``os.replace``. This suite
locks in that pattern on every function that was fixed, so reverting any one
of them to a direct write fails the build.

The check is AST-only (no imports, no DB, no filesystem writes), and the
detector is validated against synthetic good/bad writers before the real tree
is trusted.
"""

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

# (relative source file, function name) of every writer that was hardened to
# mkstemp + atomic os.replace. These are the surfaces that hold secrets or
# privileged metadata and must never regress to open(path, "w").
ATOMIC_WRITE_SURFACES = (
    ("src/core/admin/auth/helpers.py", "_save_admin_credentials"),
    ("src/utils/common_utils/utils/config/core.py", "_save_config"),
    ("src/utils/encryption/core/keyring.py", "_write_atomic"),
    ("src/utils/encryption/vault/machine_key.py", "_persist_machine_key"),
    ("src/utils/encryption/kek_migration/migration.py", "_atomic_write_bytes"),
    ("src/utils/encryption/kek_migration/migration.py", "_save_migration_metadata"),
    ("src/core/selftest/artifact_journal.py", "_flush"),
    ("src/core/messaging/services/export.py", "_persist_record"),
)


def _root_name(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _is_mkstemp(call):
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "mkstemp"
        or isinstance(func, ast.Name)
        and func.id == "mkstemp"
    )


def _is_os_replace(call):
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "replace"
        and _root_name(func) == "os"
    ) or (isinstance(func, ast.Name) and func.id == "replace")


def _is_fdopen(call):
    func = call.func
    return isinstance(func, ast.Attribute) and func.attr == "fdopen"


def _direct_write_lines(function):
    """Lines performing a direct builtin open(..., 'w') or Path.write_*()."""
    lines = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Name)
            and func.id == "open"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and any(ch in node.args[1].value for ch in ("w", "a", "x"))
        ):
            lines.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr in (
            "write_text",
            "write_bytes",
        ):
            lines.append(node.lineno)
    return lines


def _own_body_nodes(root: ast.AST):
    """Yield ``root`` and nodes reachable through execution (not definition).

    Descend through statement-level nesting (if/for/with/try/...) but skip
    nested function and lambda bodies: *defining* a nested callable that uses
    mkstemp/fdopen/replace does not run it. Counting dead nested primitives as
    protection lets a writer silence this invariant with dead evidence (the
    same vacuity fixed across the SEC100/200/300 route guards).
    """
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)


def _check_function(function):
    uses_mkstemp = False
    uses_replace = False
    uses_fdopen = False
    for node in _own_body_nodes(function):
        if not isinstance(node, ast.Call):
            continue
        uses_mkstemp = uses_mkstemp or _is_mkstemp(node)
        uses_replace = uses_replace or _is_os_replace(node)
        uses_fdopen = uses_fdopen or _is_fdopen(node)
    direct = _direct_write_lines(function)
    reasons = []
    if not uses_mkstemp:
        reasons.append("no mkstemp temp creation")
    if not uses_replace:
        reasons.append("no atomic os.replace")
    if not uses_fdopen:
        reasons.append("no os.fdopen of the temp fd")
    if direct:
        reasons.append(f"direct write(s) at lines {direct}")
    return reasons


def _scan_tree():
    results = []
    for relative, name in ATOMIC_WRITE_SURFACES:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        function = _find_function(tree, name)
        results.append((relative, name, function))
    return results


_SCAN = _scan_tree()


def test_detector_distinguishes_atomic_writer_from_direct_write():
    """Self-test: a direct open(..., 'w') is flagged; mkstemp+replace is not."""
    bad = """
def _write(path, data):
    with open(path, "w") as fh:
        fh.write(data)
"""
    worse = """
def _write(path, data):
    Path(path).write_text(data)
"""
    good = """
def _write(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise
"""
    bad_fn = _find_function(ast.parse(bad), "_write")
    assert any("mkstemp" in r for r in _check_function(bad_fn))
    assert any("os.replace" in r for r in _check_function(bad_fn))
    assert any("direct write" in r for r in _check_function(bad_fn))

    worse_fn = _find_function(ast.parse(worse), "_write")
    assert any("direct write" in r for r in _check_function(worse_fn))

    assert _check_function(_find_function(ast.parse(good), "_write")) == []


def test_dead_nested_atomic_primitives_do_not_protect_writer():
    """mkstemp/fdopen/replace in a never-invoked nested callable is dead evidence.

    Regression: ``_check_function`` used ``ast.walk(function)``, which descends
    into nested functions and lambdas. Merely *defining* a helper/lambda that
    builds a temp file and atomically replaced it did not run it, so a writer
    reintroducing a direct truncate write could silence this invariant with
    dead evidence. Live in-body primitives (including behind statement-level
    nesting) must still satisfy the invariant.
    """
    dead_nested = """
def _write(path, data):
    def never_called():
        fd, tmp = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write(data)
        os.replace(tmp, path)
    with open(path, "w") as fh:
        fh.write(data)
"""
    dead_lambda = """
def _write(path, data):
    _ = lambda: tempfile.mkstemp()
    with open(path, "w") as fh:
        fh.write(data)
"""
    live_nested = """
def _write(path, data):
    for _ in (1,):
        fd, tmp = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write(data)
        os.replace(tmp, path)
"""
    for source in (dead_nested, dead_lambda):
        reasons = _check_function(_find_function(ast.parse(source), "_write"))
        assert any("mkstemp" in r for r in reasons), (
            "dead nested mkstemp must not protect the writer"
        )
    assert _check_function(_find_function(ast.parse(live_nested), "_write")) == []


def test_every_hardened_surface_is_found_and_scan_is_not_vacuous():
    missing = [f"{rel}:{name}" for rel, name, fn in _SCAN if fn is None]
    assert not missing, f"hardened writers dropped out of the scan: {missing}"
    assert len(_SCAN) >= 8, f"only {len(_SCAN)} hardened surfaces enumerated"
    # Prove the scan actually sees the hardened primitives (guards against a
    # detector that never fires and an all-empty scan passing trivially).
    seen_mkstemp = sum(
        1
        for _, _, fn in _SCAN
        if fn is not None
        and any(_is_mkstemp(n) for n in ast.walk(fn) if isinstance(n, ast.Call))
    )
    assert seen_mkstemp >= len(_SCAN)


def test_all_sensitive_writers_remain_atomic_and_private():
    offenders = []
    for relative, name, function in _SCAN:
        if function is None:
            offenders.append(f"{relative}:{name}: function missing")
            continue
        reasons = _check_function(function)
        if reasons:
            offenders.append(f"{relative}:{name}: {'; '.join(reasons)}")
    assert not offenders, (
        "Security-sensitive writers without mkstemp + atomic replace:\n  "
        + "\n  ".join(sorted(offenders))
    )
