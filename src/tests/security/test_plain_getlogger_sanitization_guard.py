"""Structural guard: plain ``logging.getLogger`` sinks must be safe at HEAD.

Campaign context
----------------
The log-injection campaign hardened the shared ``AppLogger`` sink (which has a
``SanitizingFilter``) and then installed that same filter on the handful of
plain ``logging.getLogger(__name__)`` module loggers that actually log
caller-controlled data:

* ``a2da7643`` -- media upload (``filename`` / declared ``content_type``)
* ``51a3b46d`` -- media file access / signing
* ``42ee85e9`` -- plexijoin federation manager
* ``5a5cdb9e`` -- the ``admin`` audit file logger (its own escaping guard;
  excluded from this sweep by name)
* ``b6118196`` / ``358cb85b`` -- Referer/query redaction inside the shared
  sanitizer, not plain module loggers.

A plain module logger propagates to the root logger and therefore never sees
``AppLogger``'s filter. A later edit that either adds a NEW plain module logger
which interpolates a request/user/DB value, or adds such an interpolation to a
logger currently reviewed as static, silently reopens CWE-117 (log forging via
raw newlines, NEL, U+2028/U+2029 and bidi controls). Runtime tests cover each
fixed sink individually; nothing stops that regression. This is a pure AST
ratchet (no app import/config/DB) with four halves:

1. :data:`DOCUMENTED_LOGGERS` enumerates every plain ``logging.getLogger``
   module logger in production ``src/`` at HEAD. ``utils.logger``/``AppLogger``
   proxies and the documented ``admin`` logger are excluded. Each entry is
   either ``"filtered"`` (SanitizingFilter installed in that very module),
   ``"static"`` (no user-controlled data in its log calls -- with a written
   reason) or ``"proxy"`` (records reach the sanitized AppLogger proxy).
2. A whole-tree sweep flags any NEW plain module logger not in the registry and
   any interpolation of a value whose *name* is in ``USER_DATA_HINTS`` in an
   unfiltered, unreviewed log call. The only suppressed sites are the four
   filtered sinks plus four documented local-path/DB-id review keys (covering
   seven call sites, each with a reason). A novel module fails closed (it is
   absent from the registry). A novel *value* name fails OPEN by design: this
   is a fixed-vocabulary name heuristic, NOT data-flow/taint tracking, so
   caller-controlled data copied into an arbitrarily named local (e.g.
   ``evil = request.query_params["q"]; logger.info("x %s", evil)``) is not
   detected. That residual risk is characterized in
   ``test_known_limitation_novel_value_name_is_not_detected`` and is covered
   by human review of the enumerated loggers, not by this detector. Keeping the
   vocabulary narrow also avoids flagging every innocent ``%s`` interpolation.
3. Blobs are read from COMMITTED HEAD (``git show HEAD:<path>``), never the
   working tree: many production files are contested by parallel agents whose
   stale copies could mislead a working-tree scan, and a whole-file commit
   could otherwise silently revert a filter.
4. Non-vacuity: a synthetic unfiltered ``logger.warning(f"...{user_input}")``
   is flagged, while filtered/static/proxy cases are accepted; the committed
   tree must contain the four filtered sinks and exactly the seven documented
   safe interpolations; and a throwaway repo proves the reader returns HEAD.

Removal policy: a documented logger that disappears from the commit, or a
filter marker that vanishes from its blob, is a failure -- not a silent pass.
"""

from __future__ import annotations

import ast
import subprocess
import textwrap
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

import pytest

# Committed-tree sweep: ~5.5k HEAD blobs per test (20-30s here), so the
# suite-wide per-test timeout is too tight on a loaded runner. Assertions are
# unchanged; only the wall-clock budget.
pytestmark = [
    pytest.mark.security,
    pytest.mark.unit,
    pytest.mark.timeout(180),
]

ROOT = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# Git / committed-tree plumbing
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
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


BlobReader = Callable[[str], str]


def _make_blob_reader(commit: str, cwd: Path = ROOT) -> BlobReader:
    """Return a reader that fetches ``<commit>:<path>`` with ``git show``.

    ``cwd`` is injectable so the non-vacuity test can prove the reader returns
    committed content from a throwaway repo whose working tree has diverged.
    """

    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}", cwd=cwd)
        assert proc.returncode == 0, f"blob missing from {commit}: {path}"
        return proc.stdout

    return read


_EXCLUDED_COMPONENTS = frozenset({"tests", "selftest", "__pycache__"})

# The AppLogger machinery itself, and the conftest that manipulates it, are
# the sanitized proxy side of the campaign -- not plain module sinks.
_LOGGER_MACHINERY_PREFIX = "src/utils/common_utils/utils/logger/"


def _is_excluded(path: str) -> bool:
    parts = path.split("/")[:-1]
    if any(part in _EXCLUDED_COMPONENTS for part in parts):
        return True
    if path.endswith("conftest.py"):
        return True
    return path.startswith(_LOGGER_MACHINERY_PREFIX)


@lru_cache(maxsize=None)
def _production_files(commit: str) -> tuple[str, ...]:
    proc = _git("ls-tree", "-r", "--name-only", commit, "--", "src")
    assert proc.returncode == 0, f"cannot list src: {proc.stderr.strip()}"
    files = tuple(
        p for p in proc.stdout.splitlines() if p.endswith(".py") and not _is_excluded(p)
    )
    assert files, "no production python modules found in the committed tree"
    return files


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# ``import logging`` is sometimes aliased (``import logging as _logging`` in
# auth/totp.py, ``py_logging`` in cli/selftest.py).
_GETTER_BASES = frozenset({"logging", "_logging", "py_logging", "log"})

# Logger *names* that are sanitized proxies / the separately-hardened admin
# logger. A module using one of these names is not a plain module sink.
_PROXY_LOGGER_NAMES = frozenset({"AppLogger", "admin"})

_LOG_METHODS = frozenset(
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

# Names that mark an interpolated log value as request/user/DB-controlled.
# Deliberately excludes numeric/internal ids (``user_id``, ``file_id``,
# ``version``, ``count``) which are validated ints, and exception placeholders
# (``e``/``exc``) whose text is covered by the sink-level filter on real sinks.
# The seven locally-derived path/row hits this produces are documented below.
USER_DATA_HINTS = frozenset(
    {
        "user_input",
        "input",
        "untrusted",
        "attacker",
        "request",
        "req",
        "payload",
        "body",
        "query",
        "param",
        "params",
        "header",
        "headers",
        "username",
        "display_name",
        "nick",
        "email",
        "content",
        "message",
        "text",
        "user_text",
        "user_message",
        "filename",
        "file_name",
        "file_path",
        "path",
        "url",
        "uri",
        "form",
        "submitted",
        "provided",
        "supplied",
        "data",
        "row",
        "record",
    }
)

# Marker strings proving a module installed the sink-level guard.
FILTER_EVIDENCE: tuple[str, ...] = ("SanitizingFilter", "addFilter(")


@dataclass(frozen=True)
class PlainLogger:
    """A module-level ``VAR = logging.getLogger(NAME)`` in a production file."""

    path: str
    var: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.path, self.var)

    def __str__(self) -> str:
        return f"{self.path}:{self.var}"


def _getlogger_name(call: ast.Call) -> str | None:
    """Return the logger name for a ``logging.getLogger(...)`` call.

    ``None`` means the call is not a plain ``logging.getLogger`` of a supported
    module alias. A dynamic name such as ``__name__`` is returned as
    ``"<dynamic>"`` -- it is a plain module logger unless it is one of the
    explicitly sanitized proxy names.
    """
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "getLogger":
        return None
    base = func.value
    if not (isinstance(base, ast.Name) and base.id in _GETTER_BASES):
        return None
    if call.args and isinstance(call.args[0], ast.Constant):
        name = call.args[0].value
        if isinstance(name, str):
            return name
    return "<dynamic>"


def _plain_loggers(tree: ast.AST, path: str) -> list[PlainLogger]:
    """Enumerate plain module-level getLogger loggers in one module.

    Only assignments at module scope count: the inline, function-local
    ``_logging.getLogger(__name__)`` calls in ``auth/totp.py`` (constant or
    numeric-only messages) are not module loggers and are documented here as an
    intentional exclusion.
    """
    found: list[PlainLogger] = []
    for node in tree.body:
        targets: list[ast.Name] = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        for target in targets:
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            name = _getlogger_name(value)
            if name is None or name in _PROXY_LOGGER_NAMES:
                continue
            found.append(PlainLogger(path, target.id))
    return found


def _iter_log_calls(tree: ast.AST) -> Iterable[tuple[ast.Call, str]]:
    """Yield ``(call, logger_var)`` for every ``VAR.<logmethod>(...)`` call."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in _LOG_METHODS:
            continue
        if isinstance(func.value, ast.Name):
            yield node, func.value.id


def _interpolated_expressions(call: ast.Call) -> list[str]:
    """Stringify every value substituted into a log call's message.

    Covers f-strings (``JoinedStr``), lazy ``%``-style args (``logger.info(
    "x %s", v)``), ``str.format`` args and ``%``-formatting of the message.
    """
    exprs: list[str] = []
    message = call.args[0] if call.args else None
    if isinstance(message, ast.JoinedStr):
        for part in message.values:
            if isinstance(part, ast.FormattedValue):
                exprs.append(ast.unparse(part.value))
    if isinstance(message, ast.BinOp) and isinstance(message.op, ast.Mod):
        exprs.append(ast.unparse(message.right))
    if isinstance(message, ast.Call) and isinstance(message.func, ast.Attribute):
        if message.func.attr == "format":
            exprs.extend(ast.unparse(a) for a in message.args)
    # Lazy formatting args (the common ``logger.warning("... %s", v)`` form).
    exprs.extend(ast.unparse(a) for a in call.args[1:])
    return exprs


def _user_hints(expression: str) -> frozenset[str]:
    """Names in an interpolated expression that mark it as user-controlled."""
    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError:
        return frozenset()
    names = {n.id for n in ast.walk(parsed) if isinstance(n, ast.Name)}
    return frozenset(names & USER_DATA_HINTS)


# ---------------------------------------------------------------------------
# Registry of every plain module logger at HEAD
# ---------------------------------------------------------------------------

# Written reasons for the "static" classification. Each documents why the
# module's interpolated values are not attacker-controlled free text.
_R_API = (
    "HTTP-facing module, but its logger emits only constant strings, validated "
    "internal ids and local exception text; no raw request body/header/query "
    "value is interpolated. The request-data sinks go through the sanitized "
    "AppLogger proxy."
)
_R_ADMIN_NOTES = (
    "Admin-only note endpoints: the logger interpolates version numbers and "
    "integer target/author ids, never free-form attacker text."
)
_R_CORE_BASE = (
    "Module-availability diagnostic: only a numeric user id and an import "
    "exception string are interpolated."
)
_R_MEDIA_INTERNAL = (
    "Background media maintenance: values are internally generated ids, "
    "enum status values and local exception text. The caller-controlled "
    "filename/content_type sinks in this subsystem are the filtered "
    "_upload/_files/_signing loggers."
)
_R_MEDIA_STORAGE = (
    "Storage-backend startup diagnostics: interpolated values are local "
    "config labels, internal row ids (the reviewed DB-id site below) and "
    "local exception text -- no per-request user input."
)
_R_MIGRATIONS = (
    "Offline schema-migration tooling run at startup/CLI under operator "
    "control: values are local schema versions, counts and filenames from the "
    "trusted on-disk migrations directory (the reviewed local-path sites "
    "below). No HTTP request path supplies them."
)
_R_SCHEMA = (
    "Runtime schema reconciliation at startup: table/column names come from "
    "this codebase's own schema, not from user input."
)
_R_TELEMETRY = (
    "Aggregate counters and bounded internal exception text; no raw "
    "user-supplied field reaches a log line."
)
_R_AUDIT_BUFFER = "No dynamic log calls: only constant buffer-flush diagnostics."
_R_VOICE = (
    "SFU identity diagnostics: only local secret-resolution exception text; "
    "no request-controlled value."
)
_R_CONFIG = (
    "Config bootstrap: interpolated values are environment-variable names "
    "and local config paths, both operator-controlled."
)
_R_KEK = (
    "Local key-rotation CLI/migration tooling: operator-supplied env-var "
    "names and local keyring paths, not attacker-controlled request data."
)
_R_SYSTEM_ALERTS = "Internal alert counters and exception text only."


@dataclass(frozen=True)
class DocumentedLogger:
    """One committed plain module logger and why it is safe."""

    path: str
    var: str
    status: str  # "filtered" | "static" | "proxy"
    why: str
    evidence: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (self.path, self.var)


def _f(path: str, why: str) -> DocumentedLogger:
    return DocumentedLogger(path, "logger", "filtered", why, FILTER_EVIDENCE)


def _s(path: str, why: str, var: str = "logger") -> DocumentedLogger:
    return DocumentedLogger(path, var, "static", why)


DOCUMENTED_LOGGERS: tuple[DocumentedLogger, ...] = (
    _s("src/api/__init__.py", _R_API),
    _s("src/api/routes/applications.py", _R_API),
    _s("src/api/routes/bots.py", _R_API),
    _s("src/core/admin/notes.py", _R_ADMIN_NOTES),
    _s("src/core/base.py", _R_CORE_BASE),
    _f(
        "src/core/media/_files.py",
        "51a3b46d: file-access logger logs user-scoped file ids and paths; "
        "SanitizingFilter neutralises forged lines.",
    ),
    _s("src/core/media/_phash.py", _R_MEDIA_INTERNAL),
    _s("src/core/media/_processing.py", _R_MEDIA_INTERNAL),
    _s("src/core/media/_proxy.py", _R_MEDIA_INTERNAL),
    _s("src/core/media/_rate_limit.py", _R_MEDIA_INTERNAL),
    _s("src/core/media/_scanning.py", _R_MEDIA_INTERNAL),
    _f(
        "src/core/media/_signing.py",
        "51a3b46d: signing logger logs a caller-supplied filename; "
        "SanitizingFilter neutralises forged lines.",
    ),
    _s("src/core/media/_storage_setup.py", _R_MEDIA_STORAGE),
    _s("src/core/media/_thumbnails.py", _R_MEDIA_INTERNAL),
    _f(
        "src/core/media/_upload.py",
        "a2da7643: upload logger logs the untrusted filename/content_type; "
        "SanitizingFilter neutralises forged lines.",
    ),
    _s("src/core/media/_validation.py", _R_MEDIA_INTERNAL),
    _s("src/core/media/manager.py", _R_MEDIA_INTERNAL),
    _s("src/core/messaging/schema.py", _R_SCHEMA),
    _s("src/core/migrations/__init__.py", _R_MIGRATIONS),
    _s("src/core/migrations/cli.py", _R_MIGRATIONS, var="cli_logger"),
    _s("src/core/migrations/locking.py", _R_MIGRATIONS),
    _s("src/core/migrations/manager.py", _R_MIGRATIONS),
    _s("src/core/migrations/migrations/000_initial_schema.py", _R_MIGRATIONS),
    _s("src/core/migrations/migrations/001_selftest_noop.py", _R_MIGRATIONS),
    _s("src/core/migrations/migrations/002_artifact_rev.py", _R_MIGRATIONS),
    _s("src/core/migrations/migrations/003_srv_members_is_temporary.py", _R_MIGRATIONS),
    _s("src/core/migrations/migrations/004_id_identity_defaults.py", _R_MIGRATIONS),
    _s("src/core/migrations/migrations/005_approved_by_text.py", _R_MIGRATIONS),
    _s("src/core/migrations/migrations/006_report_queue_indexes.py", _R_MIGRATIONS),
    _s(
        "src/core/migrations/migrations/007_hot_lookup_composite_indexes.py",
        _R_MIGRATIONS,
    ),
    _s("src/core/migrations/migrations/008_auth_lookup_indexes.py", _R_MIGRATIONS),
    _s(
        "src/core/migrations/migrations/009_relationship_lookup_indexes.py",
        _R_MIGRATIONS,
    ),
    _s(
        "src/core/migrations/migrations/010_server_hot_lookup_indexes.py", _R_MIGRATIONS
    ),
    _s(
        "src/core/migrations/migrations/011_admin_audit_action_created_index.py",
        _R_MIGRATIONS,
    ),
    _s(
        "src/core/migrations/migrations/012_admin_audit_stats_covering_index.py",
        _R_MIGRATIONS,
    ),
    _s(
        "src/core/migrations/migrations/013_message_status_receipt_index.py",
        _R_MIGRATIONS,
    ),
    _s("src/core/migrations/migrations/014_webhook_listing_indexes.py", _R_MIGRATIONS),
    _s("src/core/migrations/runner.py", _R_MIGRATIONS),
    _s("src/core/migrations/tracker.py", _R_MIGRATIONS),
    _s("src/core/migrations/validator.py", _R_MIGRATIONS),
    _f(
        "src/core/plexijoin/manager.py",
        "42ee85e9: federation manager logs remote connection/message values; "
        "SanitizingFilter neutralises forged lines.",
    ),
    _s("src/core/servers/handlers/audit_write_buffer.py", _R_AUDIT_BUFFER),
    _s("src/core/telemetry/__init__.py", _R_TELEMETRY),
    _s(
        "src/core/voice/signaling/sfu/mediasoup_ws/identity.py",
        _R_VOICE,
        var="_log",
    ),
    _s("src/utils/common_utils/utils/config/core.py", _R_CONFIG),
    _s("src/utils/encryption/channel_ratchet/notify.py", _R_MEDIA_INTERNAL),
    _s("src/utils/encryption/kek_migration/cli.py", _R_KEK),
    _s("src/utils/encryption/kek_migration/migration.py", _R_KEK),
    _s("src/utils/encryption/kek_migration/protocol.py", _R_KEK),
    _s("src/utils/system_alerts.py", _R_SYSTEM_ALERTS),
)


# The ONLY unfiltered "user-data-hint" interpolations allowed. Each is a
# locally-derived path / DB id from an offline, operator-controlled code path
# (never per-request user input). New hints must be reviewed and added here.
@dataclass(frozen=True)
class SafeInterpolation:
    path: str
    func: str
    expr: str
    reason: str


DOCUMENTED_SAFE_INTERPOLATIONS: tuple[SafeInterpolation, ...] = (
    SafeInterpolation(
        "src/core/media/_storage_setup.py",
        "_row_to_media_file",
        "row['id']",
        "Internal DB primary key from the media rows table, not user text.",
    ),
    SafeInterpolation(
        "src/core/migrations/manager.py",
        "_extract_dependencies",
        "file_path",
        "Local migrations-directory path from glob, operator-controlled.",
    ),
    SafeInterpolation(
        "src/core/migrations/validator.py",
        "get_migration_files",
        "filename",
        "On-disk migration filenames from the trusted migrations directory "
        "(two call sites collapse to this one key).",
    ),
    SafeInterpolation(
        "src/core/migrations/validator.py",
        "validate_migration_file",
        "file_path",
        "Local migrations-directory path, operator-controlled (two call "
        "sites collapse to this one key).",
    ),
)


# ---------------------------------------------------------------------------
# Detector + evaluation (injectable for self-tests)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnsafeLogCall:
    path: str
    var: str
    func: str
    lineno: int
    expr: str
    hints: frozenset[str]

    def __str__(self) -> str:
        return (
            f"{self.path}:{self.lineno} in {self.func}(): "
            f"logger {self.var!r} interpolates {self.expr!r} "
            f"({', '.join(sorted(self.hints))})"
        )


def _enclosing_function(tree: ast.AST, lineno: int) -> str:
    """Name of the innermost function containing ``lineno`` (stable key)."""
    best_name = "<module>"
    best_start = -1
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            if node.lineno <= lineno <= end and node.lineno > best_start:
                best_start = node.lineno
                best_name = node.name
    return best_name


def _unsafe_calls(
    tree: ast.AST, path: str, plain_vars: set[str]
) -> list[UnsafeLogCall]:
    """User-controlled interpolations on plain module loggers in one module."""
    findings: list[UnsafeLogCall] = []
    for call, var in _iter_log_calls(tree):
        if var not in plain_vars:
            continue  # AppLogger / other sanitized proxy: not a plain sink
        for expr in _interpolated_expressions(call):
            hints = _user_hints(expr)
            if not hints:
                continue
            findings.append(
                UnsafeLogCall(
                    path,
                    var,
                    _enclosing_function(tree, call.lineno),
                    call.lineno,
                    expr,
                    hints,
                )
            )
    return findings


def _evaluate_module(
    source: str,
    path: str,
    registry: dict[tuple[str, str], DocumentedLogger],
    safe_hits: set[tuple[str, str, str]],
) -> tuple[list[PlainLogger], list[UnsafeLogCall]]:
    """Return (plain module loggers, offenders) for one source blob.

    The registry and safe-hit set are injectable so self-tests can exercise the
    filtered/static/proxy branches with synthetic modules without touching the
    real committed tree.
    """
    tree = ast.parse(source)
    plain = _plain_loggers(tree, path)
    offenders: list[UnsafeLogCall] = []
    for finding in _unsafe_calls(tree, path, {p.var for p in plain}):
        record = registry.get((finding.path, finding.var))
        if record is not None:
            if record.status == "filtered" and all(
                marker in source for marker in record.evidence
            ):
                continue  # sink-level filter installed in this very module
            if record.status == "proxy":
                continue  # records reach the sanitized AppLogger proxy
        if (finding.path, finding.func, finding.expr) in safe_hits:
            continue  # reviewed, documented local value
        offenders.append(finding)
    return plain, offenders


def _sweep(
    commit: str,
    registry: dict[tuple[str, str], DocumentedLogger],
    safe_hits: set[tuple[str, str, str]],
) -> tuple[list[PlainLogger], list[UnsafeLogCall]]:
    reader = _make_blob_reader(commit)
    loggers: list[PlainLogger] = []
    offenders: list[UnsafeLogCall] = []
    for path in _production_files(commit):
        source = reader(path)
        try:
            ast.parse(source)
        except SyntaxError as exc:
            raise AssertionError(f"cannot parse committed {path}: {exc}") from exc
        found, bad = _evaluate_module(source, path, registry, safe_hits)
        loggers.extend(found)
        offenders.extend(bad)
    return loggers, offenders


REGISTRY: dict[tuple[str, str], DocumentedLogger] = {
    r.key: r for r in DOCUMENTED_LOGGERS
}
SAFE_HITS: set[tuple[str, str, str]] = {
    (s.path, s.func, s.expr) for s in DOCUMENTED_SAFE_INTERPOLATIONS
}


# ---------------------------------------------------------------------------
# Self-tests: a detector that never fires must not let the real scan pass
# ---------------------------------------------------------------------------


def test_self_test_unfiltered_user_input_interpolation_is_flagged():
    """The exact campaign shape: a plain logger with f-string user data."""
    source = textwrap.dedent(
        """
        import logging
        logger = logging.getLogger(__name__)

        def handle(user_input):
            logger.warning(f"bad request from {user_input}")
        """
    )
    plain, offenders = _evaluate_module(
        source, "src/api/_synthetic.py", REGISTRY, SAFE_HITS
    )
    assert [p.var for p in plain] == ["logger"]
    assert len(offenders) == 1, offenders
    assert "user_input" in offenders[0].hints
    assert offender_is_on_plain_logger(offenders[0], source)


def offender_is_on_plain_logger(finding: UnsafeLogCall, source: str) -> bool:
    """A finding must name a variable that is actually a plain module logger."""
    tree = ast.parse(source)
    return finding.var in {p.var for p in _plain_loggers(tree, finding.path)}


def test_self_test_filtered_sink_is_accepted():
    """A filtered sink is accepted only while its filter marker is present."""
    source = textwrap.dedent(
        """
        import logging
        from src.utils.common_utils.utils.logger.core import SanitizingFilter
        logger = logging.getLogger(__name__)
        logger.addFilter(SanitizingFilter())

        def handle(user_input):
            logger.warning(f"bad request from {user_input}")
        """
    )
    record = DocumentedLogger(
        "src/api/_synthetic.py", "logger", "filtered", "self-test", FILTER_EVIDENCE
    )
    reg = {record.key: record}
    _, offenders = _evaluate_module(source, record.path, reg, SAFE_HITS)
    assert offenders == []
    # Remove the filter call: the same message is now flagged (non-vacuity).
    gutted = source.replace("logger.addFilter(SanitizingFilter())", "")
    _, offenders = _evaluate_module(gutted, record.path, reg, SAFE_HITS)
    assert len(offenders) == 1, offenders


def test_self_test_static_and_proxy_cases_are_accepted():
    """Static constants/ids and sanitized-proxy logging are not flagged."""
    static = textwrap.dedent(
        """
        import logging
        logger = logging.getLogger(__name__)

        def boot(version):
            logger.info(f"starting migration {version}")
        """
    )
    _, offenders = _evaluate_module(
        static, "src/api/_synthetic.py", REGISTRY, SAFE_HITS
    )
    assert offenders == []

    # A documented safe local path interpolation is accepted at its own site.
    local = textwrap.dedent(
        """
        import logging
        logger = logging.getLogger(__name__)

        def get_migration_files(filename):
            logger.warning(f"skipping {filename}")
        """
    )
    # Use the real documented site path so the review entry applies.
    local_path = "src/core/migrations/validator.py"
    _, offenders = _evaluate_module(local, local_path, REGISTRY, SAFE_HITS)
    assert offenders == []

    # Records routed through an AppLogger proxy are not plain sinks at all.
    proxy = textwrap.dedent(
        """
        from src.utils.common_utils.utils.logger import logger

        def handle(user_input):
            logger.warning(f"bad request from {user_input}")
        """
    )
    plain, offenders = _evaluate_module(
        proxy, "src/api/_synthetic.py", REGISTRY, SAFE_HITS
    )
    assert plain == [] and offenders == []


def test_self_test_proxy_status_suppresses_a_finding():
    """The explicit 'proxy' status branch is exercised, not just implicit."""
    source = textwrap.dedent(
        """
        import logging
        logger = logging.getLogger(__name__)

        def handle(user_input):
            logger.warning(f"bad request from {user_input}")
        """
    )
    record = DocumentedLogger("src/api/_synthetic.py", "logger", "proxy", "self-test")
    _, offenders = _evaluate_module(
        source, record.path, {record.key: record}, SAFE_HITS
    )
    assert offenders == []


def test_self_test_unregistered_plain_logger_fails_closed():
    """A new plain logger with user data is flagged with an empty registry."""
    source = textwrap.dedent(
        """
        import logging
        fresh = logging.getLogger(__name__)

        def handle(payload):
            fresh.warning("payload %s", payload)
        """
    )
    _, offenders = _evaluate_module(source, "src/api/_brand_new.py", {}, SAFE_HITS)
    assert len(offenders) == 1, offenders
    assert "payload" in offenders[0].hints


def test_known_limitation_novel_value_name_is_not_detected() -> None:
    """Characterize the name-heuristic limit (a KNOWN GAP, not an endorsement).

    ``USER_DATA_HINTS`` is a fixed vocabulary, so user-controlled request data
    copied into a local whose name is absent from it is invisible to the AST
    sweep -- even though the same expression interpolated inline (with the
    ``request`` hint present) *is* flagged. This guard is a name-based ratchet,
    not taint tracking; the enumerated-logger registry and human review are
    what cover such a sink. The test records the verified probe behaviour so
    the limitation is explicit rather than silently assumed away; it is not a
    claim that the renamed form is safe. The narrow vocabulary is intentional:
    broadening it to every ``%s`` would drown the signal in false positives.
    """
    renamed = textwrap.dedent(
        """
        import logging
        logger = logging.getLogger(__name__)

        def handle(request):
            evil = request.query_params.get("q")
            logger.info("x %s", evil)
        """
    )
    inline = textwrap.dedent(
        """
        import logging
        logger = logging.getLogger(__name__)

        def handle(request):
            logger.info("x %s", request.query_params.get("q"))
        """
    )
    _, renamed_offenders = _evaluate_module(
        renamed, "src/api/_synthetic.py", {}, SAFE_HITS
    )
    _, inline_offenders = _evaluate_module(
        inline, "src/api/_synthetic.py", {}, SAFE_HITS
    )
    assert renamed_offenders == [], (
        "vocabulary/taint behaviour changed: revisit this note"
    )
    assert inline_offenders, "the inline request expression must still be detectable"


# ---------------------------------------------------------------------------
# Real committed tree
# ---------------------------------------------------------------------------


def test_registry_is_well_formed() -> None:
    keys = [r.key for r in DOCUMENTED_LOGGERS]
    assert keys, "registry must not be empty"
    assert len(keys) == len(set(keys)), "duplicate logger entries in registry"
    for record in DOCUMENTED_LOGGERS:
        assert record.path.startswith("src/"), record.path
        assert record.var, record.path
        assert record.status in {"filtered", "static", "proxy"}, record
        assert record.why.strip(), f"undocumented classification: {record.key}"
        if record.status == "filtered":
            assert set(record.evidence) == set(FILTER_EVIDENCE), record.key
    for safe in DOCUMENTED_SAFE_INTERPOLATIONS:
        assert safe.reason.strip(), safe
        assert (safe.path, safe.func, safe.expr) in SAFE_HITS


def test_scan_is_not_vacuous(commit_sha: str) -> None:
    files = _production_files(commit_sha)
    assert len(files) >= 1000, f"only {len(files)} production modules scanned"
    for required in (
        "src/core/media/_upload.py",
        "src/core/media/_files.py",
        "src/core/media/_signing.py",
        "src/core/plexijoin/manager.py",
    ):
        assert required in files, f"{required} unexpectedly excluded from scan"
    # The reader really returns committed content: each filtered sink carries
    # its filter in HEAD even when a peer's working copy is stale.
    reader = _make_blob_reader(commit_sha)
    for path in (
        "src/core/media/_upload.py",
        "src/core/media/_files.py",
        "src/core/media/_signing.py",
        "src/core/plexijoin/manager.py",
    ):
        blob = reader(path)
        assert all(marker in blob for marker in FILTER_EVIDENCE), path


def test_registry_covers_every_committed_plain_module_logger(commit_sha: str) -> None:
    """Enumeration + fail-closed: no unregistered plain getLogger module."""
    found, _ = _sweep(commit_sha, REGISTRY, SAFE_HITS)
    found_keys = {p.key for p in found}
    registered = set(REGISTRY)
    assert found_keys == registered, (
        "plain getLogger module logger set drifted from the documented registry.\n"
        f"  NEW/unregistered loggers: {sorted(found_keys - registered)}\n"
        f"  stale registry entries: {sorted(registered - found_keys)}"
    )
    assert len(found_keys) >= 40, "suspiciously few module loggers enumerated"


def test_filtered_loggers_still_install_the_sanitizing_filter(commit_sha: str) -> None:
    """Each filtered sink's committed blob still installs SanitizingFilter."""
    reader = _make_blob_reader(commit_sha)
    filtered = [r for r in DOCUMENTED_LOGGERS if r.status == "filtered"]
    assert len(filtered) >= 4, "filtered sink registry was gutted"
    for record in filtered:
        blob = reader(record.path)
        assert all(marker in blob for marker in record.evidence), (
            f"{record.key} no longer installs the sink-level SanitizingFilter"
        )


def test_detector_finds_exactly_the_documented_safe_sites(commit_sha: str) -> None:
    """Non-vacuity: suppressions match the real committed hits exactly.

    Run with no suppression at all -- the detector must fire on precisely the
    documented local-path/DB-id sites (seven calls, four unique keys). Fewer
    means the detector is vacuous; more means a new, undocumented sink exists.
    """
    _, raw = _sweep(commit_sha, REGISTRY, set())
    found = {(f.path, f.func, f.expr) for f in raw}
    expected = SAFE_HITS
    assert found == expected, (
        "unfiltered user-data interpolation set drifted from the documented "
        "safe sites.\n"
        f"  undocumented (NEW) sinks: {sorted(found - expected)}\n"
        f"  stale safe-site entries: {sorted(expected - found)}"
    )


def test_no_unfiltered_user_controlled_log_interpolation(commit_sha: str) -> None:
    """The guard: every unfiltered interpolation is filtered/documented/safe."""
    _, offenders = _sweep(commit_sha, REGISTRY, SAFE_HITS)
    assert not offenders, (
        "unfiltered user/request/DB-controlled log interpolation(s) in "
        "production (install SanitizingFilter on the module logger, route "
        "through the AppLogger proxy, or document the local value as safe):\n  "
        + "\n  ".join(
            str(o) for o in sorted(offenders, key=lambda o: (o.path, o.lineno))
        )
    )


def test_reader_returns_committed_blob_not_stale_working_tree(tmp_path: Path) -> None:
    """Non-vacuity: the reader returns HEAD, not a peer's stale working copy."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "sink.py"
    target.write_text("logger.addFilter(SanitizingFilter())\n", encoding="utf-8")

    assert _git("init", "-q", cwd=repo).returncode == 0
    assert _git("add", "sink.py", cwd=repo).returncode == 0
    committed = _git(
        "-c",
        "user.email=guard@example.invalid",
        "-c",
        "user.name=guard",
        "commit",
        "-qm",
        "sink",
        cwd=repo,
    )
    assert committed.returncode == 0, committed.stderr
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    assert sha

    # Peer-style stale working copy: the filter is gone from disk only.
    target.write_text("# filter removed from working tree\n", encoding="utf-8")
    assert "SanitizingFilter" not in target.read_text(encoding="utf-8")

    reader = _make_blob_reader(sha, cwd=repo)
    blob = reader("sink.py")
    assert "SanitizingFilter" in blob
    assert "filter removed" not in blob
