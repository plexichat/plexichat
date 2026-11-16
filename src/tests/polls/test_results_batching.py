"""get_results batches its vote aggregation without changing semantics.

The single conditional-aggregation query must still report per-option totals,
the viewer's own selections, and the distinct voter headcount, while hidden
results stay hidden.
"""

from src.core.polls.models import PollResultsVisibility


def test_visible_results_keep_counts_votes_and_total(poll_manager, user_with_dm):
    user, friend, _dm, msg = user_with_dm
    poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
    a, b = poll.options[0].id, poll.options[1].id
    poll_manager.vote(user.id, poll.id, [a])
    poll_manager.vote(friend.id, poll.id, [b])

    results = poll_manager.get_results(poll.id, user.id)

    counts = {opt.id: opt.vote_count for opt in results.options}
    assert counts == {a: 1, b: 1}
    assert results.total_votes == 2
    assert results.user_voted is True
    assert results.user_votes == [a]


def test_multi_choice_reports_every_own_option(poll_manager, user_with_dm):
    user, friend, _dm, msg = user_with_dm
    poll = poll_manager.create_poll(
        user.id,
        msg.id,
        "Question?",
        ["A", "B", "C"],
        allow_multiple_choice=True,
    )
    a, b, c = (opt.id for opt in poll.options)
    poll_manager.vote(user.id, poll.id, [a, b])
    poll_manager.vote(friend.id, poll.id, [c])

    results = poll_manager.get_results(poll.id, user.id)

    assert set(results.user_votes) == {a, b}
    assert results.total_votes == 2
    assert {opt.id: opt.vote_count for opt in results.options} == {a: 1, b: 1, c: 1}


def test_hidden_results_stay_hidden(poll_manager, user_with_dm):
    user, friend, _dm, msg = user_with_dm
    poll = poll_manager.create_poll(
        user.id,
        msg.id,
        "Question?",
        ["A", "B"],
        results_visibility=PollResultsVisibility.AFTER_VOTE,
    )
    poll_manager.vote(user.id, poll.id, [poll.options[0].id])

    # 'friend' has not voted, so an AFTER_VOTE poll must reveal nothing.
    results = poll_manager.get_results(poll.id, friend.id)

    assert results.user_voted is False
    assert results.total_votes is None
    assert all(opt.vote_count is None for opt in results.options)
