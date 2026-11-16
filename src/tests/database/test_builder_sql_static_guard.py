"""Static guard against raw f-strings/concatenation in SQL construction.

Runtime tests only cover the statements a fixture happens to execute. This AST
audit covers every SQL-building expression in the three owned areas and fails
on a new interpolation that has not gone through the sanctioned helpers in
``sql_safety`` (which themselves delegate to ``require_identifier`` /
``bounded_int``). Only object names and bounded numeric fragments may be
interpolated; everything else must be a DB-API placeholder.
"""

import ast
import textwrap
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]

SCAN_PATHS = [
    SRC / "core/database/builder",
    SRC / "core/migrations/cloner.py",
    SRC / "core/search/indexer",
]

# The trusted choke point. Nothing outside sql_safety.py may invent a rule.
TRUSTED_FILE = "core/database/builder/sql_safety.py"

APPROVED_CALLS = {
    "sql_identifier",
    "sql_identifier_list",
    "sql_placeholders",
    "sql_assignments",
    "sql_limit",
    "sql_offset",
    "sql_operator",
    "sql_condition",
    "require_identifier",
    "bounded_int",
    "parse_id",
    "convert_placeholders",
    "build_where_clause",
}
APPROVED_METHODS = {"join", "format"}
STATEMENT_METHODS = {"execute", "fetch_all", "fetch_one", "execute_many"}
SQL_VARIABLES = {"sql", "query", "pg_sql", "insert_sql", "_DELETE_FROM", "safe_table"}
# Deliberately specific so log messages ("INSERT returned ...", "... DROP the
# stale ...") are not mistaken for SQL statements.
SQL_MARKERS = (
    "SELECT ",
    "INSERT INTO ",
    "DELETE FROM ",
    "UPDATE ",
    "FROM ",
    "WHERE ",
    "PRAGMA ",
    "LIMIT ",
    "OFFSET ",
    "RETURNING",
    " IN (",
    "CREATE TABLE ",
    "CREATE INDEX ",
    "ALTER TABLE ",
    "DROP TABLE ",
    "DROP INDEX ",
    "DROP TRIGGER ",
    "COUNT(",
    "setval(",
    "pg_get_serial_sequence",
)

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _call_name(node):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _iter_source_files():
    for base in SCAN_PATHS:
        if base.is_file():
            yield base
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def _module_string_constants(tree):
    """Module-level names bound to a plain string (SQL templates)."""
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
        return _static_text(node.func.value, constants)
    return ""


def _is_sql(node, constants, sql_nodes):
    if id(node) in sql_nodes:
        return True
    text = _static_text(node, constants)
    return any(marker in text for marker in SQL_MARKERS)


def _approved(node, safe_names):
    """Whether an interpolated expression is a sanctioned SQL fragment."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id in safe_names
    if isinstance(node, ast.Call):
        name = _call_name(node)
        return name in APPROVED_CALLS or name in APPROVED_METHODS
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
    """Fixed-point set of locals known to hold a validated SQL fragment."""
    safe = set(constants)
    assignments = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                assignments.append((_target_names(target), node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            assignments.append((_target_names(node.target), node.value))
        elif isinstance(node, ast.AugAssign):
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


def _ctx_sql_nodes(tree, constants):
    """Expressions used as a statement or assigned to a SQL variable."""
    nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in SQL_VARIABLES:
                    nodes.add(id(node.value))
        if isinstance(node, ast.Call) and _call_name(node) in STATEMENT_METHODS:
            if node.args:
                nodes.add(id(node.args[0]))
    return nodes


def _interpolations(node):
    """Sub-expressions interpolated into a SQL string expression."""
    if isinstance(node, ast.JoinedStr):
        return [
            part.value for part in node.values if isinstance(part, ast.FormattedValue)
        ]
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


def _candidate_nodes(tree):
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
    """Return offender descriptions for raw SQL interpolations in ``source``."""
    tree = ast.parse(source)
    constants = _module_string_constants(tree)
    safe = _safe_names(tree, constants)
    sql_nodes = _ctx_sql_nodes(tree, constants)
    offenders = []
    for node in _candidate_nodes(tree):
        if not _is_sql(node, constants, sql_nodes):
            continue
        for value in _interpolations(node):
            if not _approved(value, safe):
                offenders.append(f"{label}:{node.lineno}: {ast.unparse(value)}")
    return offenders


def _audit_repo():
    offenders = []
    for path in _iter_source_files():
        rel = str(path.relative_to(SRC))
        if rel == TRUSTED_FILE:
            continue  # the single trusted place allowed to assemble SQL text
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        offenders.extend(audit_source(source, rel))
    return offenders


def test_guard_recognises_raw_interpolation_shapes():
    """The audit must catch fresh f-strings, concatenation and .format()."""
    bad = """
        sql = f"SELECT * FROM users WHERE name = '{name}'"
        sql = "SELECT * FROM users WHERE name = " + name
        sql = "SELECT * FROM users WHERE name = '{}'".format(name)
    """
    offenders = audit_source(textwrap.dedent(bad), "synthetic")
    assert len(offenders) == 3, offenders
    assert all("name" in offender for offender in offenders)


def test_guard_accepts_sanctioned_interpolations():
    safe = """
        _DELETE_FROM = "DELETE FROM {table}"
        limit_value = sql_limit(limit)
        sql = f"SELECT * FROM {sql_identifier(table)} LIMIT {limit_value}"
        sql = _DELETE_FROM.format(table=sql_identifier(table))
        sql += " AND id IN (" + sql_placeholders(len(ids)) + ")"
    """
    assert audit_source(textwrap.dedent(safe), "synthetic") == []


def test_no_raw_interpolation_in_sql_construction():
    offenders = _audit_repo()
    assert offenders == [], (
        "Raw f-string/concatenation interpolation in SQL construction. Route "
        "identifiers through sql_identifier and numbers through sql_limit/"
        "sql_offset/sql_placeholders, and bind all values: " + ", ".join(offenders)
    )
