"""PERF700 invariant: deep-page readers seek by key, never by OFFSET.

An ``OFFSET n`` makes the database walk and discard ``n`` rows on every page, so
the message list and the admin audit export got slower the deeper a client
paged. The perf fix replaced those scans with indexed range predicates (``id <
?`` / ``(created_at, id)`` seek) plus a limit+1 lookahead.

This is a *static* invariant: it parses the real functions with :mod:`ast` and
inspects only their SQL string literals (function docstrings are excluded, so a
comment such as "unlike an OFFSET ..." cannot trip it). Every detector is first
validated against a synthetic violation before the real tree is trusted. No app
import, config, DB or network is needed; marked ``unit`` so the default pytest
selection keeps it.
"""

import ast
import re
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[2]

# (source path relative to repo root, keyset page function name)
KEYSET_FUNCTIONS = {
    "src/core/messaging/repositories/message.py": "get_by_conversation_page",
    "src/api/routes/admin/audit.py": "_iter_audit_export_rows",
    "src/core/suite/stash/stash_manager_items/listing.py": "_query_item_page_keyset",
}

_SEEK = re.compile(r"id\s*<\s*\?|id\s*>\s*\?")


def _parse(source):
    return ast.parse(textwrap.dedent(source))


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _sql_strings(fn):
    """String literals in ``fn`` with every docstring node excluded by identity."""
    doc_ids = set()
    scopes = [fn] + [
        n
        for n in ast.walk(fn)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    for scope in scopes:
        if (
            scope.body
            and isinstance(scope.body[0], ast.Expr)
            and isinstance(scope.body[0].value, ast.Constant)
            and isinstance(scope.body[0].value.value, str)
        ):
            doc_ids.add(id(scope.body[0].value))
    return [
        node.value
        for node in ast.walk(fn)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in doc_ids
    ]


def _check_keyset_source(source, func_name):
    """Problems with the seek shape of ``func_name`` in ``source``."""
    fn = _find_function(_parse(source), func_name)
    if fn is None:
        return [f"{func_name}: function not found"]
    strings = _sql_strings(fn)
    problems = []
    if any("OFFSET" in s.upper() for s in strings):
        problems.append(f"{func_name}: OFFSET found in SQL")
    if not any(_SEEK.search(s) for s in strings):
        problems.append(f"{func_name}: no indexed id range/seek predicate")
    if not any("LIMIT" in s.upper() for s in strings):
        problems.append(f"{func_name}: no bounded LIMIT")
    return problems


def _real_problems():
    problems = []
    scanned = []
    for rel, name in sorted(KEYSET_FUNCTIONS.items()):
        path = ROOT / rel
        if not path.exists():
            problems.append(f"{rel}: missing file")
            continue
        problems.extend(
            f"{rel}: {p}"
            for p in _check_keyset_source(path.read_text(encoding="utf-8"), name)
        )
        scanned.append(rel)
    return problems, scanned


_REAL_PROBLEMS, _SCANNED = _real_problems()


def test_self_test_detectors_catch_synthetic_violations():
    """The detector rejects OFFSET, a missing seek, and a missing LIMIT."""
    good = textwrap.dedent(
        """
        def page(user_id, cursor=None):
            \"\"\"Seek by id; this docstring mentions OFFSET but is not SQL.\"\"\"
            query = "SELECT * FROM t WHERE id < ? ORDER BY id DESC LIMIT ?"
            return query
        """
    )
    with_offset = textwrap.dedent(
        """
        def page(user_id, cursor=None):
            query = "SELECT * FROM t ORDER BY id DESC LIMIT ? OFFSET ?"
            return query
        """
    )
    no_seek = textwrap.dedent(
        """
        def page(user_id, cursor=None):
            query = "SELECT * FROM t ORDER BY id DESC LIMIT ?"
            return query
        """
    )
    no_limit = textwrap.dedent(
        """
        def page(user_id, cursor=None):
            query = "SELECT * FROM t WHERE id < ? ORDER BY id DESC"
            return query
        """
    )

    assert _check_keyset_source(good, "page") == []
    assert any("OFFSET" in p for p in _check_keyset_source(with_offset, "page"))
    assert any("seek" in p for p in _check_keyset_source(no_seek, "page"))
    assert any("LIMIT" in p for p in _check_keyset_source(no_limit, "page"))
    assert any("not found" in p for p in _check_keyset_source("x = 1\n", "page"))


def test_scan_is_not_vacuous():
    assert len(KEYSET_FUNCTIONS) >= 3
    assert len(_SCANNED) == len(KEYSET_FUNCTIONS)


def test_keyset_page_functions_never_use_offset():
    assert not _REAL_PROBLEMS, (
        "keyset pagination regressed to OFFSET scans:\n  "
        + "\n  ".join(sorted(_REAL_PROBLEMS))
    )
