"""Static guard: direct outbound HTTP calls must carry an explicit timeout.

A socket connect with no timeout can hang a worker forever (slowloris-style
resource exhaustion, or a black-holed SSRF target). The SSRF gate decides
*where* we may connect; this module locks in the companion invariant that a
direct ``requests``/``httpx``/``urlopen`` call always bounds *how long* it
waits.

The check is AST-based, not a grep: string matching counts comments, dict keys
and ``dict.get`` calls. It enumerates call sites whose receiver is an imported
HTTP module (or an imported ``urlopen`` helper) and rejects any that omit the
``timeout=`` keyword. Client objects (``httpx.Client(...)``,
``requests.Session()``) configure their timeout at construction and are intentionally
out of scope here; the transport-level default lives in
``src.utils.outbound.urlopen`` and is asserted separately below.
"""

import ast
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

HTTP_MODULES = frozenset(
    {"httpx", "requests", "aiohttp", "urllib.request", "src.utils.outbound"}
)
HTTP_METHODS = frozenset(
    {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "request",
        "urlopen",
        "stream",
        "send",
    }
)


def _python_files(base: Path):
    for directory, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for filename in sorted(files):
            if filename.endswith(".py"):
                yield Path(directory) / filename


def _parse(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _aliases(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in HTTP_MODULES:
                    found.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module in HTTP_MODULES:
            for alias in node.names:
                found.add(alias.asname or alias.name)
    return found


def _root_name(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _transport():
    """Yield ``(relative_path, line_no, call)`` for direct HTTP calls."""
    for path in _python_files(SRC):
        if os.sep + "tests" in str(path):
            continue
        tree = _parse(path)
        if tree is None:
            continue
        aliases = _aliases(tree)
        if not aliases:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr not in HTTP_METHODS or _root_name(func) not in aliases:
                    continue
            elif isinstance(func, ast.Name):
                if func.id not in HTTP_METHODS or func.id not in aliases:
                    continue
            else:
                continue
            yield str(path.relative_to(ROOT)), node.lineno, node


def _has_timeout(call: ast.Call) -> bool:
    return any(keyword.arg == "timeout" for keyword in call.keywords)


def test_transport_scan_is_not_vacuous():
    calls = list(_transport())
    assert len(calls) >= 8, f"only found {len(calls)} direct HTTP call sites"
    # The hardened transcription provider is a known direct ``requests`` sink;
    # if it disappears from the scan the alias detection is broken.
    assert any("transcription/provider.py" in path for path, _, _ in calls), calls


def test_every_direct_http_call_has_a_timeout():
    offenders = [
        f"{path}:{line} {ast.unparse(call)[:80]}"
        for path, line, call in _transport()
        if not _has_timeout(call)
    ]
    assert not offenders, (
        "Direct outbound HTTP calls with no timeout (a hung socket can pin a "
        "worker forever):\n  " + "\n  ".join(offenders)
    )


def test_transport_floor_enforces_a_positive_default_timeout():
    """The shared urllib helper is the last line of defence for callers that
    forget to pass one; its default must stay a finite, positive number.
    """
    tree = _parse(ROOT / "src/utils/outbound.py")
    assert tree is not None
    defaults = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "urlopen"
        ):
            for arg, default in zip(
                node.args.args[-len(node.args.defaults) :], node.args.defaults
            ):
                if arg.arg == "timeout" and isinstance(default, ast.Constant):
                    defaults[arg.arg] = default.value
    assert "timeout" in defaults, "urlopen lost its default timeout"
    assert isinstance(defaults["timeout"], (int, float))
    assert 0 < defaults["timeout"] < 300, defaults
