"""SEC300 invariant: fresh core sample logs no secret literals.

A log line is copied verbatim into files shipped off-box and read by a wider
audience than the code that emitted it. This suite locks a third, *fresh* slice
of the core tree (DSAR/erasure, media/storage, artifacts, webhook delivery,
voice signaling, TLS, settings, migrations, search, database, messaging and
antispam) against two regressions:

* a log format string that embeds a secret value or a ``secret=...`` marker;
* an f-string interpolation of a secret-bearing expression
  (``logger.debug(f"secret={secret[:8]}")``).

The sample is named rather than glob-discovered so a module cannot silently opt
out, and it deliberately does not overlap the scrubbing/webhook sample (SEC150)
or the auth-core sample (SEC200b). The detector is a source-level AST scan, so
it checks the code that would ship rather than whatever a mocked logger
happened to record. A detector self-test proves the scan is not vacuous.
"""

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[3]

# Fresh sample: security-sensitive core modules not covered by the SEC150
# (scrubbing/webhook) or SEC200b (auth-core) log suites. Adding a module is the
# ratchet; removing one drops the non-vacuity guard below.
SAMPLE_MODULES = (
    "src/core/dsar/harvester.py",
    "src/core/dsar/audit_log.py",
    "src/core/erasure.py",
    "src/core/artifacts/recording.py",
    "src/core/artifacts/federation.py",
    "src/core/media/storage/s3.py",
    "src/core/media/storage/encrypted.py",
    "src/core/webhooks/delivery.py",
    "src/core/voice/signaling/sfu/mediasoup.py",
    "src/core/tls/__init__.py",
    "src/core/settings/manager.py",
    "src/core/migrations/locking.py",
    "src/core/search/indexer/elasticsearch.py",
    "src/core/database/monitoring.py",
    "src/core/messaging/services/message/edit_delete.py",
    "src/core/antispam/detector.py",
)

# Modules already claimed by the earlier log suites; the fresh sample must not
# overlap them.
PRIOR_SAMPLE_MODULES = frozenset(
    {
        "src/core/security/outbound.py",
        "src/core/telemetry/scrubbing.py",
        "src/core/webhooks/manager/signature.py",
        "src/core/webhooks/url_safety.py",
        "src/core/webhooks/manager/validation.py",
        "src/core/webhooks/retry.py",
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
    }
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
# "password: {}", "secret -> x". Plain prose such as "password change failed"
# or "token count updated" does not match.
_SECRET_KV = re.compile(
    r"\b(?:" + "|".join(SECRET_WORDS) + r")\b\s*(?::|=|->)\s*(?:%|[{$\w])",
    re.IGNORECASE,
)
# Well-known credential prefixes that should never appear as literal text.
_SECRET_VALUE = re.compile(
    r"\b(?:sk_live_|sk_test_|rk_live_|gh[pousr]_|AKIA[0-9A-Z]{8,}|"
    r"xox[baprs]-|eyJ[A-Za-z0-9_-]{8,})",
    re.IGNORECASE,
)
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
    if not isinstance(node, ast.Call):
        return False
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
    """Return ``(literal_text, interpolated_sources)`` for a log call."""
    if not node.args:
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
    """Return ``(leaks, log_call_count)`` for a source string.

    Each leak is ``(lineno, kind, evidence)`` where kind is ``literal`` or
    ``interpolation``.
    """
    tree = ast.parse(source)
    leaks, calls = [], 0
    for node in ast.walk(tree):
        if not _is_logger_call(node):
            continue
        parts = _message_parts(node)
        if parts is None:
            continue
        calls += 1
        literal, sources = parts
        match = _SECRET_KV.search(literal) or _SECRET_VALUE.search(literal)
        if match:
            leaks.append((node.lineno, "literal", match.group(0)[:40]))
        for expression in sources:
            if _SECRET_EXPR.search(expression) or _SECRET_VALUE.search(expression):
                leaks.append((node.lineno, "interpolation", expression[:70]))
    return leaks, calls


def test_detector_flags_secret_literals_and_spares_benign_text():
    """Self-test: both leak shapes must fire, benign prose must not."""
    snippet = (
        "logger.info('token=%s accepted')\n"  # 1 literal KV
        "logger.debug('password: %s')\n"  # 2 literal KV
        "logger.error('key: sk_live_abc123')\n"  # 3 literal value
        "logger.debug(f'secret={secret[:8]}')\n"  # 4 interpolation
        "logger.info('password change failed')\n"  # 5 benign
        "logger.warning('token count updated')\n"  # 6 benign
    )
    leaks, calls = _scan(snippet)
    assert calls == 6, calls
    assert {line for line, _, _ in leaks} == {1, 2, 3, 4}, leaks
    assert {kind for _, kind, _ in leaks} == {"literal", "interpolation"}, leaks


def test_fresh_sample_does_not_overlap_prior_log_suites():
    assert len(SAMPLE_MODULES) == len(set(SAMPLE_MODULES))
    assert not (set(SAMPLE_MODULES) & PRIOR_SAMPLE_MODULES)


@pytest.mark.parametrize("relative", SAMPLE_MODULES)
def test_no_secret_literals_in_fresh_core_log_messages(relative):
    path = ROOT / relative
    assert path.is_file(), f"sample module disappeared: {relative}"
    leaks, _ = _scan(path.read_text(encoding="utf-8"))
    assert not leaks, f"{relative} logs secret material: {leaks}"


def test_sample_is_not_vacuous():
    total = 0
    for relative in SAMPLE_MODULES:
        path = ROOT / relative
        assert path.is_file(), f"sample module disappeared: {relative}"
        total += _scan(path.read_text(encoding="utf-8"))[1]
    assert total >= 120, f"expected a meaningful sample, found {total} log calls"
