"""Server search must apply the same query bounds as message/user search.

The core ``validate_search_query`` guard (length + term count + non-empty)
was only wired into the message and user search entry points. Server search
was the outlier: an unbounded query could reach ``websearch_to_tsquery`` /
``ILIKE`` and an empty query had no defined error contract. These regressions
pin that every server-search entry point validates before touching the index.
"""

from __future__ import annotations

import pytest

from src.core.search.exceptions import InvalidQuerySyntaxError
from src.core.search.manager.server_search import ServerSearchMixin


class _Stub(ServerSearchMixin):
    """Minimal manager with a recording indexer; no DB/backend needed."""

    def __init__(self):
        self._config = {"result_limit": 100}
        self.rate_limit_checked = False
        self.calls = []

    def _check_rate_limit(self, user_id):
        self.rate_limit_checked = True

    def _enrich_server_results(self, results):
        return results

    @property
    def _ranking_engine(self):
        class _Ranking:
            def rank_server_results(self, results, query):
                return results

        return _Ranking()

    def _indexer_for_test(self):
        class _Idx:
            def __init__(self, calls):
                self.calls = calls

            def search_servers(self, **kwargs):
                self.calls.append(kwargs)
                return []

            def search_servers_page(self, **kwargs):
                self.calls.append(kwargs)
                return [], None

        return _Idx(self.calls)


@pytest.fixture
def stub():
    s = _Stub()
    s._indexer = s._indexer_for_test()
    return s


@pytest.mark.parametrize("bad_query", ["", "   \t\n", "word " * 20, "x" * 257])
def test_search_servers_rejects_bad_query(stub, bad_query):
    with pytest.raises(InvalidQuerySyntaxError):
        stub.search_servers(user_id=1, query=bad_query)
    # Validation happens before the rate limiter / backend is touched.
    assert stub.rate_limit_checked is False
    assert stub.calls == []


@pytest.mark.parametrize("bad_query", ["", "   \t\n", "word " * 20, "x" * 257])
def test_search_servers_page_rejects_bad_query(stub, bad_query):
    with pytest.raises(InvalidQuerySyntaxError):
        stub.search_servers_page(user_id=1, query=bad_query)
    assert stub.rate_limit_checked is False
    assert stub.calls == []


def test_search_servers_accepts_valid_query(stub):
    results = stub.search_servers(user_id=1, query="hello world")
    assert results == []
    assert stub.rate_limit_checked is True
    assert len(stub.calls) == 1
    assert stub.calls[0]["query"] == "hello world"
