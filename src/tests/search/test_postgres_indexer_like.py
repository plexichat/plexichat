"""PostgreSQL indexer must escape LIKE/ILIKE metacharacters in user queries.

Parameterization stops SQL injection but does not stop ``%``/``_`` from acting
as wildcards, so a bare ``%`` would otherwise match every indexed row.
docs/admin-hardening.md: "Treat %, _, and \\ literally in search queries."
"""

import pytest

from src.core.search.indexer.postgres import PostgresIndexer, _escape_like

pytestmark = [pytest.mark.unit, pytest.mark.search]


class _FakeDB:
    def __init__(self, websearch: bool = True):
        self.websearch = websearch
        self.selects = []

    def execute(self, query, params=None):
        return None

    def fetch_one(self, query, params=None):
        if "websearch_to_tsquery" in query and not self.websearch:
            raise RuntimeError("websearch_to_tsquery unsupported")
        return {"?": 1}

    def fetch_all(self, query, params=None):
        self.selects.append((query, params))
        return []


def _make_indexer(websearch: bool = True):
    db = _FakeDB(websearch=websearch)
    indexer = PostgresIndexer(db)
    indexer.initialize()
    return db, indexer


@pytest.mark.parametrize("raw", ["%", "_", "\\", "100%_off", "a\\b"])
def test_message_search_escapes_like_metacharacters(raw):
    db, indexer = _make_indexer()
    indexer.search_messages(raw)

    sql, params = db.selects[-1]
    assert "content ILIKE ? ESCAPE '\\'" in sql
    assert params[1] == f"%{_escape_like(raw)}%"
    assert params[1] != f"%{raw}%"


def test_message_search_fallback_branch_escapes():
    db, indexer = _make_indexer(websearch=False)
    indexer.search_messages("%")

    sql, params = db.selects[-1]
    assert "plainto_tsquery" in sql
    assert "content ILIKE ? ESCAPE '\\'" in sql
    assert params[2] == f"%{_escape_like('%')}%"


def test_user_search_escapes_like_metacharacters():
    db, indexer = _make_indexer()
    indexer.search_users("%")

    sql, params = db.selects[-1]
    assert sql.count("ILIKE ? ESCAPE '\\'") == 2
    assert params[1] == f"%{_escape_like('%')}%"
    assert params[2] == f"%{_escape_like('%')}%"


def test_server_search_escapes_like_metacharacters():
    db, indexer = _make_indexer()
    indexer.search_servers("_")

    sql, params = db.selects[-1]
    assert sql.count("ILIKE ? ESCAPE '\\'") == 2
    assert params[1] == f"%{_escape_like('_')}%"
    assert params[2] == f"%{_escape_like('_')}%"
