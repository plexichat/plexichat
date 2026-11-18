"""SEC300 ratchet: URL-bearing modules use the vetted transport or timeouts.

A socket connect with no deadline can pin a worker forever (a slowloris peer or
a black-holed SSRF target). This suite is an independent, source-level companion
to the direct-call timeout guard: it scopes the check to modules in ``src/``
that actually embed an outbound ``http(s)://`` URL literal, then locks in the
strengthening invariant that every HTTP sink in such a module is either:

* the vetted ``src.utils.outbound.urlopen`` transport (which enforces the
  scheme allowlist and a positive default timeout), or
* a direct call that passes an explicit ``timeout=`` keyword.

All discovery is AST-based over source files, so no app import, config or
database is needed. Self-tests below exercise a non-vetted call with and
without a timeout and the vetted transport before the scan is trusted.
"""

import ast
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

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
        "open",
    }
)
VETTED_MODULE = "src.utils.outbound"


def _is_url_literal(node):
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(("http://", "https://"))
    )


def _python_files(base: Path):
    for directory, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for filename in sorted(files):
            if filename.endswith(".py"):
                path = Path(directory) / filename
                if os.sep + "tests" + os.sep in str(path):
                    continue
                yield path


def _parse(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _import_origins(tree):
    """Map local name -> (module, imported symbol or None)."""
    origins = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in HTTP_MODULES or alias.name.startswith(VETTED_MODULE):
                    local = alias.asname or alias.name.split(".")[0]
                    origins[local] = (alias.name, None)
        elif isinstance(node, ast.ImportFrom) and node.module in HTTP_MODULES:
            for alias in node.names:
                origins[alias.asname or alias.name] = (node.module, alias.name)
    return origins


def _root_name(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _network_calls(tree):
    """Yield ``(lineno, call, vetted, has_timeout, literal_url)`` for HTTP sinks."""
    origins = _import_origins(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            attr = func.attr
            if attr not in HTTP_METHODS:
                continue
            origin = origins.get(_root_name(func))
            if origin is None:
                continue
            module, _ = origin
        elif isinstance(func, ast.Name):
            origin = origins.get(func.id)
            if origin is None:
                continue
            module, imported = origin
            attr = imported or func.id
            if attr not in HTTP_METHODS:
                continue
        else:
            continue

        vetted = module == VETTED_MODULE and attr == "urlopen"
        has_timeout = any(keyword.arg == "timeout" for keyword in node.keywords)
        literal_url = any(_is_url_literal(arg) for arg in node.args) or any(
            keyword.arg in ("url", "urlopen") and _is_url_literal(keyword.value)
            for keyword in node.keywords
        )
        yield node.lineno, node, vetted, has_timeout, literal_url


def _scan_source(source):
    """Return ``(has_url_literal, calls, violations)`` for one module's source."""
    tree = ast.parse(source)
    has_url_literal = any(_is_url_literal(node) for node in ast.walk(tree))
    calls = []
    violations = []
    for lineno, call, vetted, has_timeout, literal_url in _network_calls(tree):
        record = (lineno, call, vetted, has_timeout, literal_url)
        calls.append(record)
        if not vetted and not has_timeout:
            summary = ast.unparse(call)[:90]
            violations.append(f"line {lineno}: {summary}")
    return has_url_literal, calls, violations


def _scan_tree():
    """Scan every URL-bearing, non-test module under ``src/``."""
    scanned = []
    for path in _python_files(SRC):
        tree = _parse(path)
        if tree is None:
            continue
        source = path.read_text(encoding="utf-8")
        if not any(_is_url_literal(node) for node in ast.walk(tree)):
            continue
        has_url_literal, calls, violations = _scan_source(source)
        scanned.append(
            (str(path.relative_to(ROOT)), has_url_literal, calls, violations)
        )
    return scanned


_SCAN = _scan_tree()


def test_detector_distinguishes_vetted_and_raw_url_calls():
    """Self-test: raw URL call fails, timeout and vetted transport pass."""
    raw = "import requests\nrequests.get('https://example.com/x')\n"
    timeout = "import requests\nrequests.get('https://example.com/x', timeout=5)\n"
    vetted = (
        "from src.utils.outbound import urlopen\nurlopen('https://example.com/x')\n"
    )
    unscoped = "import requests\nrequests.get(ENDPOINT)\n"

    assert _scan_source(raw)[2], "raw URL call with no deadline was not flagged"
    assert not _scan_source(timeout)[2], "explicit timeout call was flagged"
    has_url, calls, violations = _scan_source(vetted)
    assert has_url and len(calls) == 1 and calls[0][2] is True
    assert not violations, "vetted transport call was flagged"
    # Gating: a module with no URL literal is out of this suite's scope.
    assert _scan_source(unscoped)[0] is False


def test_scan_is_not_vacuous():
    modules = [rel for rel, _, _, _ in _SCAN]
    assert len(modules) >= 30, f"only {len(modules)} URL-bearing modules found"
    calls = [call for _, _, module_calls, _ in _SCAN for call in module_calls]
    assert len(calls) >= 10, f"only {len(calls)} HTTP sink calls found"
    assert any(call[2] for call in calls), "no vetted urlopen call discovered"
    assert any(not call[2] and call[3] for call in calls), (
        "no direct call with an explicit timeout discovered"
    )
    assert any(
        "transcription/provider.py" in rel
        for rel, _, module_calls, _ in _SCAN
        if module_calls
    ), "known direct-requests sink dropped out of the scan"


def test_every_url_bearing_module_uses_vetted_transport_or_timeout():
    offenders = [
        f"{rel}:{violation}"
        for rel, _, _, violations in _SCAN
        for violation in violations
    ]
    assert not offenders, (
        "HTTP calls in URL-bearing modules with neither the vetted transport "
        "nor an explicit timeout:\n  " + "\n  ".join(sorted(offenders))
    )


def test_every_scanned_module_really_has_a_url_literal():
    assert all(has_url for _, has_url, _, _ in _SCAN)
