"""Deterministic invariants: query builders never interpolate raw values.

Two complementary checks are pinned here:

* a focused AST audit of a representative sample of builder modules fails if a
  new f-string / ``+`` / ``.format()`` interpolates anything that has not gone
  through the sanctioned ``sql_safety`` choke point;
* runtime checks prove that attacker-controlled values are bound as DB-API
  placeholders (never spliced into the SQL text) and that hostile identifiers
  are rejected.

The static audit is intentionally self-contained so it can be read and reasoned
about without importing another test module.
"""

import ast
import textwrap
from pathlib import Path
from typing import cast

import pytest

from src.core.database.builder.base import SQLInjectionError
from src.core.database.builder.delete import DeleteQuery
from src.core.database.builder.insert import InsertQuery
from src.core.database.builder.select import SelectQuery
from src.core.database.builder.update import UpdateQuery
from src.core.security.hardening import HardeningViolation

pytestmark = [pytest.mark.unit, pytest.mark.security]

SRC = Path(__file__).resolve().parents[2]

# A representative sample covering every statement shape built by the package.
SAMPLED_BUILDERS = [
    "core/database/builder/select.py",
    "core/database/builder/insert.py",
    "core/database/builder/update.py",
    "core/database/builder/delete.py",
    "core/database/builder/where.py",
    "core/database/builder/composer.py",
]

APPROVED_CALLS = {
    "sql_identifier",
    "sql_identifier_list",
    "sql_placeholders",
    "sql_assignments",
    "sql_limit",
    "sql_offset",
    "sql_operator",
    "sql_condition",
    "sql_finite_float",
    "bounded_int",
    "bounded_sql_int",
    "require_identifier",
    "convert_placeholders",
    "build_where_clause",
}
APPROVED_METHODS = {"join", "format"}
SQL_MARKERS = (
    "SELECT ",
    "INSERT INTO ",
    "DELETE FROM ",
    "UPDATE ",
    " WHERE ",
    " RETURNING",
    " LIMIT ",
    " OFFSET ",
    "SET ",
    "CREATE TABLE ",
    "DROP TABLE ",
)
SQL_NAMES = {"sql", "pg_sql", "query", "insert_sql", "_DELETE_FROM"}


def _call_name(node):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _module_constants(tree):
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    found[target.id] = node.value.value
    return found


def _static_text(node, constants):
    """Literal SQL text of a string expression, ignoring interpolations."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id, "")
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _static_text(node.left, constants) + _static_text(node.right, constants)
    if isinstance(node, ast.Call) and _call_name(node) == "format":
        return _static_text(cast(ast.Attribute, node.func).value, constants)
    return ""


def _approved(node, safe_names):
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id in safe_names
    if isinstance(node, ast.Call):
        return _call_name(node) in APPROVED_CALLS | APPROVED_METHODS
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _approved(node.left, safe_names) and _approved(node.right, safe_names)
    if isinstance(node, ast.JoinedStr):
        return all(
            _approved(part.value, safe_names)
            for part in node.values
            if isinstance(part, ast.FormattedValue)
        )
    return False


def _target_names(target):
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names = []
        for element in target.elts:
            names.extend(_target_names(element))
        return names
    return []


def _safe_names(tree, constants):
    """Fixed-point set of locals proven to hold sanctioned SQL fragments."""
    safe = set(constants) | set(SQL_NAMES)
    assignments = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                assignments.append((_target_names(target), node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            assignments.append((_target_names(node.target), node.value))
    changed = True
    while changed:
        changed = False
        for names, value in assignments:
            if names and _approved(value, safe):
                for name in names:
                    if name not in safe:
                        safe.add(name)
                        changed = True
    return safe


def _interpolations(node):
    if isinstance(node, ast.JoinedStr):
        return [p.value for p in node.values if isinstance(p, ast.FormattedValue)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts = []
        for operand in (node.left, node.right):
            if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
                continue
            parts.append(operand)
        return parts
    if isinstance(node, ast.Call) and _call_name(node) == "format":
        return list(node.args) + [kw.value for kw in node.keywords]
    return []


def _candidates(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            yield node
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            if isinstance(node.left, ast.Constant) and isinstance(
                node.right, ast.Constant
            ):
                continue
            yield node
        elif isinstance(node, ast.Call) and _call_name(node) == "format":
            yield node


def audit_source(source, label):
    """Return offender descriptions for raw interpolations into SQL text."""
    tree = ast.parse(source)
    constants = _module_constants(tree)
    safe = _safe_names(tree, constants)
    offenders = []
    for node in _candidates(tree):
        text = _static_text(node, constants)
        if not any(marker in text for marker in SQL_MARKERS):
            continue
        for value in _interpolations(node):
            if not _approved(value, safe):
                offenders.append(f"{label}:{node.lineno}: {ast.unparse(value)}")
    return offenders


def test_audit_detects_a_fresh_raw_interpolation():
    bad = "sql = f\"SELECT * FROM users WHERE name = '{name}'\""
    offenders = audit_source(bad, "synthetic")
    assert len(offenders) == 1, offenders
    assert "name" in offenders[0]


def test_audit_accepts_sanctioned_interpolations():
    good = """
        sql = f"SELECT * FROM {sql_identifier(table)} WHERE id = {sql_placeholders(1)}"
        sql += " AND name = ?"
    """
    assert audit_source(textwrap.dedent(good), "synthetic") == []


def test_no_raw_interpolation_in_sampled_builders():
    offenders = []
    for rel in SAMPLED_BUILDERS:
        path = SRC / rel
        offenders.extend(audit_source(path.read_text(encoding="utf-8"), rel))
    assert offenders == [], "Raw value interpolation in SQL: " + ", ".join(offenders)


# --- runtime: values are bound, identifiers validated -----------------------

EVIL = "x' OR '1'='1'; DROP TABLE users; --"


def _values(params):
    return [getattr(p, "value", p) for p in params]


def test_select_binds_filter_values_as_placeholders():
    sql, params = SelectQuery(None, "users").where("name", "=", EVIL).build()
    assert "WHERE" in sql and "?" in sql
    assert EVIL not in sql
    assert EVIL in _values(params)


def test_insert_binds_payload_values_as_placeholders():
    sql, params = InsertQuery(None, "users", {"name": EVIL}).build()
    assert sql.count("?") == 1
    assert EVIL not in sql
    assert EVIL in _values(params)


def test_update_and_delete_bind_values_and_require_a_where():
    with pytest.raises(ValueError):
        UpdateQuery(None, "users", {"name": EVIL}).build()
    with pytest.raises(ValueError):
        DeleteQuery(None, "users").build()

    sql, params = (
        UpdateQuery(None, "users", {"name": "safe"}).where("name", "=", EVIL).build()
    )
    assert "UPDATE" in sql and "WHERE" in sql and EVIL not in sql
    assert EVIL in _values(params)


def test_hostile_identifiers_are_rejected():
    with pytest.raises(SQLInjectionError):
        SelectQuery(None, EVIL).build()
    with pytest.raises(SQLInjectionError):
        InsertQuery(None, "users", {EVIL: "value"}).build()
    with pytest.raises(HardeningViolation):
        # The choke point itself refuses the injection at the lowest layer.
        from src.core.database.builder.sql_safety import sql_identifier

        sql_identifier(EVIL)
