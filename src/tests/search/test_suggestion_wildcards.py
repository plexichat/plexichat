"""Search suggestions must treat LIKE metacharacters literally.

Regression for the search-history suggestion query, which interpolated the
caller's prefix straight into ``LIKE ?`` without escaping. A bare ``%`` (or
``_``) therefore acted as a wildcard and returned unrelated history rows.

docs/admin-hardening.md: "Treat ``%``, ``_``, and ``\\`` literally in search
queries. Do not interpret them as wildcards."
"""

import pytest

pytestmark = pytest.mark.search


def _insert_history(db, user_id, queries):
    """Insert search-history rows, allocating fresh primary keys."""
    row = db.fetch_one("SELECT COALESCE(MAX(id), 0) AS max_id FROM search_history")
    next_id = int(row["max_id"] or 0) + 1
    for i, query in enumerate(queries):
        db.execute(
            """INSERT INTO search_history
               (id, user_id, query, search_type, result_count, searched_at)
               VALUES (?, ?, ?, 'message', 1, ?)""",
            (next_id + i, user_id, query, 1000 + i),
        )


class TestSuggestionWildcardEscaping:
    def test_percent_prefix_is_literal(self, users_with_dm):
        user1, _user2, _dm, _messages, search = users_with_dm
        _insert_history(search._db, user1.id, ["alpha one", "% literal prefix"])

        suggestions = search.get_search_suggestions(user1.id, "%", limit=10)

        assert "% literal prefix" in suggestions
        # Without escaping, the bare "%" matched every history row.
        assert "alpha one" not in suggestions

    def test_underscore_prefix_is_literal(self, users_with_dm):
        user1, _user2, _dm, _messages, search = users_with_dm
        _insert_history(search._db, user1.id, ["a_b item", "axb item"])

        suggestions = search.get_search_suggestions(user1.id, "a_", limit=10)

        assert "a_b item" in suggestions
        # "_" must not match the arbitrary "x" character.
        assert "axb item" not in suggestions

    def test_backslash_prefix_is_literal(self, users_with_dm):
        user1, _user2, _dm, _messages, search = users_with_dm
        _insert_history(search._db, user1.id, [r"\n literal", "n literal"])

        suggestions = search.get_search_suggestions(user1.id, "\\", limit=10)

        assert r"\n literal" in suggestions
        assert "n literal" not in suggestions
