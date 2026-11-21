"""Structural invariant: every ``LIMIT ?`` reader in the dedup package clamps.

``clamp_page_limit`` (``src/core/media/deduplication/paging.py``) is the single
boundary that keeps a SQLite ``LIMIT ?`` from becoming an unbounded read: a
negative limit means "unlimited", a huge positive one materializes the whole
moderation table, and non-int input must fail closed. The behavioural tests
pin today's three call sites, but a peer adding a fourth listing method would
silently bypass them.

This is a *static* guard: it parses every module in the deduplication package
with :mod:`ast` and fails if any function that binds ``LIMIT ?`` does not call
``clamp_page_limit``. Function docstrings are excluded so a comment about
"LIMIT ?" cannot trip it, and the detector is validated against synthetic
clamped/unclamped/docstring-only snippets before the real tree is trusted, so
it cannot silently become an always-passing scan. No app import, config, DB or
network is needed.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[3]
DEDUP_DIR = ROOT / "src" / "core" / "media" / "deduplication"

# The listing methods that must exist for this guard to be meaningful. If the
# package is reorganised, fail loudly rather than let the scan pass vacuously.
KNOWN_LIMIT_READERS = {
    "get_reports",
    "get_blocked_users",
    "get_blocked_hashes",
}


def _parse(source):
    return ast.parse(textwrap.dedent(source))


def _sql_literals(fn):
    """String literals in ``fn`` with every docstring node excluded by identity."""
    doc_ids = set()
    scopes = [fn] + [
        node
        for node in ast.walk(fn)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
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


def _calls_clamp(fn):
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "clamp_page_limit":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "clamp_page_limit":
            return True
    return False


def _scan(source):
    """Return ``(unclamped_names, all_limit_reader_names)`` for a source text."""
    tree = _parse(source)
    unclamped = set()
    readers = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any("LIMIT ?" in sql for sql in _sql_literals(node)):
            continue
        readers.add(node.name)
        if not _calls_clamp(node):
            unclamped.add(node.name)
    return unclamped, readers


class TestDedupLimitGuardDetector:
    """Validate the detector itself so the real-tree assertion is non-vacuous."""

    def test_flags_unclamped_limit_reader(self):
        src = '''
            def get_things(db, limit=50):
                return db.fetch_all("SELECT x FROM t LIMIT ? OFFSET ?", (limit, 0))
        '''
        unclamped, readers = _scan(src)
        assert readers == {"get_things"}
        assert unclamped == {"get_things"}

    def test_accepts_clamped_limit_reader(self):
        src = '''
            def get_things(db, limit=50):
                limit = clamp_page_limit(limit)
                return db.fetch_all("SELECT x FROM t LIMIT ? OFFSET ?", (limit, 0))
        '''
        unclamped, readers = _scan(src)
        assert readers == {"get_things"}
        assert unclamped == set()

    def test_docstring_mention_is_not_a_reader(self):
        src = '''
            def helper():
                """Compose the LIMIT ? fragment for callers."""
                return None
        '''
        assert _scan(src) == (set(), set())


class TestDedupPackageLimitReadersAllClamped:
    def test_no_unclamped_limit_reader_in_package(self):
        modules = sorted(DEDUP_DIR.glob("*.py"))
        assert modules, f"no modules found under {DEDUP_DIR}; guard would be vacuous"

        all_unclamped = set()
        all_readers = set()
        for path in modules:
            unclamped, readers = _scan(path.read_text(encoding="utf-8"))
            all_unclamped.update(unclamped)
            all_readers.update(readers)

        # Keep the guard honest against the real tree: the known listing
        # methods must still be detected as LIMIT readers.
        assert KNOWN_LIMIT_READERS <= all_readers, (
            f"expected dedup LIMIT readers {sorted(KNOWN_LIMIT_READERS)}, "
            f"found {sorted(all_readers)}; guard is stale"
        )
        assert all_unclamped == set(), (
            "dedup LIMIT ? readers missing clamp_page_limit: "
            f"{sorted(all_unclamped)}"
        )
