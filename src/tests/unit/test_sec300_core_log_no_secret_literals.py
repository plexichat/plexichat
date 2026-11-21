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


def _scan_tree(tree):
    """Return ``(leaks, log_call_count)`` from an already-parsed module tree.

    Each leak is ``(lineno, kind, evidence)`` where kind is ``literal`` or
    ``interpolation``.
    """
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
    ``test_sample_is_not_vacuous``, which re-read and re-scanned all 16 modules
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


@pytest.mark.parametrize(
    "relative,source,tree", _SAMPLES, ids=[relative for relative, _, _ in _SAMPLES]
)
def test_no_secret_literals_in_fresh_core_log_messages(relative, source, tree):
    leaks, _ = _scan_tree(tree)
    assert not leaks, f"{relative} logs secret material: {leaks}"


def test_sample_is_not_vacuous():
    total = sum(_scan_tree(tree)[1] for _, _, tree in _SAMPLES)
    assert total >= 120, f"expected a meaningful sample, found {total} log calls"


def test_sample_modules_are_read_and_parsed_once(monkeypatch):
    """Perf regression: every sample module is read and AST-parsed once.

    The detector used to be a source-string parser (``_scan(source)``) called
    from both the per-module parametrized test and the non-vacuity test, so all
    16 sample modules were read and parsed twice for no benefit. Counting the
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
