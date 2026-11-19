"""SEC400 invariant: CSV transcript cells are formula-injection neutralised.

Message content and ids in a downloaded CSV are attacker-authored free text. A
cell beginning with ``=``, ``+``, ``-``, ``@`` (or a stripped tab/CR) is
evaluated as a formula by Excel/Sheets, so the transcript export fix (commit
5dc811d3) routed every data cell through ``_csv_cell``, which prefixes such
values with an apostrophe. This suite is a static ratchet over that export
module: a future writerow that splices a raw ``msg.get(...)`` value into the CSV
fails the build.

Pure AST over checked-in source -- no imports, no DB. The cell classifier is
validated against synthetic good/bad rows before the real module is scanned.
"""

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]
EXPORT = "src/core/messaging/services/export.py"
NEUTRALIZER = "_csv_cell"


def _call_name(node):
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _contains_neutralizer(node) -> bool:
    return any(_call_name(n) == NEUTRALIZER for n in ast.walk(node))


def _is_literal(node) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _cell_is_safe(cell) -> bool:
    """A CSV cell is safe when it is a fixed literal or a neutralised value."""
    if _is_literal(cell):
        return True
    if _call_name(cell) == NEUTRALIZER:
        return True
    # ``_csv_cell(x) if cond else ""`` -- both outcomes are safe.
    if isinstance(cell, ast.IfExp):
        return _contains_neutralizer(cell.body) and _is_literal(cell.orelse)
    return False


def _writerow_cells(tree):
    """Yield the list of cell expressions for each ``writer.writerow([...])``."""
    rows = []
    for node in ast.walk(tree):
        if _call_name(node) != "writerow" or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.List):
            rows.append((node.lineno, list(arg.elts)))
    return rows


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


_TREE = ast.parse((ROOT / EXPORT).read_text(encoding="utf-8"))


def test_classifier_catches_raw_cells_and_accepts_neutralised_ones():
    """Self-test: the detector flags raw attacker data but not fixed cells."""
    bad = ast.parse(
        'writer.writerow(["id", msg.get("id"), msg.get("content"), msg.get("author_id")])'
    )
    good = ast.parse(
        'writer.writerow(["id", _csv_cell(msg.get("id")), '
        '_csv_cell(msg.get("content")), '
        '_csv_cell(msg.get("reply_to_id")) if msg.get("reply_to_id") else ""])'
    )
    _, bad_cells = _writerow_cells(bad)[0]
    _, good_cells = _writerow_cells(good)[0]
    assert [_cell_is_safe(c) for c in bad_cells] == [True, False, False, False]
    assert all(_cell_is_safe(c) for c in good_cells)


def test_export_scan_is_not_vacuous():
    rows = _writerow_cells(_TREE)
    assert len(rows) >= 2, f"only found {len(rows)} writerow calls in {EXPORT}"
    # The data-row writer must actually be neutralised, otherwise the
    # "no raw cells" assertion below would pass trivially.
    assert sum(1 for _, cells in rows if any(_contains_neutralizer(c) for c in cells)) >= 1
    assert _find_function(_TREE, NEUTRALIZER) is not None


def test_helper_lists_dangerous_leads_and_prefixes_them():
    """The neutraliser must still cover every spreadsheet formula trigger."""
    leads = None
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_CSV_FORMULA_LEADS":
                    leads = node.value
    assert leads is not None, "_CSV_FORMULA_LEADS catalogue disappeared"
    lead_values = [e.value for e in leads.elts if isinstance(e, ast.Constant)]
    for token in ("=", "+", "-", "@", "\t", "\r"):
        assert token in lead_values, f"formula lead {token!r} no longer neutralised"

    helper = _find_function(_TREE, NEUTRALIZER)
    assert helper is not None
    helper_src = ast.unparse(helper)
    assert NEUTRALIZER and "_CSV_FORMULA_LEADS" in helper_src
    assert "'" in helper_src, "neutraliser lost its apostrophe prefix"


def test_no_csv_cell_bypasses_formula_neutralisation():
    offenders = [
        f"{EXPORT}:{lineno}: {ast.unparse(cell)}"
        for lineno, cells in _writerow_cells(_TREE)
        for cell in cells
        if not _cell_is_safe(cell)
    ]
    assert not offenders, (
        "Raw attacker-controlled cell written to CSV (formula injection):\n  "
        + "\n  ".join(sorted(offenders))
    )
