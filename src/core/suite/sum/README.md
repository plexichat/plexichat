# PlexiSum

PlexiSum is the workbook and high-performance data-layer for Plexichat. Each workbook is an independent SQLite database (`.plexisum`) stored through the main file-storage adapter (S3 or local filesystem). The main database holds only metadata, ownership, and ACL — never workbook cells.

## Overview

PlexiSum provides a spreadsheet-like engine with:

- **File-backed isolation**: Every workbook is a standalone `.plexisum` file with its own SQLite schema.
- **Optimistic concurrency**: Readers get snapshot isolation; writers use compare-and-swap on a revision counter.
- **Coalesced writes**: Cell edits are buffered in a durable operation log and flushed to immutable blobs on a configurable window.
- **Formula evaluation**: A bounded formula engine with cycle detection, cross-sheet references, and lazy evaluation.
- **Column segments**: Dense packed storage with auto-selected encoding (run-length, delta, dictionary) for viewport reads.
- **Manifest integrity**: The container format uses SHA-256 checksums and zstandard compression.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  SumManager (facade)                                    │
│  ┌───────────┬───────────┬──────────┬──────────────┐    │
│  │ Workbooks │   Data    │  Admin   │  Operations  │    │
│  ├───────────┼───────────┼──────────┼──────────────┤    │
│  │ Sessions  │ Snapshots │ Features │   Viewport   │    │
│  ├───────────┼───────────┼──────────┼──────────────┤    │
│  │ Exchange  │ Revisions │          │              │    │
│  └───────────┴───────────┴──────────┴──────────────┘    │
│                      │                                   │
│  ┌───────────────────┴──────────────────────────────┐   │
│  │  BlobStore (MediaBlobStore / LocalBlobStore)     │   │
│  └──────────────────────────────────────────────────┘   │
│                      │                                   │
│  ┌───────────────────┴──────────────────────────────┐   │
│  │  .plexisum file (container format)                │   │
│  │  ┌──────────────────────────────────────────┐    │   │
│  │  │  SQLite DB (cells, sheets, operations)    │    │   │
│  │  └──────────────────────────────────────────┘    │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### Main database tables

| Table | Purpose |
|---|---|
| `sum_workbooks` | Workbook identity, ownership, revision counter, blob pointers |
| `sum_grants` | Per-workbook ACL (subject, permissions, expiry) |
| `sum_range_permissions` | Per-range read/edit restrictions |
| `sum_snapshots` | Named snapshot pointers |
| `sum_operations` | Durable operation buffer between flushes |
| `sum_features` | Client-authored visualization definitions |
| `sum_revision_blobs` | Immutable revision blob metadata |

### Workbook file tables

| Table | Purpose |
|---|---|
| `sum_meta` | Format version marker |
| `sum_sheets` | Sheet definitions |
| `sum_columns` | Column metadata (types, display names, positions) |
| `sum_rows` | Row metadata |
| `sum_cells` | Typed, sparse cell storage |
| `sum_column_segments` | Packed column segments for viewport reads |
| `sum_statistics` | Access/filter/sort counters for index promotion |

## Key files

| File | Purpose |
|---|---|
| `__init__.py` | Module facade — `setup()`, `get_manager()`, re-exports |
| `manager.py` | `SumManager` class (mixin composition of all manager modules) |
| `manager_base.py` | Base manager shared logic |
| `manager_workbooks.py` | Workbook CRUD operations |
| `manager_data.py` | Cell read/write operations |
| `manager_operations.py` | Operation log and flush logic |
| `manager_sessions.py` | Coalesced write sessions |
| `manager_snapshots.py` | Snapshot creation and restore |
| `manager_viewport.py` | Viewport reads (cell ranges for rendering) |
| `manager_features.py` | Visualization definitions (charts, pivots) |
| `manager_exchange.py` | Import/export (CSV) |
| `manager_admin.py` | Administrative operations |
| `manager_revisions.py` | Revision history and GC |
| `schema.py` | DDL for main DB and workbook file schemas |
| `container.py` | `.plexisum` container format (header + zstd + SHA-256) |
| `segments.py` | Column segment encoding (plain, run-length, delta, dictionary) |
| `formulas.py` | Bounded formula evaluator facade |
| `_formula_core.py` | Formula tokenizer, parser, core evaluator |
| `_formula_advanced.py` | Advanced formula functions |
| `_formula_shared.py` | Shared formula constants, error codes, cell resolver protocol |
| `_formula_protocol.py` | Formula protocol definitions |
| `_shared.py` | Constants, errors, blob stores, session state |

## Usage

### Initialization

```python
from src.core.suite.sum import setup, get_manager

# Initialize with the main database, media adapter, and storage directory
manager = setup(
    db=main_db,
    servers_module=servers,
    media=media_adapter,
    storage_dir="/data/workbooks",
)
```

### Create a workbook

```python
manager = get_manager()
workbook = manager.create_workbook(
    title="Sales Q1",
    owner_id=user_id,
    scope="personal",
)
```

### Read cells

```python
cells = manager.get_viewport(
    workbook_id=workbook["id"],
    sheet_id="sheet-1",
    row_start=0,
    row_end=100,
    col_start=0,
    col_end=26,
)
```

### Write cells

```python
manager.apply_operation(
    workbook_id=workbook["id"],
    actor_id=user_id,
    operation_type="set_cell",
    payload={
        "sheet_id": "sheet-1",
        "row": 0,
        "column": 0,
        "value_type": "text",
        "value": "Hello",
    },
)
```

### Evaluate formulas

```python
from src.core.suite.sum.formulas import compute_value

result = compute_value(
    value_type="formula",
    value=None,
    formula="SUM(A1:A100)",
    resolver=cell_resolver,
    sheet="sheet-1",
)
```

## Formula semantics

This engine and the PlexiSum client engine (`plexichat-client`, see its
`formula-engine/README.md`) implement the same formula language and are held to
the same answers. `src/tests/suite/formula_parity_cases.json` is the
machine-checked statement of that: every case is evaluated against both engines
from the identical file, so a semantic change in one engine and not the other
fails a test instead of drifting silently until someone notices a different
answer on screen. The client repository carries a byte-identical copy of the
same file, and `src/tests/suite/test_sum_cross_engine.py` holds the two
together from three directions: it generates a formula matrix over every
operator and adopted function family and evaluates it in both engines, it runs
every case in the table through both engines and compares the answers with each
other, and it checks the two copies of the table are still byte-identical.
Around it, `test_sum_cross_engine_fuzz.py` throws a seeded stream of random
formulas at both engines, `test_sum_engineering_agreement.py` runs the
engineering quirks the README documents past both of them, and
`test_sum_function_coverage.py` holds the client engine's whole function
inventory to this table and to the documented gap.

Two rules explain most of the behaviour:

**An error operand propagates.** Pass an error into a function and it comes
back out: `SUM(1, 1/0)`, `PROPER(1/0)`, `ROUND(1, 1/0)`, `DATE(1/0, 1, 1)` and
`SUMIF(A1:A3, 1/0)` are all `#DIV/0!`. `SWITCH` propagates the operand the
scan reaches: `SWITCH("B", 1/0, 1, "b", 2, 3)` is `#DIV/0!` rather than 2,
while `SWITCH("a", "a", 1, 1/0, 2)` is 1 because the untaken branch is never
evaluated. Three groups classify an error instead,
because they answer a question *about* the value rather than computing with it:

- `COUNT` counts numbers only, so an error is simply not counted;
- `COUNTA` counts anything non-empty, and an error is data;
- `COUNTBLANK` counts an error *cell* as non-blank, while an error written
  into the formula still propagates -- `COUNTBLANK(A5)` is 0 and
  `COUNTBLANK(1/0)` is `#DIV/0!`;
- the `IS*` family and `TYPE` report `FALSE` and `16`.

`AND` and `OR` evaluate every argument -- Excel does not short-circuit them --
so `AND(FALSE, 1/0)` is `#DIV/0!` rather than `FALSE`, and the first error in
argument order wins (`AND(NA(), 1/0)` is `#N/A`).

**A text argument is coerced, a referenced value is not.** Excel translates a
text argument into a number (`SUM("2")` is 2) but never a value read from a
cell or range (`SUM(A1)` is 0 when A1 holds the text `"2"`), which is why a
text-formatted number column does not silently add up. `AVERAGE`, `MIN`, `MAX`,
`PRODUCT` and `COUNT` follow the same rule, and the whole argument has to read
as a number rather than a prefix of one (`SUM("2abc")` is 0). The arithmetic
operators and the numeric functions apply the same whole-string rule, so
`"2abc"+1` is `#VALUE!` while `"2"+1` is 3, and a decimal that overflows a
double (`"1e999"+1`) is `#NUM!` rather than infinity.

**A scalar or a criteria argument is read through its reference.** The range
functions take their arguments apart — a rank, a quartile, a percent, a
criteria — and read each one as a single value, so a reference in one of those
slots is read out of the cell it names: `LARGE(A1:A3, A1)` is 3 and
`COUNTIF(A1:A3, A1)` is 1. An error in the cell is the answer. The rule above —
a referenced value is not coerced — is about the *range* slots; these are
single values and read like a literal argument.

**A range read where one value is expected follows the pooled rule.** A scalar
slot is reduced to a single value the way the client engine's `scalarize`
does: an error cell in the range propagates, a one-cell range is that cell,
and a range of more than one cell is `#VALUE!` -- never a comma-joined string.
This applies to every scalar slot -- `_scalar_argument` for
`LARGE`/`RANK`/`PERCENTRANK` and the regressions, `_read_number` for the
financial and statistical arguments, the criteria operand and the whole
branching family's slots (`IF`/`NOT`/`IFERROR`/`IFNA`/`IFS`/`SWITCH`/`CHOOSE`
conditions, branch values, search keys, results and fallback) -- and it is
applied *per argument* rather than
flattening the range into the pooled list, so `LARGE(A1:A5, A1:A5)` propagates
the error cell from the rank argument. The IS*/TYPE inspectors are the one
refinement: they answer a question *about* one value and treat an error as
input, so a one-cell range is inspected (`ISBLANK(A5)` is FALSE) while a
multi-cell range is `#VALUE!` rather than propagating the error cell.

**A fixed-arity call rejects a stray argument before it evaluates anything.**
The scalar functions read exactly their declared operands, so `ABS(1, 2)`,
`SIN(1, 2)` and `LEN("a", "b")` are `#VALUE!` rather than quietly reading the
first one; only `PI()` ignores its arguments on both engines. `NETWORKDAYS`,
`WORKDAY` and `COUNTIF` bound their optional slots the same way, so a fourth
argument (`NETWORKDAYS(1, 2, 3, 4)`) or a third to a two-argument form
(`COUNTIF(1, 1, 1)`) is `#VALUE!`, while `SUMIF`/`AVERAGEIF` still take their
optional sum range. `_ARITY` holds every fixed-arity bounds and the check runs
*before* the operands are read, so a wrong count beats an error operand:
`NOT(1/0, 1/0)` is `#VALUE!`, not `#DIV/0!`, matching the client engine's
pre-check. An error operand otherwise propagates out of every value function
(`PMT(1/0, 1/0, 1/0)` and `GCD(1/0)` are `#DIV/0!`); `_read_number` reads a
scalar slot through the same pooled rule as the client's `toNumber`, so an
error is propagated rather than coerced into `#VALUE!`.

**The time of day truncates through whole milliseconds, after Excel's 15-digit
rounding.** Excel keeps only 15 significant decimal digits, so the serial is
rounded there before the time is read: a serial naming 41.8 seconds is the
41st second -- `SECOND(0.04701185841617224)` is 41, not the 42 a round to the
nearest second answered here -- a value a hair below a boundary rounds *to* it
(`HOUR(0.9999999999999999)` is 0), and one carrying more than 15 digits rounds
at that digit (`MINUTE(28/3)` is 59, because `28/3` is stored as
`9.33333333333333`, i.e. 07:59:59). The time is then read from the whole
milliseconds the serial carries (`trunc(serial * 86400000)` reduced to one
day). Reading the leftover fraction after subtracting `floor(serial)`
disagreed with the client by a second at a millisecond boundary on a negative
serial; reading the un-rounded serial disagreed in both directions once the
client applied the 15-digit rule. The readers also name a time *on a date*, so
a serial whose date part is outside the covered span is `#NUM!` like every
other date reader rather than a silent 0.

**The same 15-digit rounding decides a date part.** Excel stores the serial
before any reader sees it, so the rule is not the time readers' alone: a serial
a hair below a day boundary is stored as the boundary, and `YEAR`, `MONTH`,
`DAY` and `WEEKDAY` answer the boundary's date. `YEAR(45291.99999999999)` is
2024 (the serial stores as 45292, i.e. 2024-01-01), and the rule is symmetric
about zero (`DAY(-0.9999999999999999)` is 29, because the serial stores as -1).
`DAYS`, `EDATE`, `EOMONTH`, `WORKDAY` and `NETWORKDAYS` read a serial the same
way through the shared `_round_serial`, so each stays on the day the stored
value names, and the covered range is checked on the rounded part. A holiday
names the day its rounded serial's integer part (truncated toward zero), so
`NETWORKDAYS(45292, 45292, 45291.99999999999)` counts the holiday and answers
0, and `NETWORKDAYS(-2, -2, -2.5)` counts -2 rather than flooring to -3.
A reversed span counts negative, as Excel does: the same workdays are counted
forward, then negated, so `NETWORKDAYS(DATE(2024,1,10),DATE(2024,1,1))` is -8
and a holiday counts either way.

**Places is coerced before a conversion's input is validated.** The base
conversions read their `places` argument first, so a bad number *and* bad
places report the places error: `BIN2OCT(18, "abc")` is `#VALUE!` (bad
places), not the `#NUM!` a bad input earns when places is valid.

**Criteria rank types.** `SUMIF`, `COUNTIF`, `AVERAGEIF` and their
multi-criteria forms compare each cell against the criteria through Excel's
sort order — number, then text, then `FALSE`, then `TRUE` — with text compared
case-insensitively, and the operators `<`, `<=`, `>`, `>=`, `<>` and `=` all
work on text as well as numbers. So `COUNTIF(range, "<>2")` counts the text
cells and `COUNTIF(range, ">2")` ignores them. A single cell is a 1×1 range to
these functions (`COUNTIF(A1, ">=1")`). A row that matches the criteria but
holds no number in the summed range contributes 0 to `SUMIF`/`SUMIFS`, while
`AVERAGEIF`/`AVERAGEIFS` skip it entirely — Excel ignores text and blanks in an
average range, so a match with no number at all is `#DIV/0!`.

**An error in a criteria range is not an error cell.** An error *written
into* the formula in a criteria or sum range propagates -- `COUNTIF(1/0, 1)`
and `SUMIF(1, 1, 1/0)` are `#DIV/0!` -- while an error held in a *cell* of the
range only fails to match, so `COUNTIF(A5, 1)` is 0 when A5 holds `1/0`. The
two arrive as the same string, so the argument node tells them apart: a plain
reference holds cells, anything computed is a value.

**A lookup key compares the way `=` does.** `VLOOKUP`, `HLOOKUP` and `MATCH`
match the text `"1"` against the number 1, as Excel's `=` does, and an
approximate match whose lookup value does not read as a number is a miss
(`#N/A`) rather than an error; a `row_index_num` past the bottom of the table
is `#REF!`.

**A lookup reads its range cell by cell.** `INDEX`, `MATCH`, `OFFSET`,
`INDIRECT`, `LOOKUP` and `XLOOKUP` walk the cells they need rather than
expanding the whole range first, so an error cell the lookup never touches is
not its answer: `INDEX(A1:A5, 2)` is 2 even when A5 holds `1/0`. A computed
error still propagates -- `INDEX(A1:A3, 1/0)` and `MATCH(1/0, A1:A3, 0)` are
`#DIV/0!`.

**Text compares case-insensitively.** Excel's `=` ignores case — `"a" = "A"` is
`TRUE`, and `EXACT` is the case-sensitive test — so the `=`/`<>` operators, the
lookup keys (`MATCH("TWO", …)` finds the text `two`) and `SWITCH`'s cases all
match without regard to case. The criteria comparisons already did. The fold is
one character at a time, not Unicode's full case folding, so
`"straße" = "STRASSE"` is `FALSE` — `ß` is one letter and `SS` is two — the way
the client engine's lower-casing reads it.

**An exact lookup takes wildcards.** An exact match — `MATCH`'s `match_type`
0, `VLOOKUP`/`HLOOKUP`'s `FALSE`, and `XLOOKUP`'s `match_mode` 0 and 2 — reads
a *text* lookup value as a pattern, so `MATCH("ap*", D1:D3, 0)` is 1,
`VLOOKUP("T*", B1:C3, 2, FALSE)` is 20, and `MATCH("tw~*", L1, 0)` finds the
literal `tw*`. A pattern matches
text cells only (`MATCH("*", A1:A3, 0)` is `#N/A` over a column of numbers),
and the approximate forms carry no patterns at all, so `VLOOKUP("a*", D1:D3,
1, TRUE)` is `#N/A`. `XLOOKUP`'s `match_mode` 1 and -1 answer the closest
value on the right side — the smallest entry ranked at or above the lookup,
or the largest ranked at or below it — through the same type ranking, so text
orders too; `search_mode` -1 and -2 scan last-to-first, and the binary modes
scan linearly in the same direction, which agrees on sorted data.

**A logical is not its number.** The ranking above reaches `=` and `<>` too: a
logical and a number are never equal (`TRUE = 1` is `FALSE`, where Python's own
`True == 1` answered the opposite), and the criteria functions follow, so
`COUNTIF(range, 1)` does not count a `TRUE` cell while `COUNTIF(range, TRUE)`
does. A blank is likewise not the zero it coerces to in arithmetic, so
`COUNTIF(range, 0)` counts the zero cells rather than the empty ones. Lookup
keys rank the same way: `MATCH(1, {TRUE}, 0)` is `#N/A`.

**An omitted argument is a blank.** An empty slot between commas — `SUM(1,)`,
`SUM(,1)` or `SUM(1,,2)` — is Excel's "missing" operand, and both engines read
it as a blank value: skipped by the aggregates, 0 as a number and `""` as text.
So `SUM(,)` is 0, `IF(FALSE,1,)` is blank and `LEFT("abc",)` takes 0 characters
rather than the parameter's default of 1. It used to be a parse error here,
while the client engine silently dropped a trailing comma, so the two engines
disagreed about `SUM(1,)`.

**`^` groups to the left.** `2^3^2` is `(2^3)^2`, i.e. 64, matching Excel and
the client engine (this parser used to be right-associative here).

**Unary signs nest, and only unary minus coerces.** Repeated signs are nested
unary operators, so `--1` is `-(-1)` and `+-1` is `-1`, making `=--1` the
number 1. Unary minus converts its operand to a number (`-"2"` is -2,
`-TRUE` is -1), while unary plus is the identity, as in Excel: `+"2"` is the
text `"2"` and `+TRUE` is `TRUE`. A coercion failure is an error *value*, so
`-"abc"` is `#VALUE!` and `IFERROR(-"abc", 9)` is 9 rather than a `#VALUE!`
that unwound past the catcher.

**`&` binds between arithmetic and comparison.** `1&1+1` is `1&(1+1)` (the
text "12") and `1=1&1` is `1=(1&1)` (FALSE). Concatenation used to share the
additive level, so `1&1+1` parsed as `(1&1)+1` (the number 12). A binary
operator is also left-first and does not evaluate its right operand once the
left has failed, so a *raised* right-side failure does not outrank the left
error (`NA()+A1:A5/1` is #N/A, not #DIV/0!).

**A zero discount factor is `#DIV/0!`.** `NPV` and `XNPV` divide by
`(1 + rate)` raised to the period, so a rate of -1 makes the denominator zero.
Excel treats that as the division by zero it is, so the answer is `#DIV/0!`
and not the `#NUM!` an infinite result earns; `MIRR`'s discounting follows the
same rule. Dividing here used to raise out of the evaluator instead of
answering an error at all.

**`MIRR` needs a sign change.** Excel has no rate to solve for unless the cash
flows hold at least one negative value (something to discount back) and at
least one positive one (something to compound forward), so a series that is all
one sign — a single value included — is `#DIV/0!` rather than the -1 a bare
0/0 produces.

**`DATEVALUE` reads date text.** The argument goes through one shared list of
unambiguous shapes — ISO first, then the common US and day-month-name
spellings — and a value that names no date is `#VALUE!`. A bare number is not
date text (`DATEVALUE(1)` is `#VALUE!`), and a day the month does not have
(`DATEVALUE("2023-02-29")`) is rejected rather than rolled forward into March.
`XNPV` reads its dates through the same list.

**`PERCENTRANK`'s `significance`.** Excel rejects a significance below 1 with
`#NUM!`, and the rank is rounded to that many digits. A double carries about
15 significant digits, so a larger significance cannot round anything and the
value comes back unrounded — `PERCENTRANK(A1:A3, 1, 999)` is the same as with
308. Raising the rounding factor used to throw an `OverflowError` straight out
of the evaluator.

**Text counts UTF-16 code units.** Excel measures text the way the client
engine's strings work -- in UTF-16 code units -- so an astral character (an
emoji) is two: `LEN("😀")` is 2, `FIND("😃", "😀😃😄")` is 3, and a position
can land inside a surrogate pair and keep the surviving half
(`LEFT("😀😃😄", 1)` is the lone high surrogate, `MID("😀😃😄", 2, 1)` the lone
low one). `CODE` reports the first unit, so `CODE("😀")` is 55357, and a
`FIND`/`SEARCH` start past the end of the text is `#VALUE!` even for an empty
search string. Python indexes code points instead, so the positional functions
go through the UTF-16 helpers in `_formula_shared.py` rather than slicing the
string directly.

**A number renders the way JavaScript prints it.** Converting a number to text
-- the `&` operator, `TEXT`'s fallback, `VALUE`, a criteria comparison -- goes
through ECMAScript's `Number::toString`, which is what the client engine's
`String(value)` does. That is the shortest round-tripping decimal, written
plainly from `1e-6` up to (but not including) `1e21` and in scientific notation
otherwise, with no zero padding on the exponent: `1e-5` is `0.00001`, `1e-6` is
`0.000001`, `1e-7` is `1e-7`, `1e21` is `1e+21` and `1e300` is `1e+300`.
Python's own `str` disagreed at both ends -- it printed `1e-05` and `1e-07`,
and expanded `1e21` into 22 digits and `1e300` into 301.

**A squared term is bounded by the doubles.** The spread and cross-product
aggregates -- `AVEDEV`, `DEVSQ`, `STDEV`, `STDEVP`, `VAR`, `VARP`, `SUMSQ`,
`SUMX2MY2`, `SUMX2PY2`, `SUMXY` -- sum terms that overflow long before their
inputs do: the square of `1e300` is not a double. Excel answers `#NUM!` there,
and so does this engine, rather than letting an `Infinity`, the `NaN` of an
`inf - inf`, or an `OverflowError` out of the evaluator. Python's `** 2` raised
where the client engine's multiplication quietly answered `Infinity`, so
`STDEV(1e300, -1)` used to crash here and leak a non-number there; both now
answer `#NUM!`. The sum is compensated -- this engine's Python `sum()` -- and
the client engine's spread measures now sum the same way, so
`VAR(1e3, 12, 1, 2, 3) & ""` spells `198223.3` on both rather than the
`198223.30000000002` a naive left-to-right loop printed there. `AVEDEV` is
included because its absolute deviations can overflow the sum the same way.

**A per-unit loop is bounded.** `DB` walks one period at a time, and
`BINOMDIST`/`POISSON` sum one term per success or event. Those arguments are
unrestricted doubles, so without a bound a single formula could drive the loop
for hours; past `MAX_LOOP_ITERATIONS` steps the answer is `#NUM!` rather than a
hang, shared with the client engine. The depreciation loop also stops as soon
as its step underflows to zero past the asset's life, because the book can no
longer move and the remaining periods cannot change the answer. The same
argument applies to the inverse distributions: an arithmetic overflow inside
the numeric families is `#NUM!` rather than an escaping exception, since the
client engine computes in doubles and turns an `Infinity`/`NaN` into `#NUM!`,
so `GAMMAINV`/`BETAINV` do the same where a denominator rounds away or a factor
leaves the doubles.

**A materialised block is bounded.** `OFFSET`'s `height` and `width` are loop
bounds and unrestricted doubles, so without a bound `OFFSET(A1, 0, 0, 1e9,
1e9)` built a 10^18-cell block and stalled the evaluating thread. The span is
capped at the shared `MAX_BLOCK_CELLS` cells, and a span past it is `#NUM!` --
the answer the client engine gives -- rather than an allocation the engine
cannot honour.

### Known divergences from Excel

Deliberate, and shared with the client engine unless noted:

**Criteria support wildcards.** `SUMIF`, `COUNTIF`, `AVERAGEIF` and their
multi-criteria forms pattern-match text cells with Excel's wildcards — `*`
(any run), `?` (one character), `~` to escape either — case-insensitively, in
a bare pattern (`COUNTIF(range, "tw*")`) and in the `=`/`<>` forms. A tilde
that escapes a wildcard makes the text a pattern too, so `COUNTIF(range,
"tw~*")` counts the cells holding the literal text `tw*`. Only text cells can
match a pattern; the relational operators compare literally.

| Case | Excel | Here |
|---|---|---|
| `"a" < "b"` | `TRUE` | `#VALUE!` — comparisons coerce to a number |
| `SUM(TRUE, 1)` | `2` | `1` — logical *arguments* are not coerced |
| `COUNTA("")` | `1` | `0` — an empty-text argument counts as empty |
| `IF("x", 1, 2)` | `#VALUE!` | `1` — non-boolean text is truthy |
| `"" + 1` | `1` | `#VALUE!` — empty text does not coerce to 0 |
| `DATE`/`YEAR`/`MONTH`/`DAY` before 1900-03-01 | Excel's serials | one day lower (see `_DATE_EPOCH`; modern serials match) |
| `DATEVALUE` of a text date before 1900-01-01 | `#VALUE!` | read as a serial, so `DATEVALUE("0001-01-01")` is `-693593` |
| single-argument `ROUND`/`ROUNDUP`/`ROUNDDOWN` | a formula error | `#VALUE!` — `num_digits` is required |
| `SINH`/`COSH` of an argument past ~710, and any complex result whose coefficient a double cannot hold | `#NUM!` | `#NUM!` — an overflowing double is not a cell value |
| `ERF`/`ERFC` | precise to ~1e-16 | Abramowitz & Stegun 7.1.26 (~1.5e-7), shared with the client engine |
| `BESSELJ`/`BESSELY` past the range the 40-term series can hold | a small oscillating value | `#NUM!` — a series term overflows the doubles, and an overflowing double is not a cell value |
| `BESSELJ` with a huge order at a small `x` (`BESSELJ(1,1000000)`), and `BESSELY` with a huge order | a small value | `BESSELJ` underflows to `0` while the `BESSELY` recurrence leaves the doubles (`#NUM!`), shared with the client engine |
| fractional `DEC2BIN`/`DEC2HEX`/`DEC2OCT` inputs | truncated to an integer | rendered through the float base conversion both engines share (e.g. `DEC2BIN(5.9)` is `"101.9000000000000004"`) |
| `BITAND`/`BITOR`/`BITXOR` past 2^31 | full 48-bit result | 32-bit wrap, shared with the client engine |
| an error stored in a statistical range (`MEDIAN(A1:A5)`) | `#DIV/0!` | the cell is skipped like text; an error *written into* the formula still propagates |
| `ISREF`, `ISFORMULA`, `SHEET`/`SHEETS`, `LAMBDA`/`LET`, the `LINEST`/`LOGEST`/`TREND`/`GROWTH` regression machinery, the remaining `*DIST`/`*INV` functions (`TDIST`, `CHIDIST`, `FDIST`, …), the dynamic-array family, and the volatile `RAND`/`RANDBETWEEN`/`RANDARRAY` trio | implemented | `#NAME?` — the server engine is a deliberately compact subset |

An explicitly empty argument is read as a blank value rather than as the
parameter's default — `LEFT("abc",)` is `""`, not `"a"`. Whether Excel applies
the default for an empty argument rather than coercing it is not pinned down
here, so both engines simply agree with each other.

### Functions the client engine has that this one does not

This engine implements 231 functions (`TRUE`/`FALSE` work both as bare
literals and as zero-argument calls, matching Excel and the client engine);
the client engine implements 264. The gap
is by design — a viewport read, a CSV export and a sort need a compact,
cheap-to-verify subset — but it has never been written down, so it is listed
here, with what to adopt next and what to leave alone. Every function named
below is callable in the client engine today.

**Adopted.** The first version of this list was a plan; these have now been
implemented, taking the engine from 84 to 231 functions:

| Family | Functions | Note |
|---|---|---|
| Text | `CHAR`, `CODE` | `CHAR` truncates toward zero and rejects a code outside 1-255 with `#VALUE!`; `CODE` reads the first UTF-16 unit and rejects empty text, so `CODE("😀")` is 55357 |

| Family | Functions | Note |
|---|---|---|
| Logical | `TRUE`, `FALSE` | zero-argument call forms of the literals; a stray argument is `#VALUE!` |
| Lookup | `CHOOSE`, `IFS`, `SWITCH`, `XLOOKUP` | `CHOOSE`/`IFS`/`SWITCH` are evaluated on the `IF`/`IFNA` short-circuit path, so an untaken branch is not evaluated; `XLOOKUP` defers `if_not_found` the same way, so a failing fallback never poisons a hit |
| Information | `ISLOGICAL`, `ISNONTEXT`, `ISEVEN`, `ISODD`, `ERROR.TYPE`, `N`, `T`, `CLEAN` | `ERROR.TYPE` and the `IS*` additions classify an error instead of propagating it |
| Math | `SIGN`, `GCD`, `LCM`, `SUMSQ`, `SUMX2MY2`, `SUMX2PY2`, `SUMXY` | `GCD`/`LCM` truncate toward zero, reject a magnitude above 2^53, and read their arguments one at a time, so an earlier argument's `#NUM!` beats a later `#VALUE!` (`GCD(-1, "x")` is `#NUM!`) |
| Trig | `ATAN2`, `SINH`, `COSH`, `TANH` | an overflowing `SINH`/`COSH` is `#NUM!`, not infinity |
| Date | `HOUR`, `MINUTE`, `SECOND`, `TIME`, `DATEVALUE`, `TODAY`, `NOW` | see the clock note below |
| Statistics | `MEDIAN`, `LARGE`, `SMALL`, `RANK`, `STDEV`, `VAR`, `MODE`, `PERCENTILE`, `QUARTILE`, `AVERAGEA`, `MAXA`, `MINA` | pooled over the numbers the aggregates already collect; `LARGE`/`SMALL`/`RANK`/`PERCENTILE`/`QUARTILE` read their arguments separately |
| Descriptive | `AVEDEV`, `DEVSQ`, `GEOMEAN`, `HARMEAN`, `VARP`, `STDEVP`, `TRIMMEAN`, `STANDARDIZE`, `PERCENTRANK` | pooled like the statistics above, except `TRIMMEAN`/`STANDARDIZE`/`PERCENTRANK` keep their scalar arguments apart; `GEOMEAN`/`HARMEAN` reject a non-positive domain with `#NUM!` |
| Regression | `CORREL`, `RSQ`, `SLOPE`, `INTERCEPT`, `FORECAST`, `STEYX` | paired numeric streams with the client engine's pairing rule (text dropped per side, mismatched lengths are `#N/A`); a constant predictor is `#DIV/0!` |
| Financial | `PMT`, `FV`, `PV`, `NPV`, `NPER`, `RATE`, `IPMT`, `PPMT`, `IRR`, `MIRR`, `XNPV`, `SLN`, `SYD`, `DB` | `RATE` is a Newton iteration with a bisection fallback; `IRR`/`MIRR`/`XNPV` flatten a cash-flow range, and a result off the real line is `#NUM!` |
| Distributions | `NORMDIST`, `NORMSDIST`, `NORMINV`, `NORMSINV`, `NORMINVS`, `BINOMDIST`, `POISSON`, `EXPONDIST`, `WEIBULL`, `GAMMALN`, `GAMMADIST`, `GAMMAINV`, `BETADIST`, `BETAINV`, `NEGBINOMDIST`, `FREQUENCY` | the incomplete gamma/beta integrals are ported from the client engine and kept numerically identical; `FREQUENCY` returns a column of counts |
| Combinatorics | `COMBIN`, `COMBINA`, `PERMUT`, `FACT`, `FACTDOUBLE`, `MULTINOMIAL` | the integer helpers stop at the double overflow and report `#NUM!` |
| Complex | `COMPLEX` and the `IM*` family (`IMSUM`, `IMDIV`, the trig and hyperbolic forms, …) | the complex-number text form and its 15-significant-digit coefficients are reproduced byte-for-byte from the client engine, and the `i`/`j` suffix survives the call |
| Engineering | the base conversions (`BIN2DEC` … `OCT2HEX`), the bitwise operations (`BITAND`, `BITLSHIFT`, …), `DELTA`, `GESTEP`, `ERF`/`ERFC`, `BESSELJ`/`BESSELY` and `CONVERT` | the two's-complement widths, the ignored `places` on the `*2DEC` forms, the 32-bit wrap past 2^31 and the float rendering of fractional inputs are reproduced from the client engine (see the divergences above); `ERF` runs Abramowitz & Stegun 7.1.26 |

`TODAY` and `NOW` read the clock, so they are the one exception to the
"deterministic" guarantee above: their answer depends on when the formula is
evaluated, which is also why the shared parity table does not pin them. The
distribution and financial functions are deterministic too — the inverses solve
by bisection rather than by a random search, and `RATE` backs its Newton
iteration with a bracketed bisection so a bad guess cannot diverge.

**Adopt when a feature asks for it.** The rest of the statistical
`*DIST`/`*INV` family (`TDIST`, `CHIDIST`, `FDIST`, …),
`LINEST`/`LOGEST`/`TREND`/`GROWTH`, and the
dynamic-array family (`SORT`, `FILTER`, `HSTACK`, `TAKE`, `WRAPROWS`, …). Each
needs design decisions this engine has not made yet — array results, iteration,
repeated sampling — so they should arrive with the feature that wants them
rather than as speculative surface. The scalar regressions (`CORREL`, `RSQ`,
`SLOPE`, `INTERCEPT`, `FORECAST`, `STEYX`) used to sit here too; they are
adopted — see the Regression row above.

**Leave out.** The volatile `RAND`/`RANDBETWEEN`/`RANDARRAY` trio (this engine
is deterministic outside `TODAY`/`NOW`), the introspective forms (`ISREF`,
`ISFORMULA`, `SHEET`/`SHEETS`, `INFO`, `ISOMITTED`) and the array-plumbing
wrappers (`ARRAYFORMULA`, `ANDARRAY`, `ORARRAY`): a large surface with narrow
use on a read path, and the client engine covers them where a formula needs
one. The engineering conversions and the complex arithmetic used to sit here
too; they are adopted — see the Engineering and Complex rows above.

The lexer accepts a name that is shaped like a cell reference (`ATAN2`, which is
both) and resolves it as a call when a parenthesis follows, and it accepts a dot
in a name, so adopting a name from the list above does not need a lexer change
first.

The engine is also bounded where Excel is not: `REPT`, `NETWORKDAYS` and
`WORKDAY` refuse to materialise a huge string or iterate for hours
(`MAX_REPT_CHARS`, `MAX_NETWORKDAYS_SPAN_DAYS`, `MAX_WORKDAY_OFFSET_DAYS`), and
return `#VALUE!`/`#NUM!` so a single formula cannot stall a viewport read. The
`REPT` cap counts UTF-16 code units -- the same units `LEN` reports -- and the
client engine enforces the identical limit, so both engines refuse the same
calls (`REPT("x", 32768)` and `REPT("😀", 16384)` are both `#VALUE!`). A
date serial outside the range a date can name, or a `DATE` year outside
0..9999, is `#NUM!` as well.
A multi-cell `OFFSET` is one reference in Excel; here it is a flat value list
(the client engine represents it as a row-major matrix). `INDEX`'s "whole
column"/"whole row" forms (`INDEX(A1:C3, 0, 2)`) arrive the same way, so the
two shapes still add up identically under `SUM`.
A one-cell reference is the 1×1 array Excel makes it, so `MATCH(1, A1, 0)`,
`LOOKUP(1, A1, A1)`, `VLOOKUP(1, A1, 1, FALSE)`, `HLOOKUP(1, A1, 1, FALSE)`
and `SUMPRODUCT(A1)` read that cell rather than reporting a miss.

## Configuration

Key constants in `_shared.py`:

| Constant | Default | Purpose |
|---|---|---|
| `MAX_SNAPSHOTS_KEPT` | 50 | Snapshots garbage-collected beyond this count |
| `REVISION_KEEP_COUNT` | 5 | Immutable revision blobs retained per workbook |
| `WORKBOOK_CACHE_MAX_ENTRIES` | 32 | Max cached workbook files per process |
| `WORKBOOK_CACHE_MAX_BYTES` | 256 MB | Max memory for cached workbook files |
| `SUM_SESSION_MAX_ENTRIES` | 32 | Live in-memory workbook copies per process |
| `SUM_FLUSH_MAX_OPS` | 25 | Unflushed ops before forced flush |
| `SUM_FLUSH_MAX_MS` | 2000 | Age (ms) of oldest unflushed op before flush |
| `MAX_CSV_ROWS` | 10,000 | Max rows for CSV import |
| `MAX_CSV_COLUMNS` | 100 | Max columns for CSV import |
| `PRESENCE_TTL_SECONDS` | 30 | Presence heartbeat expiry |

## Container format

The `.plexisum` binary container (`container.py:1-184`):

```
Header (16 bytes):
  magic:           b"PXSM"       (4 bytes)
  format_version:  u8 = 1        (1 byte)
  compression:     u8 (0/1)      (1 byte)
  flags:           u8            (1 byte)
  reserved:        zeroed        (9 bytes)

Body:  zstd-compressed (or raw) SQLite payload
Footer (32 bytes): SHA-256 checksum of uncompressed payload
```

### Engine-to-engine differences

**Per argument, none.** The scalar-slot range rule above is pinned
(`LEN(A1:A3)` is `#VALUE!` and `LEN(A1:A5)` is `#DIV/0!` on both sides), and so
are the arity-first rule, the propagation of an error operand and the per-slot
range rule -- the shared case table holds the engines to all of them. The
exhaustive differential sweep (`tests/unit/formula-sweep.test.mjs` in the
client repository) calls every registered function at arities zero through
four over numbers, text, an error, a blank, a logical, a cell, a multi-cell
range and the error cell, and now agrees on every one. It *repeats one operand
at every slot*, though, so it never compares two different operands in one
call. Closing its last gaps needed both engines:

- the probes compared a **matrix result** (`FREQUENCY`) by identity, so two
  matching matrices never read as equal. They now compare a matrix element by
  element, which exposed -- and let the shared table pin -- a real
  disagreement: this engine dropped a computed error operand
  (`FREQUENCY(1/0, 1/0)` answered `[0]`) where the client propagated
  `#DIV/0!`. `FREQUENCY` reads its two arrays through the same
  provenance-aware rule as the criteria functions, so an error written into
  the formula propagates while an error cell in a referenced range is skipped.
- the branching family (`IF`, `IFERROR`, `IFNA`, `IFS`, `SWITCH`, `CHOOSE`)
  reads **every** slot -- conditions, branch values, search keys, results and
  the fallback -- through the pooled rule; the client engine's branches used
  to return a raw matrix for a range result where this engine answers the
  error.

**Mixed operands, no open class.** A second, mixed-operand sweep calls each
covered function with two different operands (and a rotating triple), which
the per-arity sweep never does -- it repeats one operand at every slot. Every
covered function now agrees, so there is no allowance left and a divergence in
any of them fails the run.

Closing it needed the argument model to match the client engine's. That engine
evaluates each argument eagerly and each function reads its arguments as
*slots*, coercing one as it goes (its `scalarize`/`toNumber`): a one-cell
reference or expression is that value, an error in a slot propagates when the
body reads it, and a multi-cell range where one value belongs is `#VALUE!`.
This engine pooled every argument into one flattened value list, so an error
cell in a later range was answered before the body read an earlier slot
(`BESSELJ("x", A1:A5)` was the range's `#DIV/0!` where the client answers the
first slot's `#VALUE!`). Three changes align the two:

- the scalar families -- the engineering and complex sets and the date shifts
  -- now receive one entry per argument, read through `_scalar_slot`, and their
  bodies coerce each slot in client order (`ROUND` reads its digits first, the
  base conversions scalarize their input before the places).
- a flat range reader (`RANK`, `PERCENTRANK`, `FORECAST`, `SUMPRODUCT`) defers
  *every* reference but still evaluates its scalar arguments eagerly, so an
  error among them propagates in argument order
  (`_FLAT_ARGUMENT_ERROR_FUNCTIONS`).
- the coercions propagate: `_to_number` re-raises an error value (and
  scalarizes a range) instead of reading its spelling as text, so a body that
  reads a slot holding an error answers the error rather than `#VALUE!`.

The shared case table pins each direction of the slot/range precedence, the
multi-cell lookup value that compares equal to nothing, and the aggregates'
shape check before an error cell.

**Nested calls and three operands.** Two shapes the sweeps above never build
were added: every covered function called with *three* distinct operands (all
7^3 orderings, not the rotating triple) and with a nested call in a slot
(`COUNT(ABS("x"))`). The nested shape found and fixed a real defect:

- a **nested call that fails** now yields its error *value*. Many bodies raise
  a ``ValueError`` / ``ZeroDivisionError`` / ``OverflowError``, and that
  exception used to unwind straight past the enclosing function, so an
  error-tolerant caller could not classify or skip it: ``COUNT(ABS("x"))`` was
  ``#VALUE!`` rather than 0, ``ISERROR(ABS("x"))`` was ``#VALUE!`` rather than
  TRUE and ``PI(ABS("x"))`` was ``#VALUE!`` rather than pi. The ``_call``
  wrapper turns a raised error into the call's value, and the dependency-budget
  abort raises a distinct ``_DependencyBudgetExceeded`` so a runaway cycle still
  unwinds the whole formula instead of becoming a skippable value. The shared
  table pins the classified answers (`COUNT`, the ``IS*`` family, ``TYPE``,
  ``PI``, ``IFERROR``).

The **three-operand shape** is now closed too: the mixed-operand sweep's
rotating triple never compares three *different* operands, and calling every
function with all 7^3 orderings found 89 disagreements, all of them a
per-family slot-coercion order the pooled reader got wrong. Each family now
reads its slots the way the client engine does, and the shared table pins a
representative case from every one:

- ``MID``/``DATE``/``TIME`` join the scalar-slot family, so a coercion failure
  in an earlier slot beats an error cell in a later range
  (``MID(1, "x", A1:A5)`` is ``#VALUE!``) while a *written* error argument still
  propagates in argument order (``TIME(A1:A5, "x", NA())`` is ``#N/A``).
- ``OFFSET`` coerces its row offset before it reads the column offset
  (``OFFSET(A1, "x", 1/0)`` is ``#VALUE!``).
- ``VLOOKUP``/``HLOOKUP`` read the lookup value as one slot -- an error cell in
  a multi-cell value propagates and a multi-cell value of plain values is
  ``#VALUE!`` -- instead of keeping it a matrix that matched nothing.
- ``XLOOKUP`` settles the lookup array, the return array and their shape first
  and only then surfaces a multi-cell lookup value's error, matching the client
  engine's order (``XLOOKUP(A1:A5, 1, 1)`` is ``#DIV/0!``, ``XLOOKUP(A1:A5, 1,
  NA())`` is ``#N/A``, ``XLOOKUP(A1:A5, 1, A1:A5)`` is ``#VALUE!``).
- the criteria pair propagates a *written* criteria error first
  (``SUMIF(1, 1/0, NA())`` is ``#DIV/0!``), then the summed range's own error,
  and only then a criteria read out of a cell (``SUMIF(1, A5, NA())`` is
  ``#N/A`` while ``SUMIF(1, A5, 1)`` is ``#DIV/0!``).

A nested-call error that a function *returns* is a value the enclosing
function can classify (``COUNT(ABS("x"))`` is 0), the same rule the ``_call``
wrapper enforces.

**Deeper shapes.** Four more shapes -- four distinct operands, a call nested
two deep, a reference-producing nested call (``OFFSET``/``INDEX``/``INDIRECT``)
and the empty-argument forms (``LEFT("abc",)``) -- were checked against the
client engine and agree. They exposed one last defect there: ``XLOOKUP`` did
not propagate a *computed* scalar error in its lookup or return array,
answering ``#N/A`` for ``XLOOKUP(1, SUM(A1:A5), 1)`` where every other lookup --
and this engine -- answer ``#DIV/0!``. The client now propagates one eagerly
while a plain reference (and the identity ``+A5``) keeps its error as a cell,
the ``_computed_range_error`` rule this engine already used. The client also
now reads the lookup array, and answers its error, *before* it evaluates the
return array -- the order this engine reads them -- so a return array that
*raises* no longer pre-empts the lookup array's error
(``XLOOKUP(0, 1/0, LCM(-1, 1))`` is ``#DIV/0!``, not ``LCM``'s ``#NUM!``). The
shared table pins both directions.

**Array-valued arguments.** Calling each function with an array-valued
argument -- ``FREQUENCY``'s matrix and the ranges ``OFFSET``/``INDEX``/
``INDIRECT`` produce -- in *every* slot, not only the first, found one more
family of disagreements. This engine already had the client engine's
``scalarize`` rule (``_scalar_slot``) but did not apply it to the branching
family's slots, the criteria operands or ``NUMBERVALUE``, and its fixed-arity
slots read a plain multi-cell range's first element where the client answers
the pooled error. ``_scalar_or`` now applies that rule to those slots,
``INDIRECT`` scalarizes the reference text it reads, ``XLOOKUP`` reads a plain
multi-cell lookup value as ``#VALUE!``, ``PERCENTRANK`` rounds half away from
zero like the client's ``Math.round`` (Python's ``round`` is banker's),
``INDEX``/``MATCH``/``LOOKUP`` preserve a vector's shape instead of collapsing
a single-row range to its first element, and ``INDEX`` keeps a multi-cell
block's shape instead of flattening it. The differential now reads 0
divergences across 15,118 array-argument forms.

``VLOOKUP`` is the same shape question from the other side, and the client was
the one out: it wrapped each cell of a flat table -- a function-returned row such
as ``INDEX(A1:C3,2,0)`` or ``OFFSET(A1,1,0,1,3)`` -- into its own row, so a
vertical lookup scanned the whole row as keys and measured its width as one,
answering ``two`` and ``#REF!`` where the identical range (``A2:C2``) and this
engine answer ``#N/A`` and the value. A flat table now reads as a single row,
the rule ``HLOOKUP`` already used, so ``VLOOKUP("two", INDEX(A1:C3,2,0), 1)``
is ``#N/A`` and ``VLOOKUP(2, INDEX(A1:C3,2,0), 2)`` is ``two`` on both engines.
The shared table pins each answer: the multi-producer sweep that found it
(13,950 producer-in-slot shapes, including a pair of distinct producers in two
slots) now reads 0 divergences.

**A bare range expression.** A top-level formula that is just a range
(``A1:A5``) was evaluated with the scalar-slot rule, so it read as its error
cell, while the client returns the matrix. ``evaluate_cell`` now returns the
matrix for a bare range, so ``A1:A5``, ``+A1:A5`` and ``(A1:A5)`` all spill the
same way on both engines.

**The fixed-arity text bodies read slots in body order.** The same scalar-slot
rule the numeric families follow applies to the text functions, but their
bodies read the slots in their own order -- ``TEXT`` reads its format before
its value, ``NUMBERVALUE`` its separators before its text, ``LEFT`` its text
before its count -- and a text slot's error has to surface when the body reads
it. The bodies now read text through ``_slot_text`` (which propagates an error
value) while ``_to_number`` already raises, so ``LEFT(A1:A3, A1:A5)`` is the
first slot's #VALUE! and ``TEXT(A1:A5, A1:A3)`` the format slot's #VALUE!.
``MID`` and the date constructors use the same path.

**Criteria and sum ranges align by flat index.** Calling each function with an
array-valued argument in *every* slot found the criteria/sum ranges aligning by
(row, column) where the client flattens both row-major and aligns by flat
index, so a function-returned matrix
(``SUMIF(FREQUENCY(A1:A5, A1:A5), 1, A1:A5)``) or a row against a column read
only the first element. SUMIF/AVERAGEIF and the ``*IFS`` family now align flat
(``SUMIF(A1:C1, 10, A1:A3)`` is 3, not 1). The ``*IF``/``*IFS`` family also
propagates a *written* error argument eagerly -- the client evaluates a
non-range argument before the body -- so ``COUNTIFS(A1:A3, A1:A3, 1, 1/0)`` is
#DIV/0!, while a criteria range stays deferred.

**A range operand in a binary operator is scalarized.** Outside a function
call the operator keeps a range operand a matrix until it applies it, so the
other operand's error is not masked (``A1:A3 + A5`` is A5's #DIV/0!) and the
operator then scalarizes it (``A1:A3 = A1:A3`` and ``A1:A3 & 1`` are the
matrix's #VALUE!, not a bare FALSE or a joined string).

**A binary operator is left-first, and the right operand is not evaluated
once the left has failed.** An error *cell* on the left (``A5``) beats a
*thrown* failure on the right (``NA()``, ``ABS("x")``), so ``A5 + NA()`` is
#DIV/0! on both engines. The client used to evaluate the right operand
eagerly, so a throw unwound before its left-first error check -- the last open
operand-ordering class -- and ``A5 + NA()`` was #N/A there.

**MATCH reads its exact-match key as one scalar slot.** ``MATCH(A1:A5,
A1:A5, 0)`` propagates the key's error cell (#DIV/0!) and ``MATCH(A1:A2,
A1:A5, 0)`` is #VALUE!, the client's ``scalarize`` rule. The approximate
branches rank the key instead, so ``MATCH(A1:A5, "x")`` stays #N/A. LOOKUP and
XLOOKUP keep their own key rule -- a multi-cell value compares equal to
nothing and the body answers #VALUE! -- which the client shares.

## Related code

- `src/core/suite/sum/__init__.py:25` — `setup()` entry point
- `src/core/suite/sum/schema.py:37` — Main database DDL
- `src/core/suite/sum/schema.py:370` — Workbook file DDL
- `src/core/suite/sum/container.py:91` — `encode_container()` / `decode_container()`
- `src/core/suite/sum/segments.py:241` — `SegmentEncoder.encode_segment()`
- `src/core/suite/sum/formulas.py:48` — `compute_value()` top-level entry
- `src/core/suite/sum/manager.py:73` — `SumManager` class definition
- `src/core/suite/sum/_shared.py:53` — `_BoundedFileCache` implementation
