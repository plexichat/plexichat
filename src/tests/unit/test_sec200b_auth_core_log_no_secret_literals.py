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


def _scan(source):
    """Return ``(leaks, log_call_count)`` for a source string."""
    tree = ast.parse(source)
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


@pytest.mark.parametrize("relative", SAMPLE_MODULES)
def test_no_secret_values_in_auth_core_log_format_strings(relative):
    source = (ROOT / relative).read_text(encoding="utf-8")
    leaks, _ = _scan(source)
    assert not leaks, f"{relative} logs secret material: {leaks}"


def test_sample_is_not_vacuous():
    """The fixed sample must exercise a meaningful number of log calls."""
    total = 0
    for relative in SAMPLE_MODULES:
        path = ROOT / relative
        assert path.is_file(), f"sample module disappeared: {relative}"
        total += _scan(path.read_text(encoding="utf-8"))[1]
    assert total >= 80, f"expected a meaningful sample, found {total} log calls"
