"""Shared tokens, parser, and coercion helpers for the formula engine."""

import datetime
import decimal
import functools
import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

"""Bounded formula evaluator for PlexiSum.

Formulas are stored as raw text (after the ``=``) in ``sum_cells.formula``
and evaluated on read against a resolver supplied by the caller. The engine
supports arithmetic, comparisons, string concatenation, cell references,
ranges, cross-sheet references, and a compact set of common functions.

Every evaluation is bounded: a maximum dependency count, a maximum call
depth, and cycle detection guarantee a single malicious or accidental formula
cannot stall a viewport read. Errors propagate as ``#...`` strings.

The engine is deliberately dependency-free and deterministic so it can be
unit-tested in isolation and reused by viewport reads, CSV export, and sort.
The one exception to the determinism is TODAY/NOW, which read the clock.
"""


# Sentinel error codes returned (not raised) to callers as strings.
DIV_ZERO = "#DIV/0!"
VALUE_ERROR = "#VALUE!"
REF_ERROR = "#REF!"
NAME_ERROR = "#NAME?"
NUM_ERROR = "#NUM!"
CYCLE_ERROR = "#CYCLE!"
N_A_ERROR = "#N/A"
ERROR_CODES = (
    DIV_ZERO,
    VALUE_ERROR,
    REF_ERROR,
    NAME_ERROR,
    NUM_ERROR,
    CYCLE_ERROR,
    N_A_ERROR,
)

MAX_DEPENDENCIES = 200_000
MAX_CALL_DEPTH = 100

# Per-function resource bounds (mirrors the ROUND precision clamp: a single
# formula argument must not be able to stall the evaluating thread or force a
# multi-gigabyte allocation on a read path).
# REPT result length cap (Excel's own cell limit is 32,767 characters).
MAX_REPT_CHARS = 32_767
# Widest start/end span NETWORKDAYS will iterate day by day (~547 years).
MAX_NETWORKDAYS_SPAN_DAYS = 200_000
# Largest work-day offset WORKDAY will walk (~273 years of workdays).
MAX_WORKDAY_OFFSET_DAYS = 100_000
# Excel's own maximum date serial (9999-12-31); serials beyond this are #NUM!.
MAX_DATE_SERIAL = 2_958_465

# Excel's epoch: serial 1 == 1900-01-01, with the classic 1900 leap-year bug
# folded into 1899-12-30 (the convention most spreadsheet engines follow).
# Dates are stored and evaluated as serial numbers.
_DATE_EPOCH = datetime.date(1899, 12, 30)

# A complete signed decimal, optionally in scientific notation. Used both by
# VALUE/NUMBERVALUE and by the aggregates, which coerce a text *argument* that
# reads as a whole number rather than parsing a prefix of it.
# ASCII digits only: Python's ``\d`` (and ``float()``) also accept the
# Unicode decimal digits of other scripts -- ``float("\u0661\u0662\u0663")`` is
# 123 -- while JavaScript's ``\d`` and ``Number()`` recognise only ASCII, so the
# client engine and Excel both read "\u0661\u0662\u0663" as text. Spelling the
# class out keeps a number the same number on both engines, and because the
# pattern gates every ``float()`` call the coercion cannot see the rest.
NUMERIC_TEXT_RE = re.compile(
    r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z"
)

# A resolver returns the raw cell as a dict with at least
# ``value_type`` and ``value`` (and optionally ``formula``), or ``None``.
CellResolver = Callable[[Optional[str], int, int], Optional[Dict[str, Any]]]


def is_error(value: Any) -> bool:
    return isinstance(value, str) and value in ERROR_CODES


# ── tokenizer ────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)
  | (?P<str>"(?:[^"]|"")*")
    # The trailing boundary matters: without it a function name containing a
    # digit (BIN2DEC, ATAN2, SUMX2MY2) matched as the cell reference "BIN2"
    # followed by an identifier, so those names could never be called and
    # reported #VALUE! (a parse error) instead of #NAME?.
  |    (?P<ref>(?:(?:'[^']*'|[A-Za-z_][A-Za-z0-9_]*)!)?\$?[A-Za-z]+\$?[0-9]+(?::(?:(?:'[^']*'|[A-Za-z_][A-Za-z0-9_]*)!)?\$?[A-Za-z]+\$?[0-9]+)?)(?![A-Za-z0-9_])
  | (?P<op><=|>=|<>|=|<|>|\+|-|\*|/|\^|&|%)
  | (?P<lparen>\()
  | (?P<rparen>\))
  | (?P<comma>,)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)
    """,
    re.VERBOSE,
)


# A bare ``ATAN2``-shaped token is both a legal cell reference and, in Excel, a
# function name. Which one it is depends on what follows it, so the tokenizer
# keeps this pattern to hand and lets the caller decide.
_SIMPLE_REF_RE = re.compile(r"[A-Za-z]+[0-9]+")


class _Token:
    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value: Any) -> None:
        self.kind = kind
        self.value = value

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Token({self.kind}, {self.value!r})"


def _tokenize(text: str) -> List[_Token]:
    tokens: List[_Token] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise ValueError(f"Unexpected character at position {pos}")
        pos = match.end()
        kind = next(
            (name for name, value in match.groupdict().items() if value is not None),
            None,
        )
        if kind == "ws":
            continue
        raw = match.group()
        if kind == "num":
            tokens.append(_Token("num", float(raw)))
        elif kind == "str":
            tokens.append(_Token("str", raw[1:-1].replace('""', '"')))
        elif kind == "ref":
            # A token that could be either -- ``ATAN2`` is a valid cell reference
            # *and* a function -- is a call when an opening parenthesis follows
            # it, exactly as Excel resolves it. Without this a digit-bearing
            # function name could never be called: it parsed as a reference and
            # the stray parenthesis made the formula a parse error.
            following = text[match.end() :].lstrip()
            if following.startswith("(") and _SIMPLE_REF_RE.fullmatch(raw):
                tokens.append(_Token("id", raw.upper()))
            else:
                tokens.append(_Token("ref", raw))
        elif kind == "id":
            upper = raw.upper()
            if upper in ("TRUE", "FALSE"):
                # ``TRUE``/``FALSE`` are literals when bare but zero-argument
                # functions when called (``TRUE()``), exactly as Excel and the
                # client engine resolve them. Without the lookahead the call
                # form tokenized as a boolean followed by a stray parenthesis
                # and failed the whole formula with a parse error.
                following = text[match.end() :].lstrip()
                if following.startswith("("):
                    tokens.append(_Token("id", upper))
                elif upper == "TRUE":
                    tokens.append(_Token("bool", True))
                else:
                    tokens.append(_Token("bool", False))
            else:
                tokens.append(_Token("id", upper))
        elif kind == "lparen":
            tokens.append(_Token("lparen", "("))
        elif kind == "rparen":
            tokens.append(_Token("rparen", ")"))
        elif kind == "comma":
            tokens.append(_Token("comma", ","))
        else:
            tokens.append(_Token("op", raw))
    tokens.append(_Token("eof", None))
    return tokens


# ── cell reference parsing ───────────────────────────────────────────────────

_CELL_RE = re.compile(
    r"^(?:(?:'([^']*)'|([A-Za-z_][A-Za-z0-9_]*))!)?\$?([A-Za-z]+)\$?([0-9]+)$"
)

# Named range pattern (e.g., "MyRange", "Sheet1!MyRange")
_NAMED_RANGE_RE = re.compile(
    r"^(?:(?:'([^']*)'|([A-Za-z_][A-Za-z0-9_]*))!)?([A-Za-z_][A-Za-z0-9_]*)$"
)


def parse_cell_ref(
    text: str, named_ranges: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[str], int, int]:
    """Parse ``Sheet2!A1`` or ``'Sheet 1'!A1`` -> (sheet_name, row, col).

    Absolute ``$`` markers are ignored, and single-quoted sheet names (the
    Excel convention for names containing spaces or punctuation, e.g. the
    default ``Sheet 1``) are accepted.

    If *named_ranges* is provided and the text matches a named range,
    the range's resolved reference is returned instead.
    """
    # First, try to match as a cell reference
    match = _CELL_RE.match(text)
    if match:
        quoted, unquoted, letters, row_text = match.groups()
        sheet = quoted if quoted is not None else unquoted
        column = 0
        for char in letters.upper():
            column = column * 26 + (ord(char) - 64)
        return sheet, int(row_text) - 1, column - 1
    # Try to match as a named range
    if named_ranges:
        named_match = _NAMED_RANGE_RE.match(text)
        if named_match:
            quoted, unquoted, name = named_match.groups()
            sheet = quoted if quoted is not None else unquoted
            # Look up the named range
            range_key = name.upper()
            if range_key in named_ranges:
                range_ref = named_ranges[range_key]
                if isinstance(range_ref, str):
                    # Recursive call to parse the range reference
                    return parse_cell_ref(range_ref, named_ranges)
    raise ValueError(f"Invalid cell reference {text!r}")


# ── parser ───────────────────────────────────────────────────────────────────


class _Parser:
    def __init__(self, tokens: List[_Token]) -> None:
        self._tokens = tokens
        self._index = 0

    def _peek(self) -> _Token:
        return self._tokens[self._index]

    def _advance(self) -> _Token:
        token = self._tokens[self._index]
        if token.kind != "eof":
            self._index += 1
        return token

    def _expect_op(self, op: str) -> None:
        # Matching by value (not kind) so ``(`` works both as a grouping
        # parenthesis and as a function-call delimiter.
        token = self._peek()
        if token.value != op:
            raise ValueError(f"Expected {op!r}")

    def parse(self) -> Any:
        node = self._expr()
        if self._peek().kind != "eof":
            raise ValueError("Unexpected trailing input")
        return node

    def _expr(self) -> Any:
        # comparison / equality (lowest precedence)
        node = self._additive()
        while self._peek().kind == "op" and self._peek().value in (
            "<",
            ">",
            "<=",
            ">=",
            "=",
            "<>",
        ):
            op = self._advance().value
            right = self._additive()
            node = ("cmp", op, node, right)
        return node

    def _additive(self) -> Any:
        node = self._multiplicative()
        while self._peek().kind == "op" and self._peek().value in ("+", "-", "&"):
            op = self._advance().value
            right = self._multiplicative()
            node = (op, node, right)
        return node

    def _multiplicative(self) -> Any:
        node = self._power()
        while self._peek().kind == "op" and self._peek().value in ("*", "/"):
            op = self._advance().value
            right = self._power()
            node = (op, node, right)
        return node

    def _power(self) -> Any:
        # ``^`` is left-associative in Excel: ``2^3^2`` is ``(2^3)^2`` (64),
        # not ``2^(3^2)`` (512). A recursive call on the right-hand side made
        # the server right-associative while the client engine -- and Excel --
        # evaluate left to right, so the same formula answered differently
        # depending on which engine saw it.
        node = self._unary()
        while self._peek().kind == "op" and self._peek().value == "^":
            self._advance()
            right = self._unary()
            node = ("^", node, right)
        return node

    def _unary(self) -> Any:
        token = self._peek()
        if token.kind == "op" and token.value in ("+", "-"):
            self._advance()
            operand = self._unary()
            return ("neg", operand) if token.value == "-" else operand
        node = self._postfix()
        if self._peek().kind == "op" and self._peek().value == "%":
            self._advance()
            return ("%", node)
        return node

    def _postfix(self) -> Any:
        token = self._peek()
        if token.kind == "num":
            self._advance()
            return ("num", token.value)
        if token.kind == "str":
            self._advance()
            return ("str", token.value)
        if token.kind == "bool":
            self._advance()
            return ("bool", token.value)
        if token.kind == "ref":
            self._advance()
            return ("ref", token.value)
        if token.kind == "id":
            self._advance()
            if self._peek().value != "(":
                # A bare name with no call is Excel's named-range reference.
                # Parsing it as one lets an unresolvable name answer the
                # client engine's #REF! instead of failing the whole formula
                # with a parse error, and a defined name resolves through the
                # named-range table the same way a cell reference does.
                return ("ref", token.value)
            self._advance()
            args: List[Any] = []
            if self._peek().kind != "rparen":
                while True:
                    # An omitted argument -- ``SUM(1,)`` or ``SUM(,1)`` -- is
                    # Excel's "missing" operand, which coerces exactly like an
                    # empty cell (0 as a number, "" as text). Recording it as a
                    # blank node lets the evaluator answer it as one instead of
                    # failing the whole formula with a parse error.
                    if self._peek().kind == "comma":
                        args.append(("blank",))
                    else:
                        args.append(self._expr())
                    if self._peek().kind != "comma":
                        break
                    self._advance()
                    if self._peek().kind == "rparen":
                        args.append(("blank",))
                        break
            self._expect_op(")")
            self._advance()
            return ("call", token.value, args)
        if token.kind == "lparen":
            self._advance()
            node = self._expr()
            self._expect_op(")")
            self._advance()
            return node
        raise ValueError(f"Unexpected token {token!r}")


def parse_formula(text: str) -> Any:
    return _Parser(_tokenize(text)).parse()


# ── evaluation ───────────────────────────────────────────────────────────────


def _to_number(value: Any) -> float:
    if value is None:
        # Blank cells coerce to zero in arithmetic (Excel behavior); only
        # text that cannot be parsed stays a #VALUE! error.
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            # Infinity/NaN is not a spreadsheet value. Returning it let it flow
            # into an ``int()`` (a date year, a REPT count) and raise
            # OverflowError straight out of the engine instead of a cell error.
            raise ValueError(NUM_ERROR)
        return number
    if isinstance(value, str):
        # The whole string has to read as a number, the same complete-decimal
        # rule the aggregates apply to a text argument. ``float`` alone is too
        # permissive: it also accepts "inf", "nan" and the digit-separating
        # underscore (``float("1_0") == 10.0``), none of which Excel reads as a
        # number -- and the client engine refuses them too, so both engines
        # agree on the string that is not a number.
        text = value.strip()
        if not NUMERIC_TEXT_RE.match(text):
            raise ValueError(VALUE_ERROR)
        number = float(text)
        if not math.isfinite(number):
            # A decimal that overflows a double ("1e999") is #NUM!, not inf.
            raise ValueError(NUM_ERROR)
        return number
    raise ValueError(VALUE_ERROR)


def _js_number_text(number: float) -> str:
    """Render a double the way JavaScript's ``String(number)`` does.

    The two engines have to spell a number the same way, so this is the one
    number-to-text rule in the package: ``repr`` supplies the shortest string
    that round-trips (JavaScript does the same), and this places the point and
    the exponent the way ECMAScript's ``Number::toString`` does. Python's own
    ``str`` disagrees on both ends of the range -- it prints ``1e-05`` where
    JavaScript prints ``0.00001``, pads the exponent (``1e-07`` vs ``1e-7``),
    and expands a large value such as ``1e21`` into 22 digits where JavaScript
    switches to ``1e+21`` after 10**21. The engineering conversions stringify
    their input (and the intermediate digits) through this too, so ``repr``
    alone was never enough for them either.

    A whole number goes through the same digit placement rather than
    ``str(int(number))``: that shortcut is right down to 10**21 and then
    wrong -- ``1e21`` is an integer-valued double, so it printed the full 22
    digits where JavaScript prints ``1e+21``, and ``1e300`` printed 301
    digits. Normalizing the ``Decimal`` strips the trailing ``.0`` a whole
    number carries (``repr(5.0)`` is ``"5.0"``) without losing the exponent
    JavaScript needs at the top of the range.
    """
    if not math.isfinite(number):
        if math.isnan(number):
            return "NaN"
        return "Infinity" if number > 0 else "-Infinity"
    if number == 0:
        return "0"
    negative = number < 0
    digits_tuple = decimal.Decimal(repr(abs(float(number)))).normalize().as_tuple()
    digits = "".join(str(digit) for digit in digits_tuple.digits)
    # The Decimal is finite here (non-finite inputs return above), so the
    # exponent is an int; the cast keeps the type-checker from carrying the
    # NaN/Infinity spellings into the arithmetic below.
    point = int(digits_tuple.exponent) + len(digits)
    if 0 < point <= 21:
        if point >= len(digits):
            out = digits + "0" * (point - len(digits))
        else:
            out = digits[:point] + "." + digits[point:]
    elif -6 < point <= 0:
        out = "0." + "0" * (-point) + digits
    else:
        shown = point - 1
        out = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
        out += "e" + ("+" if shown >= 0 else "-") + str(abs(shown))
    return ("-" if negative else "") + out


def _to_text(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except OverflowError:
            # An integer past a double (a cell's JSON can carry one) is the
            # Infinity JavaScript reads from the same literal.
            return "Infinity" if value > 0 else "-Infinity"
        return _js_number_text(number)
    return str(value)


# ── UTF-16 text measurement ──────────────────────────────────────────────


def _utf16_units(text: str) -> List[int]:
    """Split text into its UTF-16 code units.

    Excel measures text in UTF-16 code units -- ``LEN("😀")`` is 2, and
    ``LEFT("😀", 1)`` is the lone high surrogate -- and so does the client
    engine, whose strings are UTF-16 by definition. Python indexes Unicode
    code points instead, so every positional text function goes through this
    list rather than slicing the string directly. A surrogate already sitting
    in the text (one half of a pair read from JSON, say) is one unit, the
    same way the client engine reads it.
    """
    units: List[int] = []
    for character in text:
        code = ord(character)
        if code < 0x10000:
            units.append(code)
        else:
            code -= 0x10000
            units.append(0xD800 + (code >> 10))
            units.append(0xDC00 + (code & 0x3FF))
    return units


def _utf16_from_units(units: List[int]) -> str:
    """Reassemble text from UTF-16 code units.

    A surrogate pair decodes back to its character; a lone surrogate decodes
    to the surrogate character itself, which Python strings allow. That is
    what ``LEFT("😀", 1)`` has to return to match Excel and the client engine.
    """
    chars: List[str] = []
    index = 0
    while index < len(units):
        unit = units[index]
        if (
            0xD800 <= unit <= 0xDBFF
            and index + 1 < len(units)
            and 0xDC00 <= units[index + 1] <= 0xDFFF
        ):
            chars.append(
                chr(0x10000 + ((unit - 0xD800) << 10) + (units[index + 1] - 0xDC00))
            )
            index += 2
        else:
            chars.append(chr(unit))
            index += 1
    return "".join(chars)


def _utf16_len(text: str) -> int:
    """Length of text in UTF-16 code units, the way Excel counts it."""
    if text.isascii():
        return len(text)
    return len(_utf16_units(text))


def _utf16_slice(text: str, start: int, stop: Optional[int] = None) -> str:
    """Slice text by UTF-16 code-unit offsets.

    ``start`` is inclusive and ``stop`` exclusive, in units, with the usual
    Python negative-index meaning -- the list being sliced is the unit list,
    so ``LEFT("😀", 1)`` keeps the first unit (the lone high surrogate) and
    ``RIGHT("😀", 1)`` the last one, exactly as the client engine slices.
    """
    if text.isascii():
        return text[start:stop]
    return _utf16_from_units(_utf16_units(text)[start:stop])


def _utf16_find(needle: str, haystack: str, start: int) -> int:
    """Unit offset of ``needle`` in ``haystack`` at or after ``start``.

    Answers the offset in UTF-16 code units, or -1 when there is no match --
    ``str.find`` over the UTF-16 byte form, which keeps the C-speed search of
    the plain ``str.find`` it replaces. A match has to start on a unit
    boundary, so an odd-byte hit (the needle's bytes straddling two units) is
    skipped and the search resumes after it. The empty-needle and past-the-end
    answers fall out the same way ``str.find`` gives them.
    """
    if needle.isascii() and haystack.isascii():
        return haystack.find(needle, start)
    hay = haystack.encode("utf-16-le", "surrogatepass")
    ndl = needle.encode("utf-16-le", "surrogatepass")
    position = hay.find(ndl, max(start, 0) * 2)
    while position != -1 and position % 2 == 1:
        position = hay.find(ndl, position + 1)
    return -1 if position == -1 else position // 2


def _utf16_first_unit(text: str) -> int:
    """First UTF-16 code unit of text, which is what CODE reports.

    A basic-multilingual character is its own code point; an astral one (an
    emoji, say) reports its high surrogate -- ``CODE("😀")`` is 55357 -- the
    way the client engine's ``charCodeAt(0)`` reads it.
    """
    code = ord(text[0])
    if code < 0x10000:
        return code
    return 0xD800 + ((code - 0x10000) >> 10)


def _fold_case(text: str) -> str:
    """Fold text the way Excel's case-insensitive comparisons do.

    Excel folds one character at a time, without the full case folding that
    can expand a single letter: ``"straße" = "STRASSE"`` is FALSE, because
    ``ß`` is one letter and ``SS`` is two. ``str.casefold()`` applies the full
    fold and answered TRUE, disagreeing with Excel and with the client engine,
    whose ``toLowerCase()`` folds the same way ``str.lower()`` does. Every
    case-insensitive comparison in this package goes through this helper so
    the two engines cannot drift apart again.
    """
    return text.lower()


def _text_equals(left: Any, right: Any) -> bool:
    """Compare two values as text the way Excel's ``=`` does.

    Excel's text comparison is case-insensitive -- ``"a" = "A"`` is TRUE and
    ``EXACT`` is the case-sensitive test -- so a plain ``==`` here answered
    FALSE where Excel, the criteria comparisons and the client engine answer
    TRUE. Lookup keys compare the way ``=`` does, so they use this too.
    """
    return _fold_case(_to_text(left)) == _fold_case(_to_text(right))


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coerce_number_text(value: Any) -> Optional[float]:
    """Read a text *argument* the way Excel reads a text number.

    A literal text argument is coerced (``SUM("2")`` is 2) while a value read
    out of a referenced range is not (``SUM(A1)`` is 0 when A1 holds the text
    "2"), so callers pass an argument's provenance in and only the literal case
    reaches here. The whole string has to read as a number: Excel translates a
    text argument into a number rather than parsing a prefix of it.

    Returns ``None`` when the text is not a complete number, which every caller
    treats as "not a number to include" rather than as an error.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not NUMERIC_TEXT_RE.match(text):
        return None
    try:
        number = float(text)
    except ValueError:  # pragma: no cover - the pattern above already gates this
        return None
    # A decimal that overflows a double ("1e999") is not a number to include,
    # so the aggregate ignores it rather than adding infinity to the total.
    return number if math.isfinite(number) else None


def _to_number_or_zero(value: Any) -> float:
    """Numeric coercion that treats blanks and non-numeric text as zero.

    Used by criteria functions (SUMIF/AVERAGEIF) where Excel silently
    ignores non-numeric entries in the summed range.
    """
    if value is None:
        return 0.0
    try:
        return _to_number(value)
    except ValueError:
        return 0.0


def _parse_criteria_target(text: str) -> Any:
    """Read the operand of an operator-prefixed criteria.

    A numeric operand compares as a number (``">=2"``) and anything else as
    text (``"<>x"``). The whole string has to read as a number, matching how
    the aggregates read a text argument, so ``">=2abc"`` is a text test rather
    than a prefix match on 2.
    """
    if NUMERIC_TEXT_RE.match(text):
        return float(text)
    return text


def _criteria_rank(value: Any) -> int:
    """Excel's cross-type ordering: numbers, then text, then logicals."""
    if isinstance(value, bool):
        return 2
    if isinstance(value, str):
        return 1
    return 0


def _compare_values(left: Any, right: Any) -> int:
    """Order two values the way Excel sorts them (``-1``, ``0`` or ``1``).

    Excel's criteria comparisons are not plain numeric coercions: every value is
    ranked (number < text < FALSE < TRUE) and compared inside its rank, with
    text compared case-insensitively. So ``COUNTIF(range, "<>2")`` counts the
    text cells and ``COUNTIF(range, ">1")`` ignores them, where coercing both
    sides with ``_to_number`` would report #VALUE! instead.
    """
    left_rank, right_rank = _criteria_rank(left), _criteria_rank(right)
    if left_rank != right_rank:
        return -1 if left_rank < right_rank else 1
    if left_rank == 2:
        left_key: Any = int(bool(left))
        right_key: Any = int(bool(right))
    elif left_rank == 1:
        left_key = _fold_case(_to_text(left))
        right_key = _fold_case(_to_text(right))
    else:
        left_key, right_key = _to_number(left), _to_number(right)
    if left_key == right_key:
        return 0
    return -1 if left_key < right_key else 1


def _criteria_operator(op: str, value: Any, target: str) -> bool:
    """Apply one operator-prefixed criteria (``">=2"``, ``"<>x"``) to a value."""
    if op in ("=", "<>") and _is_pattern(target):
        # Excel's wildcards only pattern-match text: ``COUNTIF(range, "=tw*")``
        # matches the text cells starting with "tw", while a number, a logical
        # or a blank never matches (so ``"<>tw*"`` counts them, the same way
        # ``"<>x"`` already counts every cross-type value today).
        matched = isinstance(value, str) and _wildcard_match(target, value)
        return (not matched) if op == "<>" else matched
    order = _compare_values(value, _parse_criteria_target(target))
    if op == ">":
        return order > 0
    if op == "<":
        return order < 0
    if op == ">=":
        return order >= 0
    if op == "<=":
        return order <= 0
    if op == "<>":
        return order != 0
    return order == 0


def _is_pattern(pattern: str) -> bool:
    """Report whether text has to be read as an Excel pattern.

    That is the case when it carries a wildcard -- ``*`` for any run of
    characters, ``?`` for exactly one -- or an escape sequence, because only
    the pattern path honours one: a ``~`` before ``*``, ``?`` or ``~`` makes
    that character literal, so the criteria ``"tw~*"`` matches the text
    ``"tw*"``, where comparing the string as it stands would look for a
    tilde. A ``~`` before an ordinary character is itself literal, as in
    Excel.
    """
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "~" and i + 1 < len(pattern) and pattern[i + 1] in ("*", "?", "~"):
            return True
        if char in ("*", "?"):
            return True
        i += 1
    return False


@functools.lru_cache(maxsize=512)
def _wildcard_pattern_to_regex(pattern: str) -> "re.Pattern[str]":
    """Compile a criteria wildcard pattern, folded to lower case.

    ``_wildcard_match`` lower-cases the pattern and the value through
    ``_fold_case`` before it applies this, so the pattern is compiled without
    ``re.IGNORECASE``: that flag folds a *different* set of characters than
    ``str.lower()`` (it matches ``s`` against the long s and ``k`` against the
    Kelvin sign), and JavaScript's ``i`` flag folds a third set, so leaving it
    on made the two engines disagree on those code points. The fold the rest of
    the comparison helpers use is the one that applies here too.

    Cached because a criteria compiles once per call but matches once per
    cell: recompiling the pattern for every row would turn a 100k-row
    COUNTIF into 100k compilations of the same expression.
    """
    parts = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "~" and i + 1 < len(pattern) and pattern[i + 1] in ("*", "?", "~"):
            parts.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if char == "*":
            parts.append(".*")
        elif char == "?":
            parts.append(".")
        else:
            # A ``~`` before an ordinary character (and every other
            # character) is literal, matching Excel.
            parts.append(re.escape(char))
        i += 1
    return re.compile("".join(parts), re.DOTALL)


def _wildcard_match(pattern: str, value: str) -> bool:
    """Match text against an Excel-style wildcard pattern (``*``, ``?``, ``~``)."""
    return (
        _wildcard_pattern_to_regex(_fold_case(pattern)).fullmatch(_fold_case(value))
        is not None
    )
