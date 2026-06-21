"""
Shared fixtures for poll tests.

These wire two real users (self-DM is forbidden by the messaging
service) and a poll attached to a DM message so tests can call
``polls.create_poll`` and ``polls.vote`` without further setup.
"""

import pytest


@pytest.fixture
def dm_with_message(modules, user_pool):
    """Create a DM with a message for poll tests.

    Returns
    -------
    (user1, user2, dm, msg, polls, messaging)
    """
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()

    # Messaging refuses self-DMs at runtime; pair two distinct users.
    if user1.id == user2.id:
        user2 = user_pool.get_user()
    dm = modules.messaging.create_dm(user1.id, user2.id)
    msg = modules.messaging.send_message(user1.id, dm.id, "Poll message")

    return user1, user2, dm, msg, modules.polls, modules.messaging


@pytest.fixture
def poll_with_options(dm_with_message):
    """Create a poll with options.

    Returns
    -------
    poll : Poll
        The created poll object (single return -- callers can fetch
        users/msg via ``dm_with_message`` if needed).
    """
    user1, _user2, _dm, msg, polls, _messaging = dm_with_message

    poll = polls.create_poll(
        user_id=user1.id,
        message_id=msg.id,
        question="What is your favorite color?",
        options=["Red", "Blue", "Green", "Yellow"],
    )

    return poll


@pytest.fixture
def user_with_dm(auth_manager, messaging_manager, monkeypatch):
    """Single test user plus a DM-and-message ready for poll calls.

    Plexichat's ``create_dm`` refuses self-DMs (``InvalidRecipientError``);
    every poll test that previously did ``create_dm(user.id, user.id)``
    fails.  This fixture registers a primary user plus a synthetic
    companion and creates the DM between them so the test body can
    just use ``user`` / ``dm`` / ``msg`` directly.
    """
    import uuid as _uuid

    from src.utils import encryption

    uid = _uuid.uuid4().hex[:8]
    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        encryption, "hash_password", return_value="fake_hash_$test"
    ):
        user = auth_manager.register(
            username=f"poll_user_{uid}",
            email=f"poll_{uid}@test.local",
            password="TestPass123!",
        )
        friend = auth_manager.register(
            username=f"poll_friend_{uid}",
            email=f"poll_friend_{uid}@test.local",
            password="TestPass123!",
        )

    dm = messaging_manager.create_dm(user.id, friend.id)
    msg = messaging_manager.send_message(user.id, dm.id, "test")
    return user, friend, dm, msg
