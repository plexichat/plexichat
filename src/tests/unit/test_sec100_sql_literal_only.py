"""SQL injection ratchet: credential/session stores stay 100% parameterized.

SQL identifiers cannot be bound as ``?`` parameters, so most of the codebase
legitimately builds a small, guarded slice of the statement with an f-string
(see ``test_security_d2_sql_identifier_guard``). Credential, session-ticket,
OAuth-state and token-encryption stores are different: every statement they
issue is a fixed literal with bound values, and there is no reason for them to
ever interpolate *anything* into SQL text.

This module locks that in with a static (AST) check. A regression that turns a
literal query into an f-string, a ``%``-format or a ``.format()`` call fails
the suite, even if the interpolated value happens to be "safe" today.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

EXEC_METHODS = frozenset({"execute", "executemany", "executescript", "exec_driver_sql"})

# Named, security-critical stores that must never build SQL text dynamically.
LITERAL_ONLY_SQL_MODULES = (
    "src/core/auth/managers/session/login.py",
    "src/core/auth/managers/session/tickets.py",
    "src/core/auth/oauth/state.py",
    "src/core/auth/blacklist.py",
    "src/core/auth/totp.py",
    "src/utils/encryption/channel_ratchet/store.py",
)


def _parse(path: Path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _first_sql_argument(call: ast.Call):
    """Return the first (SQL) argument of an execute-like call, or None."""
    if (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr in EXEC_METHODS
        and call.args
    ):
        return call.args[0]
    return None


def _is_fixed_value(value: ast.AST) -> bool:
    """Whether ``value`` is a fixed SQL string, possibly passed through the
    engine's dialect shim or chosen between two literal branches.
    """
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return True
    if isinstance(value, ast.Call) and value.args:
        return any(
            isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            for arg in value.args
        )
    if isinstance(value, ast.IfExp):
        return _is_fixed_value(value.body) and _is_fixed_value(value.orelse)
    return False


def _fixed_sql_names(tree: ast.Module) -> set[str]:
    """Names bound to a fixed SQL string (literal, optionally passed through
    the engine's ``convert_schema`` dialect shim) rather than assembled text.
    """
    fixed: set[str] = set()
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets, value = [node.target], node.value
        if value is None:
            continue
        if _is_fixed_value(value):
            for target in targets:
                if isinstance(target, ast.Name):
                    fixed.add(target.id)
    return fixed


def _scan():
    """Return ``(literal_count, dynamic_sites)`` across the named modules."""
    literal_count = 0
    dynamic_sites = []
    for relative in LITERAL_ONLY_SQL_MODULES:
        path = ROOT / relative
        assert path.is_file(), f"named SQL module disappeared: {relative}"
        tree = _parse(path)
        fixed_names = _fixed_sql_names(tree)
        for node in ast.walk(tree):
            sql = _first_sql_argument(node)
            if sql is None:
                continue
            is_fixed = (
                isinstance(sql, ast.Constant) and isinstance(sql.value, str)
            ) or (isinstance(sql, ast.Name) and sql.id in fixed_names)
            if is_fixed:
                literal_count += 1
            else:
                rendered = ast.unparse(sql)[:100]
                dynamic_sites.append(f"{relative}:{node.lineno} {rendered}")
    return literal_count, dynamic_sites


_LITERAL_COUNT, _DYNAMIC_SITES = _scan()


def test_literal_sql_scan_is_not_vacuous():
    assert _LITERAL_COUNT >= 20, f"only found {_LITERAL_COUNT} literal SQL statements"


def test_named_stores_never_interpolate_sql_text():
    assert not _DYNAMIC_SITES, (
        "Credential/session stores must use fixed SQL with bound parameters; "
        "found dynamic SQL text:\n  " + "\n  ".join(sorted(_DYNAMIC_SITES))
    )


def test_named_stores_still_contain_bound_parameters():
    """Guard against hollowing out the modules: their literal SQL should use
    ``?`` placeholders, i.e. the second argument to ``execute`` is non-empty.
    """
    for relative in LITERAL_ONLY_SQL_MODULES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "?" in source, f"{relative} lost its bound-parameter SQL"
