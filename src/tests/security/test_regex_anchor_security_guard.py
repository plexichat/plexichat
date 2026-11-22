"""Guard: ``$``-anchored ``re.match``/``re.search`` must close the newline bypass.

Follow-up to the regex-anchor campaign: ``b04dc790`` (aiortc SFU identifiers)
and ``4c5223d3`` (transcript export ids). In both, a validator used
``re.match(r"^...$", value)`` and returned or stored ``value`` verbatim. Python's
``$`` also matches *just before* a final newline, so ``"ok\\n"`` satisfied the
gate while carrying a control byte into a recording path, an SDP/log line, or a
path-construction step. The fixes switched to ``fullmatch`` + explicit control
character rejection.

This guard scans the whole production ``src/`` tree from COMMITTED HEAD blobs
(``git cat-file --batch`` against a pinned SHA), not the working tree: many
source files are contested by parallel agents whose staged copies may be stale.
It enumerates every end-anchored ``match``/``search`` call -- literal patterns
passed to ``re.match``/``re.search`` and patterns compiled with ``re.compile``
and then matched -- and accepts a call only when it is:

* safe by construction: ``re.fullmatch`` (or an explicit ``\\Z`` end anchor);
* neutralized in the enclosing function: an upstream control-character
  rejection, a ``.strip()``/``.rstrip()`` of the value, or an allowlist
  membership check that a trailing newline cannot satisfy; or
* explicitly listed in ``DOCUMENTED_SAFE`` with a reviewed reason.

Anything else fails closed. Self-tests prove the detector flags a plain
``re.match(r"^ok$", user)`` gate, accepts fullmatch/``\\Z``/neutralized forms,
ignores a literal ``$`` inside a character class, and that documenting a case is
required to allow it. The real committed tree must have zero undocumented
offenders.
"""

import ast
import re
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

# Test/self-test trees and console entry points are not request-path validators.
EXCLUDED_PATH_COMPONENTS = frozenset({"tests", "selftest", "cli"})

# Neutralization signals that make a trailing newline harmless in the enclosing
# function. Kept deliberately concrete: a broad "whitespace" guess would let a
# new validator pass because of an unrelated strip elsewhere in the module.
_CONTROL_SIGNALS = (
    "reject_control_characters",
    "CONTROL_CHARACTERS",
    "_has_control_chars",
    "_SDP_UNICODE_CONTROL_CHARS",
    "iscntrl",
    "0x20",
    "0x7f",
    "control character",
    "control_character",
)
_STRIP_SIGNALS = (".strip(", ".lstrip(", ".rstrip(")
_ALLOWLIST_RE = re.compile(
    r"\b[A-Za-z_]\w*\s+(?:not\s+)?in\s+(?:[A-Z][A-Z0-9_]*|\{|\(|\[)"
)

# User-supplied-token vocabulary used only to prove the enumeration actually
# covers security decisions (non-vacuity); offender detection applies to every
# end-anchored match/search, so a new validator cannot evade by renaming.
_SENSITIVE_ARGS = frozenset(
    {
        "user",
        "value",
        "token",
        "secret",
        "code",
        "name",
        "path",
        "url",
        "host",
        "address",
        "route",
        "text",
        "key",
        "id",
        "raw",
        "segment",
        "col",
        "table_name",
        "column_name",
        "index_name",
        "issuer",
        "webhook_url",
        "hash_str",
        "checked",
        "label",
    }
)
_VALIDATOR_NAME_RE = re.compile(
    r"(valid|verify|check|allow|safe|is_|validate|parse_envelope|fingerprint)",
    re.IGNORECASE,
)


# (path, enclosing function, pattern, reason). Empty by design: every existing
# benign end-anchored call is documented here with the concrete reason the
# trailing newline cannot change a security decision. A NEW call never appears
# here and is therefore rejected unless it is neutralized or uses fullmatch.
DOCUMENTED_SAFE: tuple[tuple[str, str, str, str], ...] = (
    (
        "src/api/routes/join.py",
        "validate_invite_code",
        r"^[A-Za-z0-9_-]{1,64}$",
        "The value passes through `_bounded_text()`, which calls "
        "`reject_control_characters()` before this gate; a trailing newline is "
        "rejected upstream and the returned value is the control-char-free "
        "text, so `match` cannot be bypassed.",
    ),
    (
        "src/core/database/core/maintenance.py",
        "table_exists",
        r"^[a-zA-Z0-9_]+$",
        "Internal DB-maintenance existence check. The name is only used as a "
        "bound SQL parameter (`WHERE table_name = ?` / `name=?`), never "
        "interpolated into the query, so a trailing newline cannot alter it.",
    ),
    (
        "src/core/database/core/maintenance.py",
        "column_exists",
        r"^[a-zA-Z0-9_]+$",
        "Same bound-parameter rationale as `table_exists`: the table name "
        "flows through `_sanitize_identifier()` before any PRAGMA interpolation "
        "and the column name is compared to a returned row value.",
    ),
    (
        "src/core/database/core/maintenance.py",
        "index_exists",
        r"^[a-zA-Z0-9_]+$",
        "Internal existence check; the index name is used only as a bound "
        "parameter in the information_schema/sqlite_master query.",
    ),
    (
        "src/core/migrations/migrations/004_id_identity_defaults.py",
        "_missing_identity_tables",
        r"^[a-zA-Z_][a-zA-Z0-9_]*$",
        "One-shot migration: identifiers come from the server-owned DB "
        "catalogue, not user input. The regex is a belt-and-braces filter "
        "before DDL interpolation.",
    ),
    (
        "src/core/ratelimit/manager/route_keys.py",
        "_canonicalize_string",
        r"^([A-Za-z][A-Za-z0-9_:.-]*)[ \t]+(/.*)$",
        "Rate-limit key canonicalization of an already-normalized route, not "
        "an authorization gate; a trailing newline cannot change the canonical "
        "key prefix extracted here.",
    ),
    (
        "src/core/ratelimit/middleware.py",
        "_collapse_id_segments",
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
        "Rate-limit bucket labeling only: a matching segment becomes `{id}`. "
        "A trailing newline merely leaves the segment unlabeled (and it is "
        "still capped at 64 chars); no access decision depends on the match.",
    ),
    (
        "src/core/ratelimit/middleware.py",
        "_collapse_id_segments",
        r"^[0-9a-fA-F]{16,}$",
        "Same rate-limit bucket-labeling rationale as the UUID pattern above.",
    ),
    (
        "src/core/suite/sum/_formula_shared.py",
        "parse_cell_ref",
        r"^(?:(?:'([^']*)'|([A-Za-z_][A-Za-z0-9_]*))!)?\$?([A-Za-z]+)\$?([0-9]+)$",
        "Spreadsheet formula token parser: the tokenizer consumes only "
        "`match.end()` bytes and a newline cannot form a valid cell reference. "
        "Not an authorization/allowlist gate.",
    ),
    (
        "src/core/suite/sum/_formula_shared.py",
        "parse_cell_ref",
        r"^(?:(?:'([^']*)'|([A-Za-z_][A-Za-z0-9_]*))!)?([A-Za-z_][A-Za-z0-9_]*)$",
        "Same formula-tokenizer rationale as the cell-reference pattern above.",
    ),
    (
        "src/core/voice/signaling/ice.py",
        "_is_valid_hostname",
        r"^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*$",
        "The address is extracted by `ICECandidateParser.parse()`, which "
        "`.strip()`s the candidate and rejects every control byte via "
        "`_has_control_chars()` before grammar matching; the trailing-newline "
        "shape cannot reach this validator.",
    ),
    (
        "src/core/voice/signaling/sdp.py",
        "_parse_version",
        r"^0$",
        "SDP line value dispatched from `parse()`, which strips every line "
        "and rejects all control bytes (`ord(ch) < 0x20`, DEL, unicode "
        "separators) before dispatch. A newline cannot survive into `value`.",
    ),
    (
        "src/core/voice/signaling/sdp.py",
        "_parse_origin",
        r"^(\S+)\s+(\d+)\s+(\d+)\s+(IN)\s+(IP[46])\s+(\S+)$",
        "Same `parse()` line sanitization as `_parse_version`.",
    ),
    (
        "src/core/voice/signaling/sdp.py",
        "_parse_media",
        r"^(audio|video|application)\s+(\d+)(?:/(\d+))?\s+(\S+)(?:\s+(.+))?$",
        "Same `parse()` line sanitization as `_parse_version`.",
    ),
    (
        "src/core/voice/signaling/sdp.py",
        "_parse_attribute",
        r"^([^:]+)(?::(.*))?$",
        "Same `parse()` line sanitization as `_parse_version`.",
    ),
    (
        "src/core/voice/signaling/sdp.py",
        "_validate_ice_credentials",
        r"^[a-zA-Z0-9+/_-]{4,256}$",
        "ICE credentials are SDP attribute values from `parse()` lines, which "
        "are stripped and control-char-rejected before validation.",
    ),
    (
        "src/core/voice/signaling/sdp.py",
        "_validate_ice_credentials",
        r"^[a-zA-Z0-9+/_-]{22,256}$",
        "Same SDP-line sanitization rationale as the ice-ufrag pattern above.",
    ),
    (
        "src/core/voice/signaling/sdp.py",
        "_validate_fingerprint",
        r"^(sha-1|sha-224|sha-256|sha-384|sha-512)\s+([A-Fa-f0-9:]+)$",
        "DTLS fingerprint is an SDP attribute value from a stripped, "
        "control-char-rejected line.",
    ),
    (
        "src/core/webhooks/manager/execute.py",
        "execute_webhook_by_url",
        r"^/?webhooks/(\d+)/([A-Za-z0-9_.-]+)$",
        "The captured id/token groups stop at a newline (the digit and "
        "`[A-Za-z0-9_.-]` classes exclude `\\n`), so no value passed to "
        "`execute_webhook()` contains the trailing byte.",
    ),
    (
        "src/utils/encryption/pepper.py",
        "parse_envelope",
        r"^\$plexichat\$pepper-v1\$pid=([A-Za-z0-9_-]{1,32})\$(.+)$",
        "Stored pepper-hash envelope parser; `(.+)$` never captures a newline "
        "and verification fails closed on mismatch. Not a user-controlled "
        "authorization gate.",
    ),
)


@dataclass(frozen=True)
class RegexCall:
    path: str
    lineno: int
    func: str
    method: str
    pattern: str
    arg: str
    func_source: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.lineno}"


# ---------------------------------------------------------------------------
# Committed-tree plumbing
# ---------------------------------------------------------------------------


def _git(*args: str, input: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        input=input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr!r}"
    sha = proc.stdout.decode("utf-8", "replace").strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _read_blobs(commit: str, paths: tuple[str, ...]) -> dict[str, str]:
    """Read every ``<commit>:<path>`` blob in one ``git cat-file --batch`` call."""
    if not paths:
        return {}
    payload = ("\n".join(f"{commit}:{p}" for p in paths) + "\n").encode()
    proc = _git("cat-file", "--batch", input=payload)
    assert proc.returncode == 0, f"git cat-file --batch failed: {proc.stderr!r}"
    buf = proc.stdout
    pos = 0
    out: dict[str, str] = {}
    for path in paths:
        nl = buf.find(b"\n", pos)
        assert nl != -1, f"truncated cat-file header for {path}"
        header = buf[pos:nl].decode("utf-8", "replace").split()
        pos = nl + 1
        assert len(header) >= 3 and header[1] == "blob", (
            f"unexpected cat-file header for {path}: {header!r}"
        )
        size = int(header[2])
        out[path] = buf[pos : pos + size].decode("utf-8", "replace")
        pos += size + 1  # skip the newline cat-file appends to each blob
    return out


def _is_excluded(path: str) -> bool:
    return any(p in EXCLUDED_PATH_COMPONENTS for p in path.split("/")[:-1])


def _production_files(commit: str) -> tuple[str, ...]:
    proc = _git("ls-tree", "-r", "--name-only", commit, "--", "src")
    assert proc.returncode == 0, f"cannot list src: {proc.stderr!r}"
    files = tuple(
        p
        for p in proc.stdout.decode("utf-8", "replace").splitlines()
        if p.endswith(".py") and not _is_excluded(p)
    )
    assert len(files) > 1000, f"only {len(files)} production modules in committed tree"
    return files


# ---------------------------------------------------------------------------
# AST detector (also used by self-tests on synthetic modules)
# ---------------------------------------------------------------------------


def _literal(node: ast.AST) -> object:
    try:
        return ast.literal_eval(node)  # type: ignore[arg-type]
    except Exception:
        return None


def _has_end_anchor(pattern: str) -> bool:
    """True for an unescaped ``$`` outside a character class, or a ``\\Z`` end.

    ``$`` inside ``[...]`` is a literal dollar sign (e.g. the password
    special-character class) and must not be treated as an anchor. ``\\Z`` is
    included so the sweep also proves hard-anchored patterns are accepted.
    """
    in_class = False
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            if i + 1 < len(pattern) and pattern[i + 1] == "Z":
                return True
            i += 2
            continue
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "$" and not in_class:
            return True
        i += 1
    return False


def _enclosing_function(tree: ast.AST, node: ast.AST):
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
    return None


def _compiled_anchor_patterns(tree: ast.AST) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        func = node.value.func
        if not (isinstance(func, ast.Attribute) and func.attr == "compile"):
            continue
        if ast.unparse(func.value) != "re":
            continue
        pattern = _literal(node.value.args[0]) if node.value.args else None
        if not isinstance(pattern, str) or not _has_end_anchor(pattern):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = pattern
    return out


def _extract_calls(source: str, path: str) -> list[RegexCall]:
    tree = ast.parse(source, filename=path)
    lines = source.splitlines()
    compiled = _compiled_anchor_patterns(tree)
    calls: list[RegexCall] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in {"match", "search", "fullmatch"}:
            method = func.attr
            receiver = ast.unparse(func.value)
        else:
            continue

        pattern: object = None
        arg = ""
        if receiver == "re":
            pattern = _literal(node.args[0]) if node.args else None
            if len(node.args) > 1:
                arg = ast.unparse(node.args[1])
        elif receiver in compiled:
            pattern = compiled[receiver]
            if node.args:
                arg = ast.unparse(node.args[0])
        if not isinstance(pattern, str) or not _has_end_anchor(pattern):
            continue

        owner = _enclosing_function(tree, node)
        if owner is not None:
            func_source = "\n".join(lines[owner.lineno - 1 : owner.end_lineno or owner.lineno])
            func_name = owner.name
        else:
            func_source = source
            func_name = "<module>"
        calls.append(
            RegexCall(
                path=path,
                lineno=node.lineno,
                func=func_name,
                method=method,
                pattern=pattern,
                arg=arg,
                func_source=func_source,
            )
        )
    return calls


def _collect_calls(commit: str) -> list[RegexCall]:
    files = _production_files(commit)
    blobs = _read_blobs(commit, files)
    calls: list[RegexCall] = []
    for path, source in blobs.items():
        calls.extend(_extract_calls(source, path))
    return calls


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _safe_by_construction(call: RegexCall) -> bool:
    # A raw pattern ending in \Z pins the true end of the string; fullmatch
    # consumes the whole value regardless of the anchor spelling.
    return call.method == "fullmatch" or re.search(r"(?<!\\)\\Z", call.pattern) is not None


def _neutralization(call: RegexCall) -> str | None:
    src = call.func_source
    low = src.lower()
    for token in _CONTROL_SIGNALS:
        if token.lower() in low:
            return f"control-char rejection ({token})"
    for token in _STRIP_SIGNALS:
        if token in src:
            return f"value strip ({token})"
    if _ALLOWLIST_RE.search(src):
        return "allowlist membership"
    return None


def _documented_reason(call: RegexCall, documented=DOCUMENTED_SAFE) -> str | None:
    for path, func, pattern, reason in documented:
        if (path, func, pattern) == (call.path, call.func, call.pattern):
            return reason
    return None


def _offenders(calls: list[RegexCall], documented=DOCUMENTED_SAFE) -> list[str]:
    bad = []
    for call in calls:
        if (
            _safe_by_construction(call)
            or _neutralization(call)
            or _documented_reason(call, documented)
        ):
            continue
        bad.append(
            f"{call.location} [{call.func}] {call.method} "
            f"{call.pattern!r} on {call.arg!r}: end-anchored match without "
            "fullmatch/\\Z, neutralization, or a documented safe case"
        )
    return bad


def _gates_security_decision(call: RegexCall) -> bool:
    """Conservative classification used only to prove enumeration coverage."""
    if _safe_by_construction(call):
        return False
    name = call.arg.strip("'\"").split(".")[0].split("[")[0]
    return name in _SENSITIVE_ARGS or bool(_VALIDATOR_NAME_RE.search(call.func))


# ---------------------------------------------------------------------------
# Self-tests: a detector that never fires must not let the real scan pass
# ---------------------------------------------------------------------------


def _synthetic_calls(source: str) -> list[RegexCall]:
    return _extract_calls(textwrap.dedent(source), "src/_synthetic/gate.py")


def test_self_test_flags_plain_end_anchored_security_match():
    source = """
    import re

    def check_user(user):
        if not re.match(r"^ok$", user):
            return False
        return True
    """
    calls = _synthetic_calls(source)
    assert len(calls) == 1
    call = calls[0]
    assert call.method == "match" and call.pattern == r"^ok$"
    assert _gates_security_decision(call)
    assert not _safe_by_construction(call)
    assert _neutralization(call) is None
    assert _offenders(calls)  # <-- the bypass this campaign is about


def test_self_test_accepts_fullmatch_and_hard_end_anchor():
    source = """
    import re
    HARD = re.compile(r"^[a-z]+$")

    def a(user):
        return bool(re.fullmatch(r"^ok$", user))

    def b(user):
        return bool(re.match(r"^[a-z]+\\Z", user))

    def c(user):
        return bool(HARD.fullmatch(user))
    """
    calls = _synthetic_calls(source)
    assert len(calls) == 3
    assert all(_safe_by_construction(c) for c in calls)
    assert _offenders(calls) == []


def test_self_test_accepts_neutralized_forms():
    source = """
    import re
    from src.core.security.hardening import reject_control_characters

    def stripped(user):
        user = user.strip()
        return bool(re.match(r"^ok$", user))

    def control(user):
        reject_control_characters(user, "user")
        return bool(re.match(r"^ok$", user))

    def allowed(user):
        return user in ALLOWED_USERS and bool(re.match(r"^ok$", user))
    """
    calls = _synthetic_calls(source)
    assert len(calls) == 3
    assert {_neutralization(c) for c in calls} == {
        "value strip (.strip()",
        "control-char rejection (reject_control_characters)",
        "allowlist membership",
    }
    assert _offenders(calls) == []


def test_self_test_dollar_inside_character_class_is_not_an_anchor():
    source = """
    import re

    def password_ok(password):
        return bool(re.search(r"[!@#$%^&*]", password))
    """
    assert _synthetic_calls(source) == []


def test_self_test_documenting_a_case_is_required_to_allow_it():
    source = """
    import re

    def check_path(path):
        return bool(re.match(r"^/ok/[a-z]+$", path))
    """
    calls = _synthetic_calls(source)
    assert _offenders(calls)
    documented = (
        ("src/_synthetic/gate.py", "check_path", r"^/ok/[a-z]+$", "reviewed: internal only"),
    )
    assert _offenders(calls, documented) == []


# ---------------------------------------------------------------------------
# Real committed tree
# ---------------------------------------------------------------------------


def test_scan_is_not_vacuous(commit_sha):
    calls = _collect_calls(commit_sha)
    assert len(calls) >= 60, f"only {len(calls)} end-anchored regex calls enumerated"
    assert sum(_safe_by_construction(c) for c in calls) >= 25
    gating = [c for c in calls if _gates_security_decision(c)]
    assert len(gating) >= 25, f"only {len(gating)} security-decision gates enumerated"
    paths = {c.path for c in calls}
    for required in (
        "src/api/routes/join.py",  # invite-code validator
        "src/core/webhooks/manager/execute.py",  # user-supplied webhook URL format check
        "src/core/database/core/maintenance.py",  # identifier validators returning bool
        "src/core/voice/signaling/sdp.py",  # control-char-rejected SDP validators
        "src/core/voice/signaling/ice.py",  # relay-hostname allowlist gate
    ):
        assert required in paths, f"{required} unexpectedly absent from the sweep"
    # The reader returns committed content, not a stale/contested working copy.
    blob = _read_blobs(commit_sha, ("src/core/webhooks/manager/execute.py",))
    assert r"^/?webhooks/(\d+)/([A-Za-z0-9_.-]+)$" in blob[
        "src/core/webhooks/manager/execute.py"
    ]


def test_no_undocumented_end_anchored_validator(commit_sha):
    offenders = _offenders(_collect_calls(commit_sha))
    assert not offenders, (
        "End-anchored match/search gates without a newline-bypass defense:\n  "
        + "\n  ".join(sorted(offenders))
    )
