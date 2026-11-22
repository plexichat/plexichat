"""SEC500 invariant: boundary ID parsers reject Unicode digits, use bounded range.

``str.isdecimal()``/``str.isdigit()`` are true for non-ASCII decimal digits
(Arabic-Indic ``١٢٣``, fullwidth ``１２３``, ...) and ``int()`` maps them onto the
same value as their ASCII spelling. That is a parser differential: an upstream
ASCII-only gate (or another language's ``\\d``) reads the homoglyph as invalid,
so an ID can be read differently on each hop. An unbounded ``int()`` additionally
accepted signs, whitespace and values past the 64-bit snowflake ceiling.

The prior fixes replaced those local checks at two choke points: the artifact
route validator (``parse_snowflake_id``) and the feature-route helper
(``parse_id``). Both now require an ASCII-only ``[0-9]+`` fullmatch and delegate
the range check to the snowflake-bounded guard. This suite locks that in with a
pure-AST scan -- no app import, config or DB -- and also asserts that no
``.isdecimal()`` call reappears anywhere in the route tree.

The detector is validated against synthetic good/bad parsers (including a
``\\d+`` regex decoy) before the real tree is trusted, and a non-vacuity test
proves the scan really sees both helpers and their guards.
"""

import ast
import os
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]
ROUTES_DIR = ROOT / "src/api/routes"
ASCII_REGEX_VAR = "_ASCII_DIGITS"
ASCII_REGEX_PATTERN = "[0-9]+"

# (relative source file, parser function, conversion style). The two styles are
# the two sanctioned ways to finish a parsed boundary ID: a local range check
# against SNOWFLAKE_MAX, or delegation to the shared snowflake-bounded guard.
BOUNDARY_PARSERS = (
    ("src/api/routes/artifacts/validation.py", "parse_snowflake_id", "local"),
    ("src/api/routes/features/common.py", "parse_id", "delegated"),
)

UNICODE_DIGIT_METHODS = frozenset({"isdecimal", "isdigit"})

# ``.isdigit()`` is still used by a handful of unrelated, length-capped route
# validators, so the whole-tree ban is scoped to ``.isdecimal()`` -- the exact
# predicate the hardened helpers used to use and which now has no call sites.
ROUTE_BANNED_METHODS = frozenset({"isdecimal"})


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _own_body_nodes(root: ast.AST):
    """Yield ``root`` and nodes on its execution path, skipping nested defs.

    Descend through statement-level nesting (``if``/``for``/``with``/``try``/...)
    but do not descend into a nested function or lambda: *defining* a helper
    that validates a boundary ID does not validate it. The broad helpers below
    remain for the Unicode-digit violation detector (kept conservative); the
    positive-evidence checks use these own-body variants so dead code cannot
    satisfy the guard.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)


def _call_attrs(function):
    return {
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def _call_names(function):
    return {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _loaded_names(function):
    return {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}


def _own_body_call_attrs(function):
    return {
        node.func.attr
        for node in _own_body_nodes(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def _own_body_call_names(function):
    return {
        node.func.id
        for node in _own_body_nodes(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _own_body_loaded_names(function):
    return {node.id for node in _own_body_nodes(function) if isinstance(node, ast.Name)}


def _has_unicode_digit_check(function) -> bool:
    # Violation detector: stay on the broad traversal so a forbidden call cannot
    # hide behind a nested callable.
    return bool(_call_attrs(function) & UNICODE_DIGIT_METHODS)


def _has_ascii_fullmatch(function) -> bool:
    return "fullmatch" in _own_body_call_attrs(function)


def _has_bounded_conversion(function, style: str) -> bool:
    names = _own_body_call_names(function)
    loaded = _own_body_loaded_names(function)
    if style == "local":
        return "int" in names and "SNOWFLAKE_MAX" in loaded
    # Delegated style: hand the ASCII-validated string to the shared guard.
    return "_parse_snowflake_id" in names and "HardeningViolation" in loaded


def _ascii_regex_pattern(tree):
    """Return the pattern string compiled into ``_ASCII_DIGITS``, if present."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(t, ast.Name) and t.id == ASCII_REGEX_VAR for t in targets
        ):
            continue
        value = node.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "compile"
            and value.args
            and isinstance(value.args[0], ast.Constant)
            and isinstance(value.args[0].value, str)
        ):
            return value.args[0].value
    return None


def _python_files(base: Path):
    for directory, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for filename in sorted(files):
            if filename.endswith(".py"):
                yield Path(directory) / filename


def _isdecimal_calls_in_routes():
    offenders = []
    for path in _python_files(ROUTES_DIR):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ROUTE_BANNED_METHODS
            ):
                offenders.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} .{node.func.attr}()"
                )
    return offenders


def _scan_tree():
    results = []
    for relative, name, style in BOUNDARY_PARSERS:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        results.append(
            (
                relative,
                name,
                style,
                _find_function(tree, name),
                _ascii_regex_pattern(tree),
            )
        )
    return results


_SCAN = _scan_tree()
_ISDECIMAL = _isdecimal_calls_in_routes()


# ---------------------------------------------------------------------------
# Self-tests: prove the classifier fires on regression parsers only.
# ---------------------------------------------------------------------------


def test_detector_flags_unicode_digit_parsing_and_accepts_ascii_fullmatch():
    regress = ast.parse(
        textwrap.dedent(
            """
            def parse(raw):
                text = str(raw).strip()
                if not text.isdecimal():
                    raise HTTPException(status_code=400)
                return int(text)
            """
        )
    )
    ascii_decoy = ast.parse(
        textwrap.dedent(
            r"""
            _ASCII_DIGITS = re.compile(r"\d+")

            def parse(raw):
                if not _ASCII_DIGITS.fullmatch(raw):
                    raise HTTPException(status_code=400)
                return int(raw)
            """
        )
    )
    good = ast.parse(
        textwrap.dedent(
            """
            def parse(raw):
                text = str(raw).strip()
                if not _ASCII_DIGITS.fullmatch(text):
                    raise HTTPException(status_code=400)
                parsed = int(text)
                if parsed > SNOWFLAKE_MAX:
                    raise HTTPException(status_code=400)
                return parsed
            """
        )
    )

    regress_fn = _find_function(regress, "parse")
    assert _has_unicode_digit_check(regress_fn)
    assert not _has_ascii_fullmatch(regress_fn)

    good_fn = _find_function(good, "parse")
    assert not _has_unicode_digit_check(good_fn)
    assert _has_ascii_fullmatch(good_fn)
    assert _has_bounded_conversion(good_fn, "local")

    # The decoy uses fullmatch but a Unicode-permissive \d+ pattern: the pattern
    # assertion below (not the call detector) is what catches that regression.
    assert _has_ascii_fullmatch(_find_function(ascii_decoy, "parse"))
    assert _ascii_regex_pattern(ascii_decoy) != ASCII_REGEX_PATTERN
    assert _ascii_regex_pattern(good) is None  # no module-level regex in this snippet


def test_dead_nested_parser_evidence_does_not_satisfy_guard():
    """A fullmatch/bounded conversion inside a dead callable is not evidence.

    Regression: ``_call_attrs``/``_call_names``/``_loaded_names`` used
    ``ast.walk(function)``, so a parser whose only ``fullmatch`` call or bounded
    conversion primitive lived in an uninvoked nested def/lambda passed the
    guard. A live check behind statement-level nesting must still count. The
    Unicode-digit detector deliberately stays on the broad scan.
    """
    dead_nested = """
def parse(raw):
    def never_called():
        if not _ASCII_DIGITS.fullmatch(raw):
            raise HTTPException(status_code=400)
        parsed = int(raw)
        if parsed > SNOWFLAKE_MAX:
            raise HTTPException(status_code=400)
        return parsed
    return raw
"""
    dead_lambda = """
def parse(raw):
    _ = lambda: (_ASCII_DIGITS.fullmatch(raw), int(raw), SNOWFLAKE_MAX)
    return raw
"""
    dead_delegated = """
def parse(raw):
    def never_called():
        if not _ASCII_DIGITS.fullmatch(raw):
            raise HTTPException(status_code=400)
        return _parse_snowflake_id(raw, HardeningViolation)
    return raw
"""
    live_nested = """
def parse(raw):
    if raw:
        if not _ASCII_DIGITS.fullmatch(raw):
            raise HTTPException(status_code=400)
        parsed = int(raw)
        if parsed > SNOWFLAKE_MAX:
            raise HTTPException(status_code=400)
        return parsed
    return raw
"""
    sources = {
        "dead_nested": (dead_nested, "local"),
        "dead_lambda": (dead_lambda, "local"),
        "dead_delegated": (dead_delegated, "delegated"),
        "live_nested": (live_nested, "local"),
    }
    fns = {
        name: _find_function(ast.parse(source), "parse")
        for name, (source, _style) in sources.items()
    }
    for name in ("dead_nested", "dead_lambda", "dead_delegated"):
        fn = fns[name]
        style = sources[name][1]
        assert not _has_ascii_fullmatch(fn), f"{name}: dead fullmatch was counted"
        assert not _has_bounded_conversion(fn, style), (
            f"{name}: dead bounded conversion was counted"
        )
    fn = fns["live_nested"]
    assert _has_ascii_fullmatch(fn), "live in-body fullmatch must still count"
    assert _has_bounded_conversion(fn, "local"), (
        "live in-body bounded conversion must still count"
    )

    # The old broad logic really was fooled, so this self-test is non-vacuous.
    for name in ("dead_nested", "dead_lambda", "dead_delegated"):
        fn = fns[name]
        assert "fullmatch" in _call_attrs(fn)
        if name == "dead_delegated":
            assert "_parse_snowflake_id" in _call_names(fn)
            assert "HardeningViolation" in _loaded_names(fn)
        else:
            assert "int" in _call_names(fn)
            assert "SNOWFLAKE_MAX" in _loaded_names(fn)


def test_ascii_pattern_extractor_reads_only_the_strict_pattern():
    assert _ascii_regex_pattern(ast.parse('_ASCII_DIGITS = re.compile(r"[0-9]+")')) == (
        ASCII_REGEX_PATTERN
    )
    assert _ascii_regex_pattern(ast.parse('_ASCII_DIGITS = re.compile(r"\\d+")')) == (
        "\\d+"
    )
    assert _ascii_regex_pattern(ast.parse("OTHER = re.compile(r'[0-9]+')")) is None


def test_parser_scan_is_not_vacuous():
    assert len(_SCAN) == len(BOUNDARY_PARSERS)
    missing = [f"{rel}:{name}" for rel, name, _, fn, _ in _SCAN if fn is None]
    assert not missing, f"boundary parsers dropped out of the scan: {missing}"
    for _, name, _, fn, pattern in _SCAN:
        assert _has_ascii_fullmatch(fn), f"{name} lost its ASCII fullmatch"


# ---------------------------------------------------------------------------
# Real-tree invariants.
# ---------------------------------------------------------------------------


def test_boundary_parsers_enforce_ascii_digits_and_bounded_conversion():
    offenders = []
    for relative, name, style, fn, pattern in _SCAN:
        if pattern != ASCII_REGEX_PATTERN:
            offenders.append(f"{relative}: {ASCII_REGEX_VAR} pattern is {pattern!r}")
        if _has_unicode_digit_check(fn):
            offenders.append(f"{relative}:{name} uses .isdecimal()/.isdigit()")
        if not _has_ascii_fullmatch(fn):
            offenders.append(f"{relative}:{name} lost its ASCII fullmatch")
        if not _has_bounded_conversion(fn, style):
            offenders.append(f"{relative}:{name} lost its bounded conversion")
    assert not offenders, (
        "Boundary ID parser regressed to a permissive/unbounded conversion:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_no_route_module_calls_isdecimal():
    assert not _ISDECIMAL, (
        "Unicode-digit .isdecimal() reintroduced in a route boundary:\n  "
        + "\n  ".join(sorted(_ISDECIMAL))
    )
