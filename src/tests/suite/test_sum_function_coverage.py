"""Coverage: every function the client engine has is either pinned or absent.

The shared case table names a function only by *using* it, so a function nobody
wrote a case for is invisible -- the table could quietly lose one and every
test would still pass. This holds the two inventories together: the names the
client engine registers (read from its registry under node) and the names the
table calls. A name in neither column fails, so

* a function both engines implement has to appear in the table, where a change
  to it has something to fail;
* a function deliberately missing from the server engine has to be in
  ``CLIENT_ONLY``, the gap the engine README documents, so losing one from that
  gap is as loud as adding one silently;
* ``NOW``/``TODAY`` read the clock, so they cannot be pinned at all and are
  named as such.

The second check runs the other way: every name the table calls has to be
implemented on this side. A name the table ever expects to be ``#NAME?`` is the
deliberate "unknown name" pin (``FOO(1)``), so it is left out of the
implementation check rather than counted as a function.

The client engine lives in the other repository, so this skips with a reason
when node or the client checkout is missing, exactly as the other differential
checks do.
"""

import json
import re
import shutil
import subprocess
from typing import List, Set

import pytest

from src.tests.suite.test_sum_cross_engine import (
    PROBE_PATH,
    TABLE_PATH,
    _client_engine,
    _evaluate,
)

# The dynamic-array family, the regression machinery, the introspective forms
# and the volatile trio: implemented in the client engine, ``#NAME?`` here (see
# the engine README's "functions the client engine has that this one does
# not"). One name per line so a deletion is visible in the diff.
CLIENT_ONLY = frozenset(
    {
        "ANDARRAY",
        "ARRAYFORMULA",
        "CHOOSECOLS",
        "CHOOSEROWS",
        "DROP",
        "EXPAND",
        "FILTER",
        "GROWTH",
        "HSTACK",
        "INFO",
        "ISFORMULA",
        "ISOMITTED",
        "ISREF",
        "LAMBDA",
        "LET",
        "LINEST",
        "LOGEST",
        "ORARRAY",
        "RAND",
        "RANDARRAY",
        "RANDBETWEEN",
        "SEQUENCE",
        "SHEET",
        "SHEETS",
        "SORT",
        "TAKE",
        "TOCOL",
        "TOROW",
        "TREND",
        "UNIQUE",
        "VSTACK",
        "WRAPCOLS",
        "WRAPROWS",
    }
)

# The two functions that read the wall clock. Both engines answer them (the
# server engine has them in its dispatch), but the answer changes between two
# evaluations of the same formula, so no case can pin it.
CLOCK = frozenset({"NOW", "TODAY"})

# Names the table pins as deliberately unknown -- a formula calling a function
# nobody defines -- so their cases expect ``#NAME?`` and there is nothing to
# implement for them. ``ERROR.TYPE(FOO())`` is still 5. A newly pinned unknown
# name shows up here as a failure rather than as a silent hole.
UNKNOWN_NAMES = frozenset({"FOO", "FOO.BAR"})


def _client_functions(node: str) -> List[str]:
    """The names the client engine registers, read from its own registry."""
    completed = subprocess.run(
        [node, str(PROBE_PATH), str(TABLE_PATH), str(_client_engine())],
        input=json.dumps({"table": str(TABLE_PATH), "functions": True}),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, "the client probe failed:\n" + (
        completed.stderr or completed.stdout
    )
    return json.loads(completed.stdout)["functions"]


def _table_names() -> Set[str]:
    """The names the table calls anywhere in any formula.

    Nested calls count too: ``SUM(A1:A3, FOO())`` attaches ``FOO`` to the table
    as surely as a case of its own does, so the coverage check sees every name
    the contract mentions.
    """
    table = json.loads(TABLE_PATH.read_text())
    names: Set[str] = set()
    for case in table["cases"]:
        for match in re.findall(r"([A-Za-z_][A-Za-z0-9._]*)\s*\(", case["formula"]):
            names.add(match.upper())
    return names


def _pinned() -> Set[str]:
    """The functions the table pins, without the deliberate unknown names."""
    return _table_names() - UNKNOWN_NAMES


def test_every_client_function_is_pinned_or_documented():
    """No function may fall between the table and the documented gap."""
    node = shutil.which("node")
    engine = _client_engine()
    if node is None or engine is None:
        pytest.skip(
            "the function coverage check needs node and the plexichat-client "
            "checkout; set PLEXICHAT_CLIENT_PATH when the two are not siblings"
        )

    names = _client_functions(node)
    pinned = _table_names()
    unpinned = sorted(
        name
        for name in names
        if name not in pinned and name not in CLIENT_ONLY and name not in CLOCK
    )
    assert not unpinned, (
        f"{len(unpinned)} client function(s) are neither pinned in the shared "
        "case table nor listed as a deliberate gap: "
        + ", ".join(unpinned)
        + ". Add cases to formula_parity_cases.json (mirrored into the client "
        "copy, with both TABLE_SHA256 digests updated), or add the name to "
        "CLIENT_ONLY when the server engine is meant not to implement it."
    )


def test_every_pinned_function_is_implemented_here():
    """The table may only call names this engine answers."""
    missing = sorted(
        name
        for name in _pinned()
        if (lambda result: result["kind"] == "error" and result["value"] == "#NAME?")(
            _evaluate(f"{name}(1)")
        )
    )
    assert not missing, (
        f"{len(missing)} function(s) the shared case table calls answer #NAME? "
        "here: " + ", ".join(missing)
    )
