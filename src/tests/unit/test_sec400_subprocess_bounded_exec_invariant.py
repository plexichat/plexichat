"""SEC400 invariant: subprocess use in src/ is shell-free and time-bounded.

Two distinct failure modes are locked down here:

* shell invocation (``shell=True``, ``os.system``, ``os.popen``) turns any
  argument into an injection sink;
* a missing wall-clock ``timeout=`` lets a malformed input pin a worker forever
  (a hung ffmpeg/ffprobe/icacls child), which is a resource-exhaustion vector.

This complements the SEC100 CI guard (which is about the guard script itself)
with a source-level ratchet over the real ``src/`` tree. The only call without
a timeout is a fixed, non-network Windows ACL invocation, captured below with
a concrete justification; any new untimed child process fails the build.

Detection is AST-based and only parses files whose text mentions a process
sink, so the scan is fast and deterministic. Synthetic good/bad sources are
checked before the real tree is trusted.
"""

import ast
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

SUBPROCESS_FUNCS = frozenset(
    {
        "run",
        "call",
        "Popen",
        "check_output",
        "check_call",
        "getoutput",
        "getstatusoutput",
    }
)

# (relative source file, enclosing function) -> justification for the sole
# exception to the "every child has a deadline" rule. Keep this list empty of
# user-controlled inputs and non-fixed argv.
ALLOWED_NO_TIMEOUT = {
    (
        "src/utils/encryption/vault/machine_key.py",
        "_restrict_machine_key_permissions",
    ): "fixed local icacls argv on Windows; no user input and no network wait",
}


def _root_name(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _import_origins(tree):
    """Map local name -> ('subprocess', imported symbol or None)."""
    origins = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    origins[alias.asname or "subprocess"] = ("subprocess", None)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                origins[alias.asname or alias.name] = ("subprocess", alias.name)
    return origins


def _enclosing_function_map(tree):
    """Map every node to the name of its nearest enclosing function."""
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    result = {}
    for node in ast.walk(tree):
        current = parent.get(node)
        name = "<module>"
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = current.name
                break
            current = parent.get(current)
        result[node] = name
    return result


def _shell_is_truthy(call):
    """True for shell=True, shell=1 or a non-constant shell expression."""
    for keyword in call.keywords:
        if keyword.arg == "shell":
            value = keyword.value
            if isinstance(value, ast.Constant):
                return bool(value.value)
            return True
    return False


def _scan_source(source, relative):
    """Return process-launch records from one module's source."""
    tree = ast.parse(source)
    origins = _import_origins(tree)
    enclosing = _enclosing_function_map(tree)
    records = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        kind = None
        if isinstance(func, ast.Attribute) and func.attr in SUBPROCESS_FUNCS:
            if _root_name(func) in origins:
                kind = func.attr
        elif isinstance(func, ast.Name) and func.id in origins:
            imported = origins[func.id][1] or func.id
            if imported in SUBPROCESS_FUNCS:
                kind = imported
        if kind is not None:
            records.append(
                {
                    "relative": relative,
                    "lineno": node.lineno,
                    "function": enclosing[node],
                    "kind": kind,
                    "shell": _shell_is_truthy(node),
                    "timeout": any(k.arg == "timeout" for k in node.keywords),
                }
            )
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in ("system", "popen")
            and _root_name(func) == "os"
        ):
            records.append(
                {
                    "relative": relative,
                    "lineno": node.lineno,
                    "function": enclosing[node],
                    "kind": "os." + func.attr,
                    "shell": True,
                    "timeout": False,
                }
            )
    return records


def _violation(record):
    reasons = []
    if record["shell"]:
        reasons.append(f"shell invocation via {record['kind']}")
    if record["kind"].startswith("os."):
        reasons.append(f"shell invocation via {record['kind']}")
    if not record["timeout"]:
        key = (record["relative"], record["function"])
        if key not in ALLOWED_NO_TIMEOUT:
            reasons.append(f"{record['kind']} without timeout")
    return reasons


def _scan_tree():
    records = []
    for directory, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for filename in sorted(files):
            if not filename.endswith(".py"):
                continue
            path = Path(directory) / filename
            if os.sep + "tests" + os.sep in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            if not any(
                token in text for token in ("subprocess", "os.system", "os.popen")
            ):
                continue
            try:
                records.extend(_scan_source(text, str(path.relative_to(ROOT))))
            except SyntaxError:
                continue
    return records


_RECORDS = _scan_tree()


def test_detector_flags_shell_untimed_and_os_system_but_accepts_bounded():
    """Self-test: prove the detector catches each regression shape."""
    shell = _scan_source("import subprocess\nsubprocess.run(cmd, shell=True)\n", "x.py")
    assert len(shell) == 1 and _violation(shell[0])

    untimed = _scan_source(
        "import subprocess\ndef f(cmd):\n    return subprocess.run(cmd)\n", "x.py"
    )
    assert len(untimed) == 1 and _violation(untimed[0])

    system = _scan_source("import os\ndef f(cmd):\n    return os.system(cmd)\n", "x.py")
    assert len(system) == 1 and _violation(system[0])

    timed = _scan_source(
        "import subprocess\ndef f(cmd):\n    return subprocess.run(cmd, timeout=5)\n",
        "x.py",
    )
    assert len(timed) == 1 and not _violation(timed[0])

    # The documented exception is only exempt at its exact (file, function).
    allowed = _scan_source(
        "import subprocess\n"
        "def _restrict_machine_key_permissions(cmd):\n"
        "    return subprocess.run(cmd, check=False)\n",
        "src/utils/encryption/vault/machine_key.py",
    )
    assert len(allowed) == 1 and not _violation(allowed[0])
    elsewhere = _scan_source(
        "import subprocess\n"
        "def other(cmd):\n"
        "    return subprocess.run(cmd, check=False)\n",
        "src/utils/encryption/vault/machine_key.py",
    )
    assert _violation(elsewhere[0])


def test_scan_is_not_vacuous_and_allowlist_is_live():
    assert len(_RECORDS) >= 7, f"only {len(_RECORDS)} process launches discovered"
    assert any(r["timeout"] for r in _RECORDS), "no time-bounded child discovered"
    assert not any(r["shell"] for r in _RECORDS), "a shell invocation exists in src/"
    justified = [
        r
        for r in _RECORDS
        if not r["timeout"] and (r["relative"], r["function"]) in ALLOWED_NO_TIMEOUT
    ]
    assert len(justified) == 1, "allowlisted untimed call dropped out of the scan"
    assert ALLOWED_NO_TIMEOUT[
        (justified[0]["relative"], justified[0]["function"])
    ].strip()


def test_every_child_process_is_shell_free_and_time_bounded():
    offenders = [
        f"{r['relative']}:{r['lineno']} in {r['function']}: {'; '.join(reasons)}"
        for r in _RECORDS
        for reasons in [_violation(r)]
        if reasons
    ]
    assert not offenders, (
        "Process launches without shell=False and a deadline:\n  "
        + "\n  ".join(sorted(offenders))
    )
