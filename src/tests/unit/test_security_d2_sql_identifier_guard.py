"""AST audit: interpolated SQL identifiers must pass through a guard.

SQL parameters can be bound, but table/column/index names cannot. This module
walks every f-string handed to an execute-like call and classifies each
interpolated expression:

* an inline ``require_identifier`` / ``is_identifier`` / ``sql_identifier``
  call (the guard), or a variable bound from one;
* a literal (or literal catalogue) defined in code -- not request-derived;
* a bound placeholder / ``SET`` clause fragment / numerically bounded value --
  not an identifier at all;
* otherwise it is a violation and must appear in the fixed-catalogue allowlist
  with a justification.

Identifier-likeness is derived *structurally*, by resolving the interpolated
name to its canonical binding in the module, not by matching the name against a
fixed keyword list. The old keyword regex only inspected interpolations whose
variable name contained words such as ``table``/``column``/``name``; an
unguarded identifier bound straight from a request under a novel name (``tbl``,
``rel``, ``ident`` ...) was silently excluded -- a fixed name list bounding the
detector, and a fail-open whenever a name could not be classified. Resolution
is now all-bindings and fail closed: a name is safe only if *every* binding in
the module is a guard call or a literal catalogue, so a request-derived binding
cannot hide behind a safe same-named sibling in another scope.

A handful of interpolations cannot be classified from their binding alone (a
trusted internal SQL-fragment helper, a value validated against a literal
catalog, an internal bounded integer). Those live in ``NON_IDENTIFIER_SITES``,
keyed by the exact (path, expression) with a justification and a staleness
test -- never by name, so a novel name or site cannot inherit an exemption.

The allowlist is a ratchet: a new unguarded identifier interpolation fails the
suite, and a stale entry fails too.
"""

import ast
import os
import textwrap
from pathlib import Path

import pytest

from src.core.database.builder.sql_safety import (
    sql_identifier,
    sql_identifier_list,
)
from src.core.security.hardening import HardeningViolation, require_identifier

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

EXEC_METHODS = frozenset({"execute", "executemany", "executescript", "exec_driver_sql"})
GUARD_CALLS = frozenset(
    {"require_identifier", "is_identifier", "sql_identifier", "safe_identifier"}
)
# Fragments assembled from fixed column names plus ``?`` placeholders (or a
# placeholder list / bounded integer) are not object-name interpolation.
NON_IDENTIFIER = frozenset(
    {
        "placeholders",
        "set_clause",
        "sets",
        "updates",
        "assignments",
        "columns_str",
        "cols_str",
        "column_list",
        "values_clause",
    }
)
# Interpolations whose safety is established by a structural check at the site
# rather than by the binding of the interpolated name. Keyed by exact
# (relative path, expression) so a novel name or a new site never inherits an
# exemption; each entry carries a justification and is checked for staleness.
NON_IDENTIFIER_SITES: dict[tuple[str, str], str] = {
    (
        "src/api/routes/admin/users/admin_users.py",
        "guard_sql",
    ): "fixed hierarchy-guard SQL fragment from _hierarchy_write_guard; values bound",
    (
        "src/core/database/engines/sqlite.py",
        "temp_store",
    ): "membership-checked against the literal (MEMORY, FILE, OFF) catalog",
    (
        "src/core/database/core/maintenance.py",
        "self.transaction_depth",
    ): "internal bounded integer counter used in a SAVEPOINT name, never request-derived",
    (
        "src/core/database/core/transactions.py",
        "self.transaction_depth",
    ): "internal bounded integer counter used in a SAVEPOINT name, never request-derived",
}

# Every site whose value comes from a fixed catalogue defined in the code base
# (not from a request) but does not call the guard inline. Keyed by
# (relative path, interpolated expression).
FIXED_DDL_IDENTIFIER_SITES: dict[tuple[str, str], str] = {
    (
        "src/core/automod/schema.py",
        "col",
    ): "column from internal catalogue, regex-checked",
    (
        "src/core/automod/schema.py",
        "table",
    ): "table from internal allowlist, existence-checked",
    (
        "src/core/messaging/schema.py",
        "definition",
    ): "column type from the internal runtime_columns catalogue",
    (
        "src/core/migrations/migrations/000_initial_schema.py",
        "cascade",
    ): "literal '' or ' CASCADE' chosen by engine",
    (
        "src/core/migrations/migrations/000_initial_schema.py",
        "table",
    ): "table name from DB introspection during rollback",
    (
        "src/core/migrations/migrations/004_id_identity_defaults.py",
        "table",
    ): "table from DB introspection",
    (
        "src/core/migrations/migrations/006_report_queue_indexes.py",
        "columns",
    ): "column list from the literal INDEXES catalogue",
    (
        "src/core/migrations/migrations/006_report_queue_indexes.py",
        "name",
    ): "index name from the literal INDEXES catalogue",
    (
        "src/core/migrations/migrations/007_hot_lookup_composite_indexes.py",
        "columns",
    ): "column list from the literal INDEXES catalogue",
    (
        "src/core/migrations/migrations/007_hot_lookup_composite_indexes.py",
        "name",
    ): "index name from the literal INDEXES catalogue",
    (
        "src/core/migrations/cloner.py",
        "table_name",
    ): "parameter re-bound through sql_identifier() before every interpolation",
    (
        "src/core/erasure.py",
        "column",
    ): "for-loop target drawn from a literal column catalogue",
}


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Attribute):
                names.add(child.func.attr)
            elif isinstance(child.func, ast.Name):
                names.add(child.func.id)
    return names


def _literal_collection(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(
            _literal_collection(element)
            or (
                isinstance(element, (ast.Tuple, ast.List))
                and all(_literal_collection(inner) for inner in element.elts)
            )
            for element in node.elts
        )
    return False


def _binding_kind(name: str, scopes: list[ast.AST]) -> str | None:
    """Resolve a Name to its canonical binding kind across the whole module.

    Unlike a first-match lookup, *every* binding of ``name`` must be a guard
    call or a literal catalogue for the name to be considered safe. A single
    request-derived / unresolvable binding makes the result ``None`` so a
    shadowed, unguarded use fails closed instead of inheriting a safe sibling's
    classification. This is the canonical-name resolution that replaces the
    old fixed keyword regex over variable names.
    """
    origins: set[str] = set()
    for scope in scopes:
        for node in ast.walk(scope):
            targets: list[ast.expr] = []
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets, value = [node.target], node.value
            elif isinstance(node, ast.For):
                targets, value = [node.target], node.iter
            for target in targets:
                bound = {
                    child.id
                    for child in ast.walk(target)
                    if isinstance(child, ast.Name)
                }
                if name not in bound or value is None:
                    continue
                if _called_names(value) & GUARD_CALLS:
                    origins.add("guard")
                elif _literal_collection(value):
                    origins.add("fixed")
                else:
                    origins.add("unresolved")
    if origins and origins <= {"guard"}:
        return "guard"
    if origins and origins <= {"fixed"}:
        return "fixed"
    return None


def _scan_module(
    tree: ast.Module,
    relative: str,
    stats: dict[str, int],
    violations: set[tuple[str, str]],
) -> None:
    """Classify every f-string SQL interpolation in one module."""
    scopes = [tree] + [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for call in ast.walk(tree):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr in EXEC_METHODS
            and call.args
            and isinstance(call.args[0], ast.JoinedStr)
        ):
            continue
        for piece in call.args[0].values:
            if not isinstance(piece, ast.FormattedValue):
                continue
            expr = ast.unparse(piece.value)
            if _called_names(piece.value) & GUARD_CALLS:
                stats["guarded"] += 1
                continue
            if "bounded_int" in expr or "parse_id" in expr:
                stats["excluded"] += 1
                continue
            if expr.strip("'\"") in NON_IDENTIFIER or "join(" in expr:
                stats["excluded"] += 1
                continue
            # Exact, justified site exemptions for values whose binding cannot
            # prove safety on its own. Keyed by (path, expr), never by name.
            if (relative, expr) in NON_IDENTIFIER_SITES:
                stats["excluded"] += 1
                continue
            if isinstance(piece.value, ast.Name):
                kind = _binding_kind(piece.value.id, scopes)
                if kind == "fixed":
                    stats["fixed"] += 1
                    continue
                if kind == "guard":
                    stats["guarded"] += 1
                    continue
            # Unresolved name, non-Name expression, or a name whose bindings
            # mix safe and request-derived origins: fail closed.
            violations.add((relative, expr))


def _scan_source(source: str, relative: str):
    """Classify one synthetic source string with the real detector."""
    stats = {"guarded": 0, "fixed": 0, "excluded": 0}
    violations: set[tuple[str, str]] = set()
    _scan_module(ast.parse(source), relative, stats, violations)
    return stats, violations


def _scan():
    stats = {"guarded": 0, "fixed": 0, "excluded": 0}
    violations: set[tuple[str, str]] = set()
    for directory, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        if os.sep + "tests" in directory:
            continue
        for filename in sorted(files):
            if not filename.endswith(".py"):
                continue
            path = Path(directory) / filename
            relative = str(path.relative_to(ROOT))
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            _scan_module(tree, relative, stats, violations)
    return stats, violations


_STATS, _VIOLATIONS = _scan()


class TestSqlIdentifierAstAudit:
    def test_ast_scan_is_not_vacuous(self):
        assert _STATS["guarded"] >= 10, _STATS
        assert _STATS["fixed"] >= 10, _STATS
        assert _STATS["excluded"] >= 20, _STATS

    def test_detector_flags_novel_identifier_name_bound_from_request(self):
        """A bare identifier whose name matches no keyword list is still caught.

        The old detector only inspected interpolations whose variable name
        contained ``table``/``column``/``name``/...; an unguarded identifier
        named ``tbl`` and bound straight from a request was silently excluded.
        Canonical binding resolution closes that hole: ``tbl`` has a single
        request-derived binding, so it is a violation.
        """
        source = textwrap.dedent(
            """
            def handler(request):
                tbl = request.query_params["table"]
                db.execute(f"SELECT * FROM {tbl}")
                return tbl
            """
        )
        _, violations = _scan_source(source, "src/api/routes/novel_ident.py")
        assert ("src/api/routes/novel_ident.py", "tbl") in violations

    def test_shadowed_request_binding_is_not_masked_by_safe_sibling(self):
        """A request-derived binding fails closed even if a safe name exists.

        A first-match resolver saw the module-level literal ``table = "users"``
        (a name the old regex did inspect) and classified the interpolated inner
        request-derived ``table`` as fixed. All-bindings resolution sees both
        bindings and flags the site.
        """
        source = textwrap.dedent(
            """
            table = "users"

            def handler(request):
                table = request.query_params["table"]
                db.execute(f"SELECT * FROM {table}")
            """
        )
        _, violations = _scan_source(source, "src/api/routes/shadow_ident.py")
        assert ("src/api/routes/shadow_ident.py", "table") in violations

    def test_non_identifier_site_exemptions_are_real_and_stale_free(self):
        """Exact-site exemptions must still exist; a novel site is not exempt."""
        for relative, expr in NON_IDENTIFIER_SITES:
            source = (ROOT / relative).read_text(encoding="utf-8")
            tree = ast.parse(source)
            assert any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in EXEC_METHODS
                and node.args
                and isinstance(node.args[0], ast.JoinedStr)
                and any(
                    isinstance(piece, ast.FormattedValue)
                    and ast.unparse(piece.value) == expr
                    for piece in node.args[0].values
                )
                for node in ast.walk(tree)
            ), f"stale non-identifier exemption: {relative}:{expr}"

        # The same request-derived expression at a brand-new path is flagged,
        # proving the exemption is keyed to the exact site, not the name.
        source = textwrap.dedent(
            """
            def handler(request):
                guard_sql = request.query_params["sql"]
                db.execute(f"SELECT id FROM t WHERE {guard_sql}")
            """
        )
        _, violations = _scan_source(source, "src/api/routes/brand_new_site.py")
        assert ("src/api/routes/brand_new_site.py", "guard_sql") in violations

    def test_every_identifier_interpolation_is_guarded_or_documented(self):
        undocumented = sorted(_VIOLATIONS - set(FIXED_DDL_IDENTIFIER_SITES))
        assert not undocumented, (
            "Unguarded SQL identifier interpolations:\n  "
            + "\n  ".join(f"{path}: {expr}" for path, expr in undocumented)
        )

    def test_fixed_catalogue_allowlist_has_no_stale_entries(self):
        stale = sorted(set(FIXED_DDL_IDENTIFIER_SITES) - _VIOLATIONS)
        assert not stale, f"stale SQL identifier allowlist entries: {stale}"

    def test_known_guarded_call_sites_still_use_the_guard(self):
        expectations = {
            "src/core/migrations/cloner.py": "sql_identifier",
            "src/core/messaging/schema.py": "require_identifier",
            "src/core/push/manager/base.py": "require_identifier",
            "src/core/applications/schema.py": "require_identifier",
        }
        for relative, marker in expectations.items():
            source = (ROOT / relative).read_text(encoding="utf-8")
            assert marker in source, f"{relative} stopped using {marker}"


class TestIdentifierGuardRuntime:
    @pytest.mark.parametrize(
        "value",
        [
            "users; DROP TABLE users",
            "users--",
            "users/*",
            "a b",
            "a.b; DROP",
            "",
            "1users",
            None,
            7,
            "a" * 70,
        ],
    )
    def test_require_identifier_refuses_injected_names(self, value):
        with pytest.raises(HardeningViolation):
            require_identifier(value, "table name")

    @pytest.mark.parametrize("value", ["users", "msg_messages", "_tmp", "a1"])
    def test_require_identifier_accepts_plain_names(self, value):
        assert require_identifier(value) == value

    def test_sql_identifier_rejects_dotted_injection(self):
        with pytest.raises(HardeningViolation):
            sql_identifier("public.users; DROP TABLE x", "table name")

    def test_sql_identifier_allows_qualified_names(self):
        assert sql_identifier("public.users") == "public.users"

    def test_sql_identifier_quotes_only_the_validated_name(self):
        assert sql_identifier("users", quote='"') == '"users"'
        assert sql_identifier("users", quote="`") == "`users`"
        with pytest.raises(HardeningViolation):
            sql_identifier("users", quote="'; DROP--")

    def test_sql_identifier_list_validates_each_name(self):
        assert sql_identifier_list(["a", "b.c"]) == "a, b.c"
        with pytest.raises(HardeningViolation):
            sql_identifier_list(["a", "b; DROP"])
