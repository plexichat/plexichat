"""SEC150 invariant: no secret material is embedded in core log format strings.

A log line is copied verbatim into files that are shipped off-box and read by a
wider audience than the code that produced it. Two ways to leak a credential
through a logger call are easy to reintroduce by accident:

1. a literal format string that names/logs a credential (``"... token=..."``);
2. an f-string that interpolates a secret-bearing expression
   (``logger.debug(f"secret={secret[:8]}")``) -- the exact shape of a regression
   this suite guards against.

The check is static (AST) over a fixed sample of security-critical core modules,
so it fires on the code that would ship rather than on whatever a mocked logger
happened to record. The sample is deliberately named rather than glob-discovered
so a module cannot silently opt out.

A detector self-test proves the scan is not vacuous: it must flag a synthetic
snippet containing both leak shapes.
"""

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[3]

# Fixed sample: security/scrubbing/webhook modules that already pass. Adding a
# module here is the ratchet; removing one to dodge the check is caught by the
# coverage guard below.
SAMPLE_MODULES = (
    "src/core/security/outbound.py",
    "src/core/telemetry/scrubbing.py",
    "src/core/webhooks/manager/signature.py",
    "src/core/webhooks/url_safety.py",
    "src/core/webhooks/manager/validation.py",
    "src/core/webhooks/retry.py",
)

SECRET_WORDS = ("token", "password", "secret")
LOG_METHODS = frozenset(
    {
        "debug",
        "info",
        "warning",
        "warn",
        "error",
        "critical",
        "exception",
        "log",
        "fatal",
    }
)
LOGGER_NAMES = frozenset(
    {"logger", "log", "LOG", "_logger", "log_", "audit_logger", "_log"}
)


def _is_logger_call(node):
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr in LOG_METHODS):
        return False
    base = func.value
    if isinstance(base, ast.Name):
        return base.id in LOGGER_NAMES
    if isinstance(base, ast.Attribute):
        return base.attr in {"logger", "log"} or (
            isinstance(base.value, ast.Name) and base.value.id == "self"
        )
    return False


def _message_parts(node):
    """Return ``(literal_text, interpolated_sources)`` of a log call's message."""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value, []
    if isinstance(arg, ast.JoinedStr):
        literal, sources = [], []
        for chunk in arg.values:
            if isinstance(chunk, ast.Constant) and isinstance(chunk.value, str):
                literal.append(chunk.value)
            else:
                sources.append(ast.unparse(chunk))
        return "".join(literal), sources
    return None


def _find_leaks(source):
    """Yield ``(lineno, kind, evidence)`` leaks in a source string."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_logger_call(node):
            continue
        parts = _message_parts(node)
        if parts is None:
            continue
        literal, sources = parts
        if any(word in literal.lower() for word in SECRET_WORDS):
            yield node.lineno, "literal", literal.strip()[:80]
        for expression in sources:
            if any(word in expression.lower() for word in SECRET_WORDS):
                yield node.lineno, "interpolation", expression[:80]


def _module_sources():
    sources = {}
    for relative in SAMPLE_MODULES:
        path = ROOT / relative
        assert path.is_file(), f"sample module disappeared: {relative}"
        sources[relative] = path.read_text(encoding="utf-8")
    return sources


def test_detector_flags_both_leak_shapes():
    """Self-test: a scan that cannot flag a known leak is worthless."""
    snippet = (
        "logger.info('Auth token accepted for %s')\n"
        "logger.debug('session established, password_len=%d')\n"
        "logger.debug(f'refresh secret={secret[:8]}')\n"
        "logger.info(f'Bearer {access_token}')\n"
    )
    leaks = list(_find_leaks(snippet))
    kinds = {kind for _, kind, _ in leaks}
    assert {"literal", "interpolation"} <= kinds, leaks
    assert len(leaks) >= 4, leaks


@pytest.mark.parametrize("relative", SAMPLE_MODULES)
def test_no_secret_terms_in_log_format_strings(relative):
    source = (ROOT / relative).read_text(encoding="utf-8")
    leaks = list(_find_leaks(source))
    assert not leaks, f"{relative} logs secret material: {leaks}"


def test_sample_is_not_vacuous():
    """The sample must actually exercise logger calls to be meaningful."""
    tree_calls = 0
    for source in _module_sources().values():
        tree = ast.parse(source)
        tree_calls += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _is_logger_call(node)
            and _message_parts(node)
        )
    assert tree_calls >= 20, (
        f"expected a meaningful sample, found {tree_calls} log calls"
    )
