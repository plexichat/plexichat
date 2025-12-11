"""
Shared fixtures for poll tests.

Uses the session-scoped fixtures from root conftest for efficiency.
"""

import pytest


@pytest.fixture
def dm_with_message(modules, user_pool):
    """Create a DM with a message for poll tests."""
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()

    dm = modules.messaging.create_dm(user1.id, user2.id)
    msg = modules.messaging.send_message(user1.id, dm.id, "Poll message")

    return user1, user2, dm, msg, modules.polls, modules.messaging


@pytest.fixture
def poll_with_options(dm_with_message):
    """Create a poll with options."""
    user1, user2, dm, msg, polls, messaging = dm_with_message

    poll = polls.create_poll(
        user_id=user1.id,
        message_id=msg.id,
        question="What is your favorite color?",
        options=["Red", "Blue", "Green", "Yellow"]
    )

    return user1, user2, poll, polls, messaging
