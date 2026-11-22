"""Guard: user/admin-controlled regex compiles in the moderation core must be vetted.

Follow-up to the ReDoS campaign: ``782335ac`` taught
:func:`src.core.automod.rules.regex._has_redos_risk` to also reject flat
sequential-wildcard chains (``.*a.*a.*b``), and ``e4bfb4a1`` rejected Unicode
confusables in command names. A fix to one vetting entry point is only useful
if no *other* code path takes a stored/request-supplied pattern straight into a
backtracking matcher. This guard makes that invariant structural.

It scans the four moderation-critical subtrees of the production ``src/`` tree
(``automod``, ``antispam``, ``auth/blacklist``, ``messaging``) from COMMITTED
HEAD blobs (``git cat-file --batch`` against a pinned SHA), not the working
tree: those files are contested by parallel agents whose staged copies may be
stale. It enumerates every call that compiles a *non-literal* pattern --
``re.compile``, ``_safe_regex.compile``/``re2*.compile``, and ``fnmatch`` on a
pattern variable -- and accepts a site only when it is:

* vetted in the enclosing function via ``_has_redos_risk`` /
  ``_has_sequential_wildcard_redos_risk`` (the sequential-wildcard check is
  pinned separately below);
* RE2-backed (``re2``/``re2_module``, or ``_safe_regex`` aliased to ``re2``);
* an inherently bounded matcher -- the pattern is assembled from
  ``re.escape()`` literals (linear-time alternation of fixed strings); or
* listed in ``DOCUMENTED_SITES`` with a concrete, reviewed reason.

Anything else fails closed. Self-tests prove the detector flags a bare
``re.compile(user_pattern)`` and a dynamic ``fnmatch.fnmatchcase``, while a
vetted compile, an RE2 compile, a static literal, and an escaped-literal
compile are all accepted. The pinned HEAD must have zero undocumented sites.
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

# Exactly the subtrees the ReDoS campaign covers. The whole file is included
# for the single-file target (auth/blacklist.py); the others are directories.
TARGET_PATHS = (
    "src/core/automod",
    "src/core/antispam",
    "src/core/auth/blacklist.py",
    "src/core/messaging",
)

# Test/selftest/cli trees are not request paths. Also skip any file named
# test_* inside a production directory (in-tree regression tests).
EXCLUDED_PATH_COMPONENTS = frozenset({"tests", "selftest", "cli"})

RE2_RECEIVERS = frozenset({"re2", "re2_module", "re2_mod", "google_re2"})
COMPILE_RECEIVERS = frozenset({"re", "_safe_regex"}) | RE2_RECEIVERS
FN_MATCH_FUNCS = frozenset({"fnmatch", "fnmatchcase"})
VETTING_FUNCS = ("_has_redos_risk", "_has_sequential_wildcard_redos_risk")


# (path, enclosing function, method, pattern expression) -> reviewed reason.
# This is an allowlist of the *existing* dynamic sites in the pinned tree; a
# newly introduced user-pattern compile is absent from it and fails closed.
# Entries marked "tracked" are known pre-existing gaps kept visible here until
# their dedicated fix lands -- they are not a general safety stamp.
DOCUMENTED_SITES: dict[tuple[str, str, str, str], str] = {
    (
        "src/core/antispam/detector.py",
        "_check_message",
        "fnmatch.fnmatchcase",
        "canonical_pattern",
    ): (
        "TRACKED pre-existing sink: recipient-stored dm_spam_filters glob. At "
        "this HEAD it still uses fnmatch.fnmatchcase, whose glob->`.*` "
        "translation backtracks exponentially on `*a` chains. A linear-time "
        "`_safe_fnmatchcase` replacement is in flight (see "
        "src/tests/antispam/test_stored_config_redos_hardening.py); pattern "
        "length is capped at 512 and canonical content at 10k bytes. This "
        "entry exists only so the guard can fail closed on NEW sites."
    ),
    (
        "src/core/auth/blacklist.py",
        "add_pattern",
        "_safe_regex.compile",
        "pattern",
    ): (
        "RE2-backed: `_safe_regex` is `importlib.import_module('re2')` in "
        "this module, so admin-supplied regexes are compiled by the linear "
        "engine at write time (stdlib `re` only when re2 is unavailable; "
        "usernames are length-bounded)."
    ),
    (
        "src/core/auth/blacklist.py",
        "_get_plan",
        "_safe_regex.compile",
        "f'(?i){pattern}'",
    ): (
        "RE2-backed: same `_safe_regex` alias as add_pattern; stored regexes "
        "already passed the add-time compile and are matched against bounded "
        "usernames."
    ),
    (
        "src/core/automod/rules/links.py",
        "__init__",
        "re.compile",
        "custom_pattern",
    ): (
        "VETTED: the admin-supplied custom invite pattern passes through "
        "`_has_redos_risk(custom_pattern)` (which now includes the "
        "sequential-wildcard check) and a 500-char length cap before this "
        "stdlib fallback compile; unsafe shapes fall back to the static "
        "default."
    ),
    (
        "src/core/automod/rules/links.py",
        "__init__",
        "re.compile",
        "f'\\\\b([a-zA-Z0-9]{{{self._code_length}}})\\\\b'",
    ): (
        "BOUNDED config: `self._code_length` is clamped by `bounded_int` to "
        "[1, MAX_CODE_LENGTH]; the pattern is a fixed word-boundary shape "
        "with one bounded repetition, not attacker-chosen regex syntax."
    ),
    (
        "src/core/automod/rules/links.py",
        "validate_config",
        "re.compile",
        "pattern",
    ): (
        "VETTED: `validate_config` calls `_has_redos_risk(pattern)` and caps "
        "length before this compile; same vetting as the runtime path."
    ),
    (
        "src/core/automod/rules/regex.py",
        "__init__",
        "re.compile",
        "pattern_str",
    ): (
        "VETTED fallback: reached only when RE2 rejects the pattern, and only "
        "after `if _has_redos_risk(pattern_str): continue` (now including "
        "sequential-wildcard chains); length and total-char budgets apply."
    ),
    (
        "src/core/automod/rules/regex.py",
        "__init__",
        "re2_module.compile",
        "pattern_str",
    ): (
        "RE2-backed: primary engine for admin-supplied automod patterns; RE2 "
        "is linear-time by construction."
    ),
    (
        "src/core/automod/rules/regex.py",
        "validate_config",
        "re2_module.compile",
        "pattern_str",
    ): (
        "RE2-backed: config-validation probe using the same linear engine."
    ),
    (
        "src/core/automod/rules/regex.py",
        "validate_config",
        "re.compile",
        "pattern_str",
    ): (
        "VETTED fallback in validate_config: both stdlib compiles in this "
        "function are preceded by `_has_redos_risk(pattern_str)` (now with "
        "the sequential-wildcard check), so dangerous shapes are rejected."
    ),
    (
        "src/core/automod/rules/repeated.py",
        "__init__",
        "re.compile",
        "pattern",
    ): (
        "BOUNDED config: `max_repeats` is clamped by `bounded_int` to "
        "[2, MAX_REPEATS]; the pattern is a single-character backreference "
        "`(.)\\1{n,}` whose only variable is that bounded count -- linear "
        "over the bounded canonical view, not attacker-authored regex."
    ),
    (
        "src/core/messaging/content.py",
        "_compile_word_pattern",
        "re.compile",
        "'|'.join((re.escape(w) for w in words))",
    ): (
        "INHERENTLY BOUNDED: every word is `re.escape()`d into a literal, so "
        "the alternation is a linear-time Aho-style literal union; the "
        "pattern cannot contain a quantifier or group from user data."
    ),
    (
        "src/core/messaging/content.py",
        "_load_config",
        "re.compile",
        "pattern",
    ): (
        "TRACKED pre-existing sink: `nsfw_patterns` comes from deployment "
        "YAML (`utils.config.get('messaging')`), not from an admin HTTP route "
        "or stored DB row -- no API writes this key (only `automod` config is "
        "writable via admin/moderation.py, and that path is vetted). Static, "
        "operator-controlled patterns only; not request-reachable."
    ),
    (
        "src/core/messaging/services/content_filter.py",
        "_load_config",
        "re.compile",
        "p",
    ): (
        "TRACKED pre-existing sink: same deployment-YAML `nsfw_patterns` as "
        "messaging/content.py; operator-controlled, not request-writable. "
        "Default patterns are static literals."
    ),
    (
        "src/core/messaging/services/content_filter.py",
        "_load_config",
        "re.compile",
        "'\\\\b(' + '|'.join(escaped_words) + ')\\\\b'",
    ): (
        "INHERENTLY BOUNDED: `escaped_words` is built with `re.escape()` from "
        "the configured profanity word set; the only regex metacharacters "
        "are the fixed `\\b(...)\\b` wrapper."
    ),
    (
        "src/core/messaging/services/content_filter.py",
        "_apply_user_filters",
        "re.compile",
        "'\\\\b(' + '|'.join(escaped_words) + ')\\\\b'",
    ): (
        "INHERENTLY BOUNDED: per-user blocked words are `re.escape()`d before "
        "this alternation; bounded LRU caches the compiled result by word "
        "set. User data can only contribute literal characters."
    ),
}


@dataclass(frozen=True)
class CompileSite:
    path: str
    lineno: int
    func: str
    method: str
    pattern_expr: str
    func_source: str
    module_re2_alias: bool

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.path, self.func, self.method, self.pattern_expr)

    @property
    def location(self) -> str:
        return f"{self.path}:{self.lineno}"


# ---------------------------------------------------------------------------
# Committed-tree plumbing (identical approach to the other committed-tree guards)
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
        pos += size + 1
    return out


def _is_excluded(path: str) -> bool:
    parts = path.split("/")
    if Path(path).name.startswith("test_"):
        return True
    return any(p in EXCLUDED_PATH_COMPONENTS for p in parts[:-1])


def _production_files(commit: str) -> tuple[str, ...]:
    proc = _git("ls-tree", "-r", "--name-only", commit, "--", *TARGET_PATHS)
    assert proc.returncode == 0, f"cannot list target subtrees: {proc.stderr!r}"
    files = tuple(
        p
        for p in proc.stdout.decode("utf-8", "replace").splitlines()
        if p.endswith(".py") and not _is_excluded(p)
    )
    assert len(files) >= 40, f"only {len(files)} production modules in target subtrees"
    return files


# ---------------------------------------------------------------------------
# AST detector (also used by self-tests on synthetic modules)
# ---------------------------------------------------------------------------


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


def _extract_sites(source: str, path: str) -> list[CompileSite]:
    """Enumerate calls that compile a NON-literal (thus user/supplied) pattern."""
    tree = ast.parse(source, filename=path)
    lines = source.splitlines()
    alias_re2 = bool(
        re.search(r"import_module\(\s*['\"]re2['\"]\s*\)", source)
    ) and bool(re.search(r"\b_safe_regex\s*=", source))

    sites: list[CompileSite] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        func = node.func
        receiver = ast.unparse(func.value)

        pattern_arg: ast.AST | None = None
        method = ""
        if func.attr == "compile" and receiver in COMPILE_RECEIVERS:
            method = f"{receiver}.compile"
            pattern_arg = node.args[0] if node.args else None
            for kw in node.keywords:
                if kw.arg == "pattern":
                    pattern_arg = kw.value
        elif func.attr in FN_MATCH_FUNCS and receiver == "fnmatch":
            method = f"fnmatch.{func.attr}"
            pattern_arg = node.args[1] if len(node.args) > 1 else None
        if pattern_arg is None:
            continue

        # A literal string is a static pattern, not a user/admin-controlled one.
        if isinstance(pattern_arg, ast.Constant) and isinstance(
            pattern_arg.value, str
        ):
            continue

        owner = _enclosing_function(tree, node)
        if owner is not None:
            func_source = "\n".join(
                lines[owner.lineno - 1 : owner.end_lineno or owner.lineno]
            )
            func_name = owner.name
        else:
            func_source = source
            func_name = "<module>"

        sites.append(
            CompileSite(
                path=path,
                lineno=node.lineno,
                func=func_name,
                method=method,
                pattern_expr=ast.unparse(pattern_arg),
                func_source=func_source,
                module_re2_alias=alias_re2,
            )
        )
    return sites


def _collect_sites(commit: str) -> list[CompileSite]:
    files = _production_files(commit)
    blobs = _read_blobs(commit, files)
    sites: list[CompileSite] = []
    for path, source in blobs.items():
        sites.extend(_extract_sites(source, path))
    return sites


# ---------------------------------------------------------------------------
# Classification -- four independent acceptance paths, all auditable
# ---------------------------------------------------------------------------


def _pattern_vars(expr: str) -> set[str]:
    """Identifiers that feed the compiled pattern (for vetting-arg matching)."""
    try:
        node = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
    return names


def _vetted(site: CompileSite) -> bool:
    """A ReDoS vetting call on this exact pattern variable precedes the compile."""
    for var in _pattern_vars(site.pattern_expr):
        for vet in VETTING_FUNCS:
            if re.search(
                r"\b" + re.escape(vet) + r"\s*\(\s*" + re.escape(var) + r"\b",
                site.func_source,
            ):
                return True
    return False


def _re2_backed(site: CompileSite) -> bool:
    if site.method.split(".")[0] in RE2_RECEIVERS:
        return True
    return site.method == "_safe_regex.compile" and site.module_re2_alias


def _escaped_literals(site: CompileSite) -> bool:
    """Pattern is a union of re.escape()d literals -> no user regex syntax."""
    if "re.escape(" in site.pattern_expr:
        return True
    return "re.escape(" in site.func_source and "escaped" in site.pattern_expr.lower()


def _documented_reason(site: CompileSite, documented=DOCUMENTED_SITES) -> str | None:
    return documented.get(site.key)


def _offenders(sites: list[CompileSite], documented=DOCUMENTED_SITES) -> list[str]:
    bad: list[str] = []
    for site in sites:
        if (
            _vetted(site)
            or _re2_backed(site)
            or _escaped_literals(site)
            or _documented_reason(site, documented)
        ):
            continue
        bad.append(
            f"{site.location} [{site.func}] {site.method} on "
            f"{site.pattern_expr!r}: user/admin-controlled pattern compiled "
            "without ReDoS vetting, RE2, a bounded matcher, or a documented "
            "safe reason"
        )
    return bad


# ---------------------------------------------------------------------------
# Non-vacuity self-tests: a detector that never fires must not pass the scan
# ---------------------------------------------------------------------------


def _synthetic_sites(source: str) -> list[CompileSite]:
    return _extract_sites(textwrap.dedent(source), "src/_synthetic/gate.py")


def test_self_test_flags_unvetted_user_pattern_compile():
    source = """
    import re

    def check(user_pattern, content):
        compiled = re.compile(user_pattern)
        return bool(compiled.search(content))
    """
    sites = _synthetic_sites(source)
    assert len(sites) == 1
    site = sites[0]
    assert site.method == "re.compile" and site.pattern_expr == "user_pattern"
    assert not _vetted(site)
    assert not _re2_backed(site)
    assert not _escaped_literals(site)
    assert _offenders(sites)  # <-- the regression this guard exists to catch


def test_self_test_flags_dynamic_fnmatch_glob():
    source = """
    import fnmatch

    def check(content, user_glob):
        return fnmatch.fnmatchcase(content, user_glob)
    """
    sites = _synthetic_sites(source)
    assert len(sites) == 1
    assert sites[0].method == "fnmatch.fnmatchcase"
    assert sites[0].pattern_expr == "user_glob"
    assert _offenders(sites)


def test_self_test_accepts_vetted_compile():
    source = """
    import re
    from src.core.automod.rules.regex import _has_redos_risk

    def check(user_pattern):
        if _has_redos_risk(user_pattern):
            return None
        return re.compile(user_pattern)
    """
    sites = _synthetic_sites(source)
    assert len(sites) == 1
    assert _vetted(sites[0])
    assert _offenders(sites) == []


def test_self_test_accepts_re2_and_safe_regex_alias():
    direct = """
    import re2

    def check(user_pattern):
        return re2.compile(user_pattern)
    """
    aliased = """
    import importlib
    import re
    _safe_regex = importlib.import_module("re2")

    def check(user_pattern):
        return _safe_regex.compile(user_pattern)
    """
    assert _offenders(_synthetic_sites(direct)) == []
    alias_sites = _synthetic_sites(aliased)
    assert len(alias_sites) == 1 and alias_sites[0].module_re2_alias
    assert _re2_backed(alias_sites[0])
    assert _offenders(alias_sites) == []


def test_self_test_static_literal_is_not_even_a_candidate():
    source = """
    import re

    def check(content):
        return bool(re.match(r"^ok$", content))
    """
    assert _synthetic_sites(source) == []


def test_self_test_accepts_escaped_literal_alternation():
    source = """
    import re

    def check(words, content):
        return re.compile("|".join(re.escape(w) for w in words)).search(content)
    """
    sites = _synthetic_sites(source)
    assert len(sites) == 1
    assert _escaped_literals(sites[0])
    assert _offenders(sites) == []


def test_self_test_documentation_is_required_to_allow_an_unvetted_site():
    source = """
    import re

    def check(config_pattern):
        return re.compile(config_pattern)
    """
    sites = _synthetic_sites(source)
    assert _offenders(sites)
    documented = {
        (
            "src/_synthetic/gate.py",
            "check",
            "re.compile",
            "config_pattern",
        ): "reviewed: operator-only deployment config"
    }
    assert _offenders(sites, documented) == []


def test_self_test_vetting_of_a_different_variable_does_not_cover_the_sink():
    source = """
    import re
    from src.core.automod.rules.regex import _has_redos_risk

    def check(safe_pattern, user_pattern):
        if _has_redos_risk(safe_pattern):
            return None
        return re.compile(user_pattern)
    """
    sites = _synthetic_sites(source)
    assert len(sites) == 1
    assert not _vetted(sites[0])
    assert _offenders(sites)


# ---------------------------------------------------------------------------
# Real committed tree
# ---------------------------------------------------------------------------


def test_scan_is_not_vacuous(commit_sha):
    sites = _collect_sites(commit_sha)
    assert len(sites) >= 15, f"only {len(sites)} dynamic compile sites enumerated"
    paths = {s.path for s in sites}
    for required in (
        "src/core/automod/rules/regex.py",
        "src/core/automod/rules/links.py",
        "src/core/automod/rules/repeated.py",
        "src/core/antispam/detector.py",
        "src/core/auth/blacklist.py",
        "src/core/messaging/content.py",
        "src/core/messaging/services/content_filter.py",
    ):
        assert required in paths, f"{required} unexpectedly absent from the sweep"
    assert sum(_vetted(s) for s in sites) >= 4
    assert sum(_re2_backed(s) for s in sites) >= 4
    assert sum(_escaped_literals(s) for s in sites) >= 3
    assert any(s.method == "fnmatch.fnmatchcase" for s in sites)


# Each ReDoS vetting fix that the guard relies on must be pinned in the
# committed blob, with a helper marker distinctive to that fix and proof that
# it is actually CALLED by the public entry point (not dead code). Reverting
# either commit -- or deleting just its call -- must fail the parametrized pin
# below.
REDOS_PINNED_FIXES = (
    (
        "782335ac",
        "_has_sequential_wildcard_redos_risk",
        ("MAX_SEQUENTIAL_WILDCARDS",),
    ),
    (
        "fc2fa880",
        "_has_flat_quantifier_chain_redos_risk",
        ("_flat_atom_descriptor", "_atoms_overlap"),
    ),
)


def _assert_pinned_redos_fix(
    blob: str, func: str, markers: tuple[str, ...]
) -> None:
    """A committed ReDoS fix is present, has its markers, and is wired in."""
    assert f"def {func}(" in blob, f"{func} definition missing from HEAD blob"
    for marker in markers:
        assert marker in blob, f"{func} marker {marker!r} missing from HEAD blob"
    tail = blob.split("def _has_redos_risk(", 1)
    assert len(tail) == 2, "_has_redos_risk missing from HEAD blob"
    body = tail[1].split("\ndef ", 1)[0]
    assert f"{func}(pattern_str)" in body, (
        f"{func} is defined but not wired into _has_redos_risk"
    )


@pytest.mark.parametrize(("fix_commit", "func", "markers"), REDOS_PINNED_FIXES)
def test_head_pins_redos_vetting_fixes(commit_sha, fix_commit, func, markers):
    """Both 782335ac and fc2fa880 must be in the blobs we scan, and live."""
    blob = _read_blobs(commit_sha, ("src/core/automod/rules/regex.py",))[
        "src/core/automod/rules/regex.py"
    ]
    _assert_pinned_redos_fix(blob, func, markers)


def test_self_test_redos_pin_detects_a_reverted_fix():
    """The pin must fail when a fix's definition, marker, or wiring is gone."""
    synthetic = "\n".join(
        (
            "MAX_SEQUENTIAL_WILDCARDS = 3",
            "def _flat_atom_descriptor(pattern_str, i): return None",
            "def _atoms_overlap(a, b): return False",
            "def _has_sequential_wildcard_redos_risk(pattern_str): return False",
            "def _has_flat_quantifier_chain_redos_risk(pattern_str): return False",
            "def _has_redos_risk(pattern_str):",
            "    if _has_sequential_wildcard_redos_risk(pattern_str): return True",
            "    if _has_flat_quantifier_chain_redos_risk(pattern_str): return True",
            "    return False",
            "",
        )
    )
    # Baseline: the pin accepts a blob that genuinely contains both fixes.
    for _fix_commit, func, markers in REDOS_PINNED_FIXES:
        _assert_pinned_redos_fix(synthetic, func, markers)
    # Non-vacuity: deleting either fix (or just its wiring) must be caught.
    for _fix_commit, func, markers in REDOS_PINNED_FIXES:
        call_line = f"    if {func}(pattern_str): return True"
        unwired = "\n".join(
            line for line in synthetic.splitlines() if line != call_line
        )
        with pytest.raises(AssertionError):
            _assert_pinned_redos_fix(unwired, func, markers)
        no_def = "\n".join(
            line
            for line in unwired.splitlines()
            if not line.startswith(f"def {func}(")
        )
        with pytest.raises(AssertionError):
            _assert_pinned_redos_fix(no_def, func, markers)
        no_marker = "\n".join(
            line
            for line in synthetic.splitlines()
            if not any(m in line for m in markers)
        )
        with pytest.raises(AssertionError):
            _assert_pinned_redos_fix(no_marker, func, markers)


def test_no_undocumented_user_pattern_compile(commit_sha):
    offenders = _offenders(_collect_sites(commit_sha))
    assert not offenders, (
        "User/admin-controlled regex compiles without vetting/RE2/bounds:\n  "
        + "\n  ".join(sorted(offenders))
    )
