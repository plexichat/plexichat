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
- `COUNTBLANK` counts an error cell as non-blank;
- the `IS*` family and `TYPE` report `FALSE` and `16`.

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

**A lookup key compares the way `=` does.** `VLOOKUP`, `HLOOKUP` and `MATCH`
match the text `"1"` against the number 1, as Excel's `=` does, and an
approximate match whose lookup value does not read as a number is a miss
(`#N/A`) rather than an error; a `row_index_num` past the bottom of the table
is `#REF!`.

**Text compares case-insensitively.** Excel's `=` ignores case — `"a" = "A"` is
`TRUE`, and `EXACT` is the case-sensitive test — so the `=`/`<>` operators, the
lookup keys (`MATCH("TWO", …)` finds the text `two`) and `SWITCH`'s cases all
match without regard to case. The criteria comparisons already did.

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
text `"2"` and `+TRUE` is `TRUE`.

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
| single-argument `ROUND`/`ROUNDUP`/`ROUNDDOWN` | a formula error | `#VALUE!` — `num_digits` is required |
| `SINH`/`COSH` of an argument past ~710 | `#NUM!` | `#NUM!` — an overflowing double is not a cell value |
| `ERF`/`ERFC` | precise to ~1e-16 | Abramowitz & Stegun 7.1.26 (~1.5e-7), shared with the client engine |
| fractional `DEC2BIN`/`DEC2HEX`/`DEC2OCT` inputs | truncated to an integer | rendered through the float base conversion both engines share (e.g. `DEC2BIN(5.9)` is `"101.9000000000000004"`) |
| `BITAND`/`BITOR`/`BITXOR` past 2^31 | full 48-bit result | 32-bit wrap, shared with the client engine |
| an error stored in a statistical range (`MEDIAN(A1:A3)`) | `#DIV/0!` | the cell is skipped like text; an error *written into* the formula still propagates |
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
| Text | `CHAR`, `CODE` | `CHAR` truncates toward zero and rejects a code outside 1-255 with `#VALUE!`; `CODE` reads the first character and rejects empty text |

| Family | Functions | Note |
|---|---|---|
| Logical | `TRUE`, `FALSE` | zero-argument call forms of the literals; a stray argument is `#VALUE!` |
| Lookup | `CHOOSE`, `IFS`, `SWITCH`, `XLOOKUP` | `CHOOSE`/`IFS`/`SWITCH` are evaluated on the `IF`/`IFNA` short-circuit path, so an untaken branch is not evaluated; `XLOOKUP` defers `if_not_found` the same way, so a failing fallback never poisons a hit |
| Information | `ISLOGICAL`, `ISNONTEXT`, `ISEVEN`, `ISODD`, `ERROR.TYPE`, `N`, `T`, `CLEAN` | `ERROR.TYPE` and the `IS*` additions classify an error instead of propagating it |
| Math | `SIGN`, `GCD`, `LCM`, `SUMSQ`, `SUMX2MY2`, `SUMX2PY2`, `SUMXY` | `GCD`/`LCM` truncate toward zero and reject a magnitude above 2^53 |
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
return `#VALUE!`/`#NUM!` so a single formula cannot stall a viewport read. A
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

## Related code

- `src/core/suite/sum/__init__.py:25` — `setup()` entry point
- `src/core/suite/sum/schema.py:37` — Main database DDL
- `src/core/suite/sum/schema.py:370` — Workbook file DDL
- `src/core/suite/sum/container.py:91` — `encode_container()` / `decode_container()`
- `src/core/suite/sum/segments.py:241` — `SegmentEncoder.encode_segment()`
- `src/core/suite/sum/formulas.py:48` — `compute_value()` top-level entry
- `src/core/suite/sum/manager.py:73` — `SumManager` class definition
- `src/core/suite/sum/_shared.py:53` — `_BoundedFileCache` implementation
