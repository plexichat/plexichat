"""Structural guard: security string validators are whole-string + control-safe.

Follow-up to the path/identifier validation campaign:

* ``b04dc790`` -- aiortc SFU ids used as recording *filename components* were
  validated with ``_ID_RE.match``; Python's ``$`` also matches just before a
  trailing newline, so ``"room\\n"`` passed and landed in a path and a log line.
  The fix rejects the shared ``CONTROL_CHARACTERS`` set and uses ``fullmatch``.
* ``4c5223d3`` -- export ids are interpolated into a private-directory path;
  ``^export_[...]+$`` + ``match`` accepted a trailing newline.  The fix pins
  ``\\Z`` on the pattern, calls ``reject_control_characters`` and uses
  ``fullmatch``.
* ``e4bfb4a1`` -- command / option names (stored verbatim, echoed in events and
  used in authz-style name matching) used ``\\w`` + ``$`` + ``match``, admitting
  Unicode confusables and trailing newlines.  The fix uses ASCII-only patterns
  with ``fullmatch`` plus an NFKC/ASCII confusables check.

Runtime tests cover each site individually, but nothing stops a later edit from
replacing ``fullmatch`` with ``match``, or from adding a brand new loose-anchored
or control-char-permissive validator under a name nobody thought to test.  This
is a pure AST ratchet (no app import/config/DB) with three halves:

1. ``DOCUMENTED_VALIDATORS`` enumerates every production validator that feeds a
   filesystem path, DB lookup, event audience or authz decision (the four
   campaign fixes plus the other known-bounded ones).  ``anchored`` entries must
   still show ``fullmatch``/``\\Z`` AND a control-rejection marker in the
   committed function body; ``documented-loose`` entries are the reviewed
   cases where a bare ``match`` survives only because an upstream strip,
   control rejection, strict class or server-owned allowlist closes the gap --
   each carries a written reason.

2. A whole-tree sweep flags any *new* security-named function that uses a
   ``$``-terminated regex with ``match`` and has none of the mitigation
   markers.  A novel name with a loose anchor (or a permissive class such as
   ``(.+)`` / ``[^`` that passes NUL) fails closed.

3. The committed-tree reader really returns HEAD blobs, and both detectors are
   proven non-vacuous by synthetic self-tests (a synthetic ``match(r"^id$")``
   and a NUL-passing validator are flagged; fullmatch/control-rejected cases
   are accepted).

Blobs are read from COMMITTED HEAD (``git show HEAD:<path>``), not the working
tree: many production files are contested by parallel agents whose stale copies
can mislead a working-tree scan while a whole-file commit could silently revert
a fix.
"""

from __future__ import annotations

import ast
import subprocess
import textwrap
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# Git / committed-tree plumbing
# ---------------------------------------------------------------------------


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
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


def _make_blob_reader(commit: str):
    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}")
        assert proc.returncode == 0, f"blob missing from {commit}: {path}"
        return proc.stdout

    return read


_EXCLUDED_COMPONENTS = frozenset({"tests", "selftest"})


def _is_excluded(path: str) -> bool:
    return any(part in _EXCLUDED_COMPONENTS for part in path.split("/")[:-1])


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
# Vocabulary: what counts as a validator / loose anchor / mitigation
# ---------------------------------------------------------------------------

# A name is "security relevant" when it validates/returns an identifier,
# filename, path, name, url, token, code, key or similar and feeds a path, DB
# lookup, event audience or authz decision.  The label set is intentionally
# broad so a NOVEL name in the same family is still swept (fails closed).
NAME_HINTS = (
    "valid",
    "verify",
    "check",
    "sanitize",
    "sanitise",
    "parse",
    "normalize",
    "normalise",
    "identifier",
    "filename",
    "path",
    "url",
    "token",
    "secret",
    "name",
    "_id",
    "key",
    "slug",
    "input",
)

# Evidence anywhere in the function body that the trailing-newline / control
# character gap is closed.  ``fullmatch``/``\Z`` close the anchor; the others
# reject the control set or strip it upstream.  ``\\\\Z`` is the ast.unparse
# rendering of a literal ``\Z`` inside a string constant.
MITIGATION_MARKERS = (
    "fullmatch",
    r"\Z",
    "\\\\Z",
    "reject_control_characters",
    "CONTROL_CHARACTERS",
    ".strip()",
    "cap_config_string",
    "is_valid_control",
    "_reject_sdp_control",
)

# Proof that a validator consumes the whole string (the campaign's core rule).
ANCHOR_MARKERS = ("fullmatch", r"\Z", "\\\\Z")

# Proof that the shared control set cannot survive.  ``_is_confusable_safe``
# (isascii + NFKC) rejects every non-ASCII control, while the ASCII-only
# character class rejects C0/DEL; the helpers/policies reject them directly.
CONTROL_MARKERS = (
    "CONTROL_CHARACTERS",
    "reject_control_characters",
    "_is_confusable_safe",
    "RejectUICharacters",
    "cap_config_string",
    "is_control_character",
)

# Regex fragments that let a NUL/control character through even when the
# anchor is strict.  Flagged separately in the finding's reason.
PERMISSIVE_TOKENS = ("(.+)", "(.*)", ".+", ".*", "[^", r"\w", r"\W", r"\s", r"\S")


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _module_patterns(tree: ast.AST, path: str) -> dict[tuple[str, str], str]:
    """Map ``(path, CONST)`` -> regex source for module-level compiled patterns."""
    patterns: dict[tuple[str, str], str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if _call_name(node.value) != "compile" or not node.value.args:
            continue
        first = node.value.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                patterns[(path, target.id)] = first.value
    return patterns


def _match_pattern(
    call: ast.Call, path: str, module_patterns: dict[tuple[str, str], str]
) -> str | None:
    """Return the regex string used by ``re.match(...)`` / ``CONST.match(...)``."""
    if _call_name(call) != "match":
        return None
    func = call.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "re"
    ):
        # re.match(r"^...$", value)
        if call.args and isinstance(call.args[0], ast.Constant):
            value = call.args[0]
            if isinstance(value.value, str):
                return value.value
        return None
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        # COMPILED_PATTERN.match(value)
        return module_patterns.get((path, func.value.id))
    return None


def _evidence(fn: ast.AST, source: str) -> str:
    """Unparsed + raw source of a function (so both ``\\Z`` spellings appear)."""
    raw = ast.get_source_segment(source, fn) or ""  # type: ignore[arg-type]
    try:
        pretty = ast.unparse(fn)
    except Exception:  # pragma: no cover - defensive for exotic ASTs
        pretty = ""
    return pretty + "\n" + raw


def _is_security_named(name: str) -> bool:
    low = name.lower()
    return any(hint in low for hint in NAME_HINTS)


@dataclass(frozen=True)
class LooseFinding:
    path: str
    func: str
    lineno: int
    pattern: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno} in {self.func}(): {self.reason} [{self.pattern}]"


def _loose_validator_candidates(
    tree: ast.AST,
    *,
    path: str,
    source: str,
    exceptions: tuple[ValidatorSite, ...] = (),
) -> list[LooseFinding]:
    """Security-named functions with a loose ``match`` and no mitigation."""
    allowed = {(e.rel_path, e.func) for e in exceptions}
    module_patterns = _module_patterns(tree, path)
    found: list[LooseFinding] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if (path, fn.name) in allowed:
            continue
        if not _is_security_named(fn.name):
            continue
        evidence = _evidence(fn, source)
        if any(marker in evidence for marker in MITIGATION_MARKERS):
            continue
        for call in ast.walk(fn):
            if not isinstance(call, ast.Call):
                continue
            pattern = _match_pattern(call, path, module_patterns)
            if pattern is None or not pattern.endswith("$") or r"\Z" in pattern:
                continue
            permissive = any(tok in pattern for tok in PERMISSIVE_TOKENS)
            reason = "$ + match ignores a trailing newline (bare match + $)"
            if permissive:
                reason += "; permissive class also passes NUL/control characters"
            found.append(LooseFinding(path, fn.name, call.lineno, pattern, reason))
            break
    return found


# ---------------------------------------------------------------------------
# Part 1: documented validators
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidatorSite:
    """A production string validator whose hardening must survive in HEAD.

    ``mode="anchored"`` entries must show a whole-string anchor AND a control
    rejection in the committed function body.  ``mode="documented-loose"``
    entries are reviewed bare-``match`` cases kept safe by an upstream helper,
    strict class, allowlist or out-of-scope context; ``why`` is that reason.
    """

    label: str
    rel_path: str
    func: str
    sink: str
    mode: str
    markers: tuple[str, ...]
    why: str
    module_markers: tuple[str, ...] = ()


DOCUMENTED_VALIDATORS: tuple[ValidatorSite, ...] = (
    # ---- anchored + control-rejected (the campaign fixes) -----------------
    ValidatorSite(
        "aiortc SFU identifier",
        "src/core/voice/signaling/sfu/aiortc_adapter/validation.py",
        "validate_identifier",
        "recording filename path + log line",
        "anchored",
        ("CONTROL_CHARACTERS", "fullmatch", "_ID_RE"),
        "Id is used verbatim as a recording filename component and in a log "
        "line; CONTROL_CHARACTERS rejects the whole shared set and fullmatch "
        "closes the trailing-newline bypass (fix b04dc790).",
    ),
    ValidatorSite(
        "transcript export id",
        "src/core/messaging/services/export.py",
        "_is_valid_export_id",
        "private-directory filesystem path",
        "anchored",
        ("reject_control_characters", "fullmatch", "_EXPORT_ID_RE"),
        "The id drives path construction; reject_control_characters plus "
        "fullmatch close the '$'+match bypass, and the pattern carries \\Z "
        "(fix 4c5223d3).",
        module_markers=(r"\Z", "export_"),
    ),
    ValidatorSite(
        "application command name",
        "src/core/applications/commands/validation.py",
        "validate_command_name",
        "DB row + WebSocket audience",
        "anchored",
        ("fullmatch", "_is_confusable_safe"),
        "Stored verbatim and echoed in events; ASCII-only fullmatch plus the "
        "NFKC/ASCII confusables check reject confusables and controls "
        "(fix e4bfb4a1).",
    ),
    ValidatorSite(
        "application option name",
        "src/core/applications/commands/options.py",
        "validate_option",
        "DB row + command registration",
        "anchored",
        ("fullmatch", "_is_confusable_safe"),
        "Same ASCII/fullmatch/confusables contract as command names "
        "(fix e4bfb4a1).",
    ),
    ValidatorSite(
        "username",
        "src/core/auth/passwords.py",
        "validate_username",
        "DB lookup + authz",
        "anchored",
        ("fullmatch", "RejectUICharacters"),
        "fullmatch closes the trailing newline in the configured pattern; "
        "RejectUICharacters (is_control_character) rejects C0/DEL and the "
        "strict ASCII class rejects the rest.",
    ),
    # ---- reviewed bare-match cases (safe for the written reason) ----------
    ValidatorSite(
        "invite code",
        "src/api/routes/join.py",
        "validate_invite_code",
        "invite DB lookup / authz",
        "documented-loose",
        ("_bounded_text", "INVITE_CODE_PATTERN"),
        "_bounded_text applies reject_control_characters (shared set) to the "
        "value before the strict INVITE_CODE_PATTERN.match; a newline/NUL "
        "cannot reach the matcher, so the loose anchor is inert.",
    ),
    ValidatorSite(
        "migration identity table identifier",
        "src/core/migrations/migrations/004_id_identity_defaults.py",
        "_missing_identity_tables",
        "one-time migration DDL",
        "documented-loose",
        ("_IDENTIFIER", "_MISSING_IDENTITY"),
        "One-time migration over DB-introspected schema names (not attacker "
        "input); the strict identifier class excludes control characters and "
        "SQLite catalog names never carry a trailing newline.",
    ),
    ValidatorSite(
        "rate-limit route-key segment collapse",
        "src/core/ratelimit/middleware.py",
        "_collapse_id_segments",
        "rate-limit key label only",
        "documented-loose",
        ("_UUID_RE", "_HEXISH_RE", "[:64]"),
        "Only rewrites a metrics/rate-limit label and caps segment length; the "
        "result is never a filesystem path, DB key, audience or authz input.",
    ),
    ValidatorSite(
        "suite app id",
        "src/core/suite/boundaries.py",
        "validate_app_id",
        "suite capability authz",
        "documented-loose",
        ("_APP_ID_RE", "SUITE_APP_IDS", "not in SUITE_APP_IDS"),
        "Exact membership in the server-owned SUITE_APP_IDS allowlist closes "
        "the loose anchor: a newline-suffixed value cannot be in the set and "
        "is rejected.",
    ),
    ValidatorSite(
        "webhook execution URL",
        "src/core/webhooks/manager/execute.py",
        "execute_webhook_by_url",
        "webhook authz (token lookup)",
        "documented-loose",
        ("webhooks/", "[A-Za-z0-9_.-]", "group(2)"),
        "The loose '$'+match only lets a trailing newline sit OUTSIDE the "
        "captured groups; the id/token are taken from strict (\\d+) and "
        "[A-Za-z0-9_.-]+ groups, which exclude NUL/control characters, and "
        "the exact token then drives the DB lookup. A newline-suffixed URL "
        "cannot change the authenticated webhook.",
    ),
    ValidatorSite(
        "spreadsheet cell reference",
        "src/core/suite/sum/_formula_shared.py",
        "parse_cell_ref",
        "in-memory formula parse",
        "documented-loose",
        ("_CELL_RE", "_NAMED_RANGE_RE"),
        "Parses in-memory spreadsheet cell refs; not a path, DB lookup, event "
        "audience or authz decision.",
    ),
    ValidatorSite(
        "telemetry endpoint label",
        "src/core/telemetry/__init__.py",
        "_normalize_endpoint_uncached",
        "telemetry cardinality label",
        "documented-loose",
        ("_UUID_RE", "_TOKEN_RE", "_SEGMENT_PLACEHOLDERS"),
        "Folds high-cardinality segments into an analytics label; the loose "
        "match only groups labels and never gates a security decision.",
    ),
    ValidatorSite(
        "SDP version line",
        "src/core/voice/signaling/sdp.py",
        "_parse_version",
        "SDP line parser",
        "documented-loose",
        ("SDP_VERSION_PATTERN",),
        "Line-oriented parser splits on CRLF so the value cannot contain a "
        "newline; strict token class and the adapter's SDP control-char gate "
        "close the rest. Not a path/DB/audience/authz value.",
    ),
    ValidatorSite(
        "SDP origin line",
        "src/core/voice/signaling/sdp.py",
        "_parse_origin",
        "SDP line parser",
        "documented-loose",
        ("SDP_ORIGIN_PATTERN",),
        "Same CRLF line splitting + strict class as other SDP line parsers; "
        "protocol field only.",
    ),
    ValidatorSite(
        "SDP media line",
        "src/core/voice/signaling/sdp.py",
        "_parse_media",
        "SDP line parser",
        "documented-loose",
        ("SDP_MEDIA_PATTERN",),
        "Same CRLF line splitting + strict class as other SDP line parsers; "
        "protocol field only.",
    ),
    ValidatorSite(
        "SDP attribute line",
        "src/core/voice/signaling/sdp.py",
        "_parse_attribute",
        "SDP line parser",
        "documented-loose",
        ("SDP_ATTRIBUTE_PATTERN",),
        "Same CRLF line splitting + strict class as other SDP line parsers; "
        "protocol field only.",
    ),
    ValidatorSite(
        "SDP ICE credentials",
        "src/core/voice/signaling/sdp.py",
        "_validate_ice_credentials",
        "SDP security validator",
        "documented-loose",
        ("SDP_ICE_UFRAG_PATTERN", "SDP_ICE_PWD_PATTERN"),
        "Strict base64 token classes; values come from CRLF-split lines and "
        "the aiortc adapter additionally rejects SDP control characters. Not "
        "a filesystem/DB/authz value.",
    ),
    ValidatorSite(
        "SDP DTLS fingerprint",
        "src/core/voice/signaling/sdp.py",
        "_validate_fingerprint",
        "SDP security validator",
        "documented-loose",
        ("SDP_FINGERPRINT_PATTERN",),
        "Strict digest/hex class on a CRLF-split line; not a path, DB key, "
        "audience or authz decision.",
    ),
    ValidatorSite(
        "pepper hash envelope",
        "src/utils/encryption/pepper.py",
        "parse_envelope",
        "stored hash parse",
        "documented-loose",
        ("_ENVELOPE_RE", "group(1)", "group(2)"),
        "Parses an internally generated/stored password-hash envelope; the "
        "pepper-id group is strict and verification fails closed. Not user "
        "input feeding a path, DB lookup, audience or authz decision.",
    ),
)

DOCUMENTED_LOOSE = tuple(s for s in DOCUMENTED_VALIDATORS if s.mode == "documented-loose")


def _find_function(tree: ast.AST, func: str) -> ast.AST:
    matches = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func
    ]
    assert len(matches) == 1, f"expected exactly one {func!r}, found {len(matches)}"
    return matches[0]


def _assert_validator(site: ValidatorSite, source: str) -> None:
    tree = ast.parse(source)
    fn = _find_function(tree, site.func)
    body = _evidence(fn, source)
    # Check the security properties first so a regression reports the meaningful
    # invariant (anchor/control) rather than a secondary marker miss.
    if site.mode == "anchored":
        assert any(m in body for m in ANCHOR_MARKERS), (
            f"{site.label}: {site.func}() lost its whole-string anchor "
            "(expected fullmatch or \\Z)"
        )
        assert any(m in body for m in CONTROL_MARKERS), (
            f"{site.label}: {site.func}() lost its control-character rejection"
        )
    elif site.mode == "documented-loose":
        # The entry must stay honest: a documented-loose site still uses a
        # bare match, and its stated mitigation marker must remain present.
        assert "match(" in body, f"{site.label}: {site.func}() no longer a match site"
    else:  # pragma: no cover - registry typo guard
        raise AssertionError(f"{site.label}: unknown mode {site.mode!r}")
    for marker in site.markers:
        assert marker in body, f"{site.label}: marker {marker!r} missing from {site.func}()"
    for marker in site.module_markers:
        assert marker in source, f"{site.label}: module marker {marker!r} missing"


# ---------------------------------------------------------------------------
# Part 2: whole-tree sweep for NEW loose / control-permissive validators
# ---------------------------------------------------------------------------


def _sweep(commit: str, exceptions: tuple[ValidatorSite, ...]) -> list[LooseFinding]:
    reader = _make_blob_reader(commit)
    findings: list[LooseFinding] = []
    for path in _production_files(commit):
        source = reader(path)
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise AssertionError(f"cannot parse committed {path}: {exc}") from exc
        findings.extend(
            _loose_validator_candidates(
                tree, path=path, source=source, exceptions=exceptions
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Self-tests: a detector that never fires must not let the real scan pass
# ---------------------------------------------------------------------------


def test_self_test_loose_match_validator_is_flagged():
    source = textwrap.dedent(
        """
        import re
        def validate_id(x):
            return re.match(r"^id$", x)
        """
    )
    findings = _loose_validator_candidates(
        ast.parse(source), path="src/api/_synthetic.py", source=source
    )
    assert len(findings) == 1, findings
    assert "trailing newline" in findings[0].reason
    # A novel name in the same family still fails closed.
    novel = textwrap.dedent(
        """
        import re
        def _check_widget_tag(value):
            return re.match(r"^[a-z]+$", value)
        """
    )
    assert len(
        _loose_validator_candidates(
            ast.parse(novel), path="src/api/_synthetic.py", source=novel
        )
    ) == 1


def test_self_test_nul_passing_validator_is_flagged():
    source = textwrap.dedent(
        """
        import re
        def validate_name(value):
            return re.match(r"^.+$", value)
        """
    )
    findings = _loose_validator_candidates(
        ast.parse(source), path="src/api/_synthetic.py", source=source
    )
    assert len(findings) == 1, findings
    assert "NUL/control" in findings[0].reason


def test_self_test_fullmatch_and_control_rejected_are_accepted():
    source = textwrap.dedent(
        """
        import re
        from src.core.security.hardening.text import CONTROL_CHARACTERS
        def validate_id_full(x):
            return re.fullmatch(r"id", x)
        def validate_id_anchor(x):
            return re.match(r"^id\\Z", x)
        def validate_id_control(x):
            if CONTROL_CHARACTERS.search(x):
                return False
            return re.fullmatch(r"id", x)
        """
    )
    assert (
        _loose_validator_candidates(
            ast.parse(source), path="src/api/_synthetic.py", source=source
        )
        == []
    )


def test_self_test_documented_exception_suppresses_only_its_own_site():
    source = textwrap.dedent(
        """
        import re
        ID_RE = re.compile(r"^[a-z]+$")
        def validate_id(x):
            return ID_RE.match(x)
        """
    )
    tree = ast.parse(source)
    path = "src/api/_synthetic.py"
    assert len(_loose_validator_candidates(tree, path=path, source=source)) == 1
    allow = (
        ValidatorSite(
            "synthetic",
            path,
            "validate_id",
            "test",
            "documented-loose",
            ("ID_RE",),
            "reviewed safe for this test",
        ),
    )
    assert (
        _loose_validator_candidates(
            tree, path=path, source=source, exceptions=allow
        )
        == []
    )
    other = (
        ValidatorSite(
            "synthetic",
            path,
            "some_other_func",
            "test",
            "documented-loose",
            ("ID_RE",),
            "not this site",
        ),
    )
    assert len(
        _loose_validator_candidates(
            tree, path=path, source=source, exceptions=other
        )
    ) == 1


def test_self_test_anchor_and_control_assertions_fail_when_removed():
    site = ValidatorSite(
        "synthetic",
        "src/api/_synthetic.py",
        "validate_id",
        "path",
        "anchored",
        ("fullmatch", "CONTROL_CHARACTERS"),
        "test",
    )
    good = textwrap.dedent(
        """
        from src.core.security.hardening.text import CONTROL_CHARACTERS
        def validate_id(x):
            if CONTROL_CHARACTERS.search(x):
                return None
            return re.fullmatch(r"id", x)
        """
    )
    _assert_validator(site, good)
    weak_anchor = textwrap.dedent(
        """
        from src.core.security.hardening.text import CONTROL_CHARACTERS
        def validate_id(x):
            if CONTROL_CHARACTERS.search(x):
                return None
            return re.match(r"^id$", x)
        """
    )
    with pytest.raises(AssertionError, match="lost its whole-string anchor"):
        _assert_validator(site, weak_anchor)
    weak_control = textwrap.dedent(
        """
        def validate_id(x):
            return re.fullmatch(r"id", x)
        """
    )
    with pytest.raises(AssertionError, match="lost its control-character rejection"):
        _assert_validator(site, weak_control)


def test_registry_entries_require_reasons_and_real_paths():
    assert DOCUMENTED_VALIDATORS, "validator registry is empty"
    for site in DOCUMENTED_VALIDATORS:
        assert site.why.strip(), f"{site.label}: empty allowlist reason"
        assert site.rel_path.startswith("src/"), site.rel_path
        assert site.func and site.sink, site.label
        assert site.mode in {"anchored", "documented-loose"}, site.label


# ---------------------------------------------------------------------------
# Real committed tree
# ---------------------------------------------------------------------------


def test_scan_is_not_vacuous(commit_sha):
    files = _production_files(commit_sha)
    assert len(files) >= 1000, f"only {len(files)} production modules scanned"
    for required in (
        "src/core/voice/signaling/sfu/aiortc_adapter/validation.py",
        "src/core/messaging/services/export.py",
        "src/core/applications/commands/validation.py",
        "src/core/applications/commands/options.py",
        "src/core/security/hardening/text.py",
    ):
        assert required in files, f"{required} unexpectedly excluded from scan"
    # The reader really returns committed content: the three campaign fixes
    # must still be present in HEAD, not just in a contested working tree.
    reader = _make_blob_reader(commit_sha)
    assert "CONTROL_CHARACTERS.search" in reader(
        "src/core/voice/signaling/sfu/aiortc_adapter/validation.py"
    )
    assert r"export_[A-Za-z0-9_-]{16,}\Z" in reader(
        "src/core/messaging/services/export.py"
    )
    assert "not OPTION_NAME_PATTERN.fullmatch" in reader(
        "src/core/applications/commands/options.py"
    )


def test_shared_control_character_set_is_the_reference(commit_sha):
    """Bind the guard to the actual shared CONTROL_CHARACTERS definition."""
    source = _make_blob_reader(commit_sha)("src/core/security/hardening/text.py")
    for fragment in (
        "CONTROL_CHARACTERS = re.compile(",
        r"\x00-\x1f",
        r"\x7f-\x9f",
        r"\u2028",
        r"\u202a-\u202e",
    ):
        assert fragment in source, f"shared control set lost {fragment!r}"


def test_documented_validators_hardened_in_committed_tree(commit_sha):
    reader = _make_blob_reader(commit_sha)
    assert sum(s.mode == "anchored" for s in DOCUMENTED_VALIDATORS) >= 5
    for site in DOCUMENTED_VALIDATORS:
        _assert_validator(site, reader(site.rel_path))


def test_committed_tree_sweep_detector_is_non_vacuous(commit_sha):
    # With suppression removed, the detector must fire on every documented
    # loose site.  If it fires nowhere, the "no new site" test is vacuous.
    findings = _sweep(commit_sha, ())
    found = {(f.path, f.func) for f in findings}
    expected = {(s.rel_path, s.func) for s in DOCUMENTED_LOOSE}
    assert expected <= found, (
        "sweep failed to detect documented loose validators; detector is "
        f"vacuous. missing={sorted(expected - found)}"
    )


def test_documented_loose_exceptions_are_exactly_the_sweep_findings(commit_sha):
    # The allowlist stays minimal: every documented loose site still matches,
    # and every loose site in the tree is documented (no novel name).
    found = {(f.path, f.func) for f in _sweep(commit_sha, ())}
    expected = {(s.rel_path, s.func) for s in DOCUMENTED_LOOSE}
    assert found == expected, (
        "loose-validator set drifted from the documented allowlist.\n"
        f"  undocumented (NEW) loose validators: {sorted(found - expected)}\n"
        f"  stale exception entries: {sorted(expected - found)}"
    )


def test_no_new_loose_or_control_permissive_validator_in_committed_tree(commit_sha):
    offenders = _sweep(commit_sha, DOCUMENTED_LOOSE)
    assert not offenders, (
        "New loose-anchored / control-permissive security validator in "
        "production (use fullmatch or \\Z and reject the shared control set):\n  "
        + "\n  ".join(
            str(o) for o in sorted(offenders, key=lambda o: (o.path, o.lineno))
        )
    )
