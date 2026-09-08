"""
Regression tests for finding M5 (artifacts): the free-text search filter
used to build ``LIKE '%{q}%'`` without escaping ``%`` / ``_`` and without
declaring ``ESCAPE``, so a bare ``%`` matched EVERY row (filter evasion /
listing DoS). ``_build_where`` now escapes backslash/percent/underscore and
declares ``ESCAPE '\\'``; these tests pin that behaviour.
"""

from src.core.artifacts.repository import count_artifacts, list_artifacts


def _seed(db, artifact_builder):
    return [
        artifact_builder(db, author_id=1, title="Quarterly Report"),
        artifact_builder(db, author_id=2, title="100% Growth"),
        artifact_builder(db, author_id=3, title="Q1_Final"),
    ]


def test_percent_search_matches_literal_percent_only(db, artifact_builder):
    _seed(db, artifact_builder)

    # A bare "%" must NOT match every row (the pre-fix filter evasion).
    matches = list_artifacts(db, {"search": "%"})
    titles = [a.title for a in matches]
    assert titles == ["100% Growth"]

    assert count_artifacts(db, {"search": "%"}) == 1


def test_underscore_search_matches_literal_underscore_only(db, artifact_builder):
    _seed(db, artifact_builder)

    # "_" is a LIKE single-char wildcard; escaped it matches only the row
    # that literally contains an underscore.
    matches = list_artifacts(db, {"search": "_"})
    assert [a.title for a in matches] == ["Q1_Final"]


def test_wildcard_laden_term_does_not_widen(db, artifact_builder):
    _seed(db, artifact_builder)

    # "100%" must match only the percent row, never the others.
    matches = list_artifacts(db, {"search": "100%"})
    assert [a.title for a in matches] == ["100% Growth"]


def test_backslash_input_is_harmless(db, artifact_builder):
    _seed(db, artifact_builder)

    # A lone backslash is escaped (not treated as an escape char), so the
    # query is safe and returns no spurious full-table matches.
    matches = list_artifacts(db, {"search": "\\"})
    assert matches == []


def test_literal_search_still_finds_plain_terms(db, artifact_builder):
    _seed(db, artifact_builder)

    matches = list_artifacts(db, {"search": "quarterly"})
    assert [a.title for a in matches] == ["Quarterly Report"]
