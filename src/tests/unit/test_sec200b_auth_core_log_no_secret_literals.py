"""SEC200b invariant: auth/core log format strings carry no secret values.

A credential that reaches a log line is copied verbatim into files shipped
off-box and read by a wider audience than the code that emitted it. The two
easy ways to reintroduce that bug are:

* a format string that names a secret *value* next to a marker
  (``logger.info("token=%s", token)`` / ``"... secret: {value}"``);
* an f-string that interpolates a secret-bearing expression
  (``logger.debug(f"refresh secret={secret[:8]}")``).

This is a static (AST) scan over a fixed sample of security-critical core
auth modules -- a different surface from the scrubbing/webhook sample covered
elsewhere. The sample is named, not glob-discovered, so a module cannot silently
opt out. The detector deliberately targets a secret *value* shape rather than
the mere word "password"/"token", so ordinary messages such as "password change
failed" are not false positives.

A detector self-test proves the scan flags both leak shapes while leaving a
benign message alone.
"""

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[3]

# Fixed sample of core auth modules that emit log lines. Adding a module here
# is the ratchet; removing one to dodge the check drops the non-vacuity guard
# below.
SAMPLE_MODULES = (
    "src/core/auth/deletion_log.py",
    "src/core/auth/managers/api_token.py",
    "src/core/auth/managers/base.py",
    "src/core/auth/managers/deletion.py",
    "src/core/auth/managers/ip_blacklist.py",
    "src/core/auth/managers/password.py",
    "src/core/auth/managers/session/lifecycle.py",
    "src/core/auth/managers/session/login.py",
    "src/core/auth/managers/twofactor.py",
    "src/core/auth/oauth/pkce.py",
    "src/core/auth/oauth/state.py",
    "src/core/auth/reaper.py",
    "src/core/auth/totp.py",
    "src/core/admin/auth/helpers.py",
    "src/core/admin/auth/password_mixin.py",
)

SECRET_WORDS = (
    "token",
    "password",
    "passwd",
    "secret",
    "credential",
    "passphrase",
    "api_key",
    "apikey",
    "private_key",
)

# A secret word immediately followed by a value marker: "token=%s",
# "password: {}", "secret -> x". Plain prose like "token for frozen user"
# does not match.
_SECRET_KV = re.compile(
    r"\b(?:" + "|".join(SECRET_WORDS) + r")\b\s*[:=]\s*(?:%|[{$\w])",
    re.IGNORECASE,
)
# An interpolated expression whose identifier is a secret name or ends in one.
_SECRET_EXPR = re.compile(
    r"(?:^|[^A-Za-z])(?:" + "|".join(SECRET_WORDS) + r")(?:$|[^A-Za-z])",
    re.IGNORECASE,
)

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


def _scan_tree(tree):
    """Return ``(leaks, log_call_count)`` from an already-parsed module tree."""
    leaks, calls = [], 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_logger_call(node)):
            continue
        parts = _message_parts(node)
        if parts is None:
            continue
        calls += 1
        literal, sources = parts
        match = _SECRET_KV.search(literal)
        if match:
            leaks.append((node.lineno, "literal", match.group(0)[:40]))
        for expression in sources:
            if _SECRET_EXPR.search(expression):
                leaks.append((node.lineno, "interpolation", expression[:70]))
    return leaks, calls


def _scan(source):
    """Scan one source string. Used only by the in-memory self-test below.

    Real sample modules never go through this path: they are read and parsed
    once by :func:`_load_sample` and handed to ``_scan_tree`` as trees, so a
    synthetic snippet is the only thing that pays for its own parse.
    """
    return _scan_tree(ast.parse(source))


def _load_sample():
    """Read and AST-parse every sample module exactly once.

    Returns ``((relative, source, tree), ...)`` shared by the parametrized
    completeness test and the non-vacuity test. Previously each sample was read
    and parsed twice: once by the parametrized test and again by
    ``test_sample_is_not_vacuous``, which re-read and re-scanned all 15 modules
    just to count log calls. Every detector now reuses these shared trees.
    """
    samples = []
    for relative in SAMPLE_MODULES:
        path = ROOT / relative
        assert path.is_file(), f"sample module disappeared: {relative}"
        source = path.read_text(encoding="utf-8")
        samples.append((relative, source, ast.parse(source)))
    return tuple(samples)


_SAMPLES = _load_sample()


def test_detector_flags_both_leak_shapes_and_spares_benign_text():
    """Self-test: both leak shapes must be caught, plain prose must not."""
    snippet = (
        "logger.info('token=%s accepted')\n"
        "logger.debug('session established, password: %s')\n"
        "logger.debug(f'refresh secret={secret[:8]}')\n"
        "logger.info(f'Bearer {access_token}')\n"
        "logger.info('password change for user %s failed')\n"
        "logger.warning('token count update skipped')\n"
    )
    leaks, calls = _scan(snippet)
    kinds = {kind for _, kind, _ in leaks}
    assert calls == 6, calls
    assert {"literal", "interpolation"} <= kinds, leaks
    assert len(leaks) == 4, leaks
    benign_lines = {line for line, _, _ in leaks}
    assert 5 not in benign_lines and 6 not in benign_lines, leaks


@pytest.mark.parametrize(
    "relative,source,tree", _SAMPLES, ids=[relative for relative, _, _ in _SAMPLES]
)
def test_no_secret_values_in_auth_core_log_format_strings(relative, source, tree):
    leaks, _ = _scan_tree(tree)
    assert not leaks, f"{relative} logs secret material: {leaks}"


def test_sample_is_not_vacuous():
    """The fixed sample must exercise a meaningful number of log calls."""
    total = sum(_scan_tree(tree)[1] for _, _, tree in _SAMPLES)
    assert total >= 80, f"expected a meaningful sample, found {total} log calls"


def test_sample_modules_are_read_and_parsed_once(monkeypatch):
    """Perf regression: every sample module is read and AST-parsed once.

    The detector used to be a source-string parser (``_scan(source)``) called
    from both the per-module parametrized test and the non-vacuity test, so all
    15 sample modules were read and parsed twice for no benefit. Counting the
    reads/parses pins the fix deterministically -- no wall-clock timing and no
    flaky threshold. The detector and all real-tree callers must reuse the
    pre-built trees: no extra I/O however many assertions call them.
    """
    reads = []
    parses = []
    real_read_text = Path.read_text
    real_parse = ast.parse

    def counting_read_text(self, *args, **kwargs):
        reads.append(self)
        return real_read_text(self, *args, **kwargs)

    def counting_parse(source, *args, **kwargs):
        parses.append(source)
        return real_parse(source, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    monkeypatch.setattr(ast, "parse", counting_parse)

    samples = _load_sample()

    assert len(samples) == len(SAMPLE_MODULES)
    assert reads == [ROOT / rel for rel in SAMPLE_MODULES]
    assert parses == [source for _, source, _ in samples]

    # Every real-tree caller reuses the shared trees; it neither re-reads a
    # module nor re-parses an already-parsed source.
    for relative, source, tree in samples:
        leaks, calls = _scan_tree(tree)
        assert isinstance(leaks, list) and calls >= 0
    assert reads == [ROOT / rel for rel in SAMPLE_MODULES]
    assert parses == [source for _, source, _ in samples]
