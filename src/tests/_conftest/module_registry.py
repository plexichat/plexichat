"""ModuleRegistry wiring fixture and friends."""

import pytest
import uuid

from src.utils import encryption

# =============================================================================
# Backward Compatibility Fixtures
# =============================================================================


class ModuleRegistry:
    """

    import uuid
        Backward compatibility wrapper for module fixtures.

        Provides attribute access to individual manager fixtures.
    """

    def __init__(
        self,
        db,
        auth_manager,
        messaging_manager,
        server_manager,
        presence_manager,
        rel_manager,
        reaction_manager,
        webhook_manager,
        thread_manager,
        notification_manager,
        media_manager,
        search_manager,
        app_manager,
        sticker_manager,
        poll_manager,
        soundboard_manager,
        embeds_manager,
        reports,
        feedback,
    ):
        self._db = db
        self.auth = auth_manager
        self.messaging = messaging_manager
        self.servers = server_manager
        self.presence = presence_manager
        self.relationships = rel_manager
        self.reactions = reaction_manager
        self.webhooks = webhook_manager
        self.threads = thread_manager
        self.notifications = notification_manager
        self.media = media_manager
        self.search = search_manager
        self.applications = app_manager
        self.stickers = sticker_manager
        self.polls = poll_manager
        self.soundboard = soundboard_manager
        self.embeds = embeds_manager
        self.reports = reports
        self.feedback = feedback


@pytest.fixture
def modules(
    db,
    auth_manager,
    messaging_manager,
    server_manager,
    presence_manager,
    rel_manager,
    reaction_manager,
    webhook_manager,
    thread_manager,
    notification_manager,
    media_manager,
    search_manager,
    app_manager,
    sticker_manager,
    poll_manager,
    soundboard_manager,
    embeds_manager,
    reports,
    feedback,
):
    """
    Backward compatibility fixture that wraps all module managers.

    Returns a ModuleRegistry instance that provides attribute access to all managers.
    Example: modules.auth, modules.messaging, etc.
    """
    return ModuleRegistry(
        db=db,
        auth_manager=auth_manager,
        messaging_manager=messaging_manager,
        server_manager=server_manager,
        presence_manager=presence_manager,
        rel_manager=rel_manager,
        reaction_manager=reaction_manager,
        webhook_manager=webhook_manager,
        thread_manager=thread_manager,
        notification_manager=notification_manager,
        media_manager=media_manager,
        search_manager=search_manager,
        app_manager=app_manager,
        sticker_manager=sticker_manager,
        poll_manager=poll_manager,
        soundboard_manager=soundboard_manager,
        embeds_manager=embeds_manager,
        reports=reports,
        feedback=feedback,
    )


class UserPool:
    """
    Backward compatibility wrapper for user management.

    Provides similar interface to the old user_pool fixture.
    """

    def __init__(self, auth_manager):
        self.auth_manager = auth_manager
        self._users = {}

    def create_user(self, username=None, email=None, password="TestPassword123!"):
        """Create a new user and store in pool."""
        from unittest.mock import patch

        if username is None:
            username = f"testuser_{uuid.uuid4().hex[:8]}"
        if email is None:
            email = f"test_{uuid.uuid4().hex[:8]}@example.com"

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = self.auth_manager.register(
                username=username,
                email=email,
                password=password,
            )
        self._users[user.id] = user
        return user

    def get_user(self, user_id=None):
        """Get a user from the pool by ID, or auto-create one.

        When ``user_id`` is ``None`` (the common polls/soundboard/
        settings test pattern) we create a fresh user on the fly and
        store it in the pool so subsequent ``get_user(id)`` lookups
        for the same id succeed.  This keeps both the historical
        "lookup by id" callers AND the modern "fresh helper" callers
        working through a single fixture.
        """
        if user_id is None:
            return self.create_user()
        return self._users.get(user_id)

    def get_all_users(self):
        """Get all users in the pool."""
        return list(self._users.values())


@pytest.fixture
def user_pool(auth_manager):
    """
    Backward compatibility fixture for user management.

    Returns a UserPool instance that provides methods for creating and managing test users.
    """
    return UserPool(auth_manager)


@pytest.fixture
def db_and_modules(db, auth_manager, messaging_manager, server_manager, embeds_manager):
    """
    Backward compatibility fixture that returns db and module managers as a tuple.

    Returns: (db, auth_manager, messaging_manager, server_manager, embeds_manager)
    """
    return db, auth_manager, messaging_manager, server_manager, embeds_manager


@pytest.fixture
def db_and_search(db, auth_manager, messaging_manager, server_manager, search_manager):
    """
    Fixture that returns db and search-related module managers as a tuple.

    Returns: (db, auth_manager, messaging_manager, server_manager, search_manager)
    """
    return db, auth_manager, messaging_manager, server_manager, search_manager


@pytest.fixture
def db_and_auth(db, auth_manager):
    """
    Backward compatibility fixture that returns both db and auth manager.

    Returns a tuple: (db, auth_manager)
    """
    return db, auth_manager
