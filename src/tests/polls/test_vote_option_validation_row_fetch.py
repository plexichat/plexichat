"""Voting option validation must not materialize a row per selected option.

``vote`` / ``change_vote`` verify that every selected option belongs to the
poll.  The check only needs a *count*, so it must issue a COUNT aggregate and
fetch a single row rather than fetching (and allocating) every matching option
row.  Voting is a hot path; the old code fetched one full row per selected
option just to compare ``len(rows)`` against the selection size.
"""

import pytest


@pytest.mark.polls
def _option_validation_fetch_counts(poll_manager, user_with_dm, method):
    """Return rows fetched by the option-validation query during one call.

    Returns
    -------
    (rows_fetched, results)
        ``rows_fetched`` is the total number of rows materialized by calls to
        ``fetch_all`` whose SQL validates selected options.  ``results`` is the
        manager call's return value.
    """
    user, friend, _dm, msg = user_with_dm

    # Ten options is the hard envelope ceiling for a poll/vote selection, so
    # this is the largest (hottest) legitimate selection the validator sees.
    options = [f"Option {i}" for i in range(10)]
    poll = poll_manager.create_poll(
        user.id, msg.id, "Row fetch probe?", options, allow_multiple_choice=True
    )
    option_ids = [opt.id for opt in poll.options]

    real_fetch_all = poll_manager._db.fetch_all
    real_fetch_one = poll_manager._db.fetch_one
    fetched = {"validation_rows": 0, "validation_calls": 0}

    def is_validation_query(query):
        q = " ".join(str(query).split()).lower()
        # Match only the selected-option validation query. Poll hydration reads
        # poll_options with a different ORDER BY / predicate shape (no IN).
        return "from poll_options" in q and " id in (" in q

    def counting_fetch_all(query, params=()):
        rows = real_fetch_all(query, params)
        if is_validation_query(query):
            fetched["validation_calls"] += 1
            fetched["validation_rows"] += len(rows)
        return rows

    def counting_fetch_one(query, params=()):
        row = real_fetch_one(query, params)
        if is_validation_query(query):
            fetched["validation_calls"] += 1
            fetched["validation_rows"] += 1 if row else 0
        return row

    poll_manager._db.fetch_all = counting_fetch_all
    poll_manager._db.fetch_one = counting_fetch_one
    try:
        results = method(friend.id, poll.id, option_ids)
    finally:
        poll_manager._db.fetch_all = real_fetch_all
        poll_manager._db.fetch_one = real_fetch_one

    assert fetched["validation_calls"] >= 1, "validation query was not exercised"
    return fetched["validation_rows"], results


@pytest.mark.polls
def test_vote_option_validation_does_not_fetch_every_row(poll_manager, user_with_dm):
    rows_fetched, results = _option_validation_fetch_counts(
        poll_manager, user_with_dm, poll_manager.vote
    )
    # A COUNT aggregate returns exactly one row regardless of selection size.
    # Fetching one row per selected option (10) is the regression.
    assert rows_fetched <= 1, (
        f"option validation materialized {rows_fetched} rows; "
        "a COUNT query should fetch only 1"
    )
    assert len(results.user_votes) == 10


@pytest.mark.polls
def test_change_vote_option_validation_does_not_fetch_every_row(
    poll_manager, user_with_dm
):
    rows_fetched, results = _option_validation_fetch_counts(
        poll_manager, user_with_dm, poll_manager.change_vote
    )
    assert rows_fetched <= 1, (
        f"option validation materialized {rows_fetched} rows; "
        "a COUNT query should fetch only 1"
    )
    assert len(results.user_votes) == 10
