"""Per-module manager fixtures (auth, messaging, servers, ...)."""

import pytest


# =============================================================================
# Module Fixtures (Function-Scoped)
# =============================================================================


@pytest.fixture
def auth_manager(db):
    """AuthManager fixture."""
    from src.core import auth

    # Set up the global auth module so API middleware can use it
    auth.setup(db)

    # Return the global auth module (which contains the AuthManager)
    return auth


@pytest.fixture
def messaging_manager(db):
    """MessagingManager fixture."""
    from src.core.messaging.manager import MessagingManager

    manager = MessagingManager(db)
    yield manager
    # Clean up any database connections held by messaging repositories
    # to prevent ResourceWarning about unclosed connections.
    for attr_name in dir(manager):
        try:
            attr = getattr(manager, attr_name)
            if hasattr(attr, "_db"):
                repo_db = getattr(attr, "_db", None)
                if repo_db is not None and repo_db is not db:
                    try:
                        repo_db.close()
                    except Exception:
                        pass
        except Exception:
            pass


@pytest.fixture
def server_manager(db, auth_manager, messaging_manager):
    """ServerManager fixture."""
    from src.core.servers.manager import ServerManager

    manager = ServerManager(
        db, auth_module=auth_manager, messaging_module=messaging_manager
    )
    # Disable encryption for tests to avoid keyring issues
    manager._encrypt_descriptions = False
    yield manager
    # Clean up any database connections held by server repositories
    # to prevent ResourceWarning about unclosed connections.
    for attr_name in dir(manager):
        try:
            attr = getattr(manager, attr_name)
            if hasattr(attr, "_db"):
                repo_db = getattr(attr, "_db", None)
                if repo_db is not None and repo_db is not db:
                    try:
                        repo_db.close()
                    except Exception:
                        pass
        except Exception:
            pass


@pytest.fixture
def rel_manager(db):
    """RelationshipManager fixture."""
    # Re-export picks up the manager transformed MRO after the
    # legacy manager.py was split into a sub-package (see
    # src/core/relationships/manager/__init__.py).
    from src.core.relationships.manager import RelationshipManager

    return RelationshipManager(db)


@pytest.fixture
def presence_manager(db, rel_manager):
    """PresenceManager fixture."""
    from src.core.presence.manager import PresenceManager

    return PresenceManager(db, relationships_module=rel_manager)


@pytest.fixture
def reaction_manager(db, rel_manager):
    """ReactionManager fixture."""
    from src.core.reactions.manager import ReactionManager

    return ReactionManager(db, relationships_module=rel_manager)


@pytest.fixture
def webhook_manager(db, server_manager, auth_manager, messaging_manager):
    """WebhookManager fixture with real permission and message dependencies."""
    from src.core.webhooks.manager import WebhookManager

    return WebhookManager(
        db,
        auth_module=auth_manager,
        # Keep this fixture's raw-channel tests focused on webhook behavior;
        # those tests intentionally do not create a messaging conversation.
        # Production setup wires the real messaging module in api/bootstrap.
        messaging_module=None,
        servers_module=server_manager,
    )


@pytest.fixture
def thread_manager(db):
    """ThreadManager fixture."""
    from src.core.threads.manager import ThreadManager

    return ThreadManager(db)


@pytest.fixture
def notification_manager(db):
    """NotificationManager fixture."""
    from src.core.notifications.manager import NotificationManager

    return NotificationManager(db)


@pytest.fixture
def automod_manager(db, server_manager, messaging_manager, notification_manager):
    """AutoModManager fixture."""
    from src.core.automod.manager import AutoModManager

    return AutoModManager(db, server_manager, messaging_manager, notification_manager)


@pytest.fixture
def media_manager(db, messaging_manager):
    """MediaManager fixture."""
    from src.core.media.manager import MediaManager

    manager = MediaManager(db, messaging_manager)
    yield manager
    # Wait for fire-and-forget background tasks before the db fixture
    # removes the temp SQLite file (see MediaManager.close docstring:
    # a thread touching a deleted mmap'd DB file crashes with SIGBUS).
    manager.close()


@pytest.fixture
def search_manager(db, auth_manager, messaging_manager, server_manager):
    """SearchManager fixture."""
    from src.core.search.manager import SearchManager

    return SearchManager(db, auth_manager, messaging_manager, server_manager)


@pytest.fixture
def app_manager(db):
    """ApplicationManager fixture."""
    from src.core.applications.manager import ApplicationManager

    return ApplicationManager(db)


@pytest.fixture
def sticker_manager(db, server_manager, messaging_manager):
    """StickerManager fixture.

    Encryption of sticker pack descriptions is disabled to avoid a
    keyring dependency in tests; descriptions round-trip as plaintext
    so ``assert pack.description == "..."`` works without a live
    encryption keyring service.

    Wires ``servers_module`` and ``messaging_module`` so the
    permission / message-existence checks execute for real; without
    these the manager short-circuits both gates to True via the
    ``_servers is None`` / ``_messaging is None`` bypass, which is
    why ``test_delete_pack_no_permission_fails`` and friends used to
    silently succeed.
    """
    from src.core.stickers.manager import StickerManager

    manager = StickerManager(
        db,
        messaging_module=messaging_manager,
        servers_module=server_manager,
    )
    manager._encrypt_descriptions = False
    return manager


@pytest.fixture
def poll_manager(db, messaging_manager):
    """PollManager fixture (wired with messaging: access checks fail closed)."""
    from src.core.polls.manager import PollManager

    return PollManager(db, messaging_module=messaging_manager)


@pytest.fixture
def settings_manager(db):
    """SettingsManager fixture (canonical name used by the test suite).

    The earlier fixture key ``settings_manager`` was undefined and the
    matching ``test_settings.py`` tests crashed on collection.  This
    fixture wires a fresh ``SettingsManager`` against the function-scoped
    database so each test runs in isolation, matching the conventions of
    the sibling module fixtures declared above.
    """
    from src.core.settings.manager import SettingsManager

    return SettingsManager(db)


@pytest.fixture
def soundboard_manager(db, server_manager, auth_manager):
    """SoundboardManager fixture.

    The manager checks ``_servers.has_permission(...)`` for negative-
    permission tests to actually raise ``PermissionDeniedError``.
    Passing ``server_manager`` ensures those checks fire; without
    ``servers_module`` wired the manager returns ``True`` on every
    check (``_check_server_permission`` short-circuits), turning the
    whole negative-permission suite into silent passes.
    """
    from src.core.soundboard.manager import SoundboardManager

    return SoundboardManager(
        db, auth_module=auth_manager, servers_module=server_manager
    )


@pytest.fixture
def embeds_manager(db, messaging_manager, server_manager):
    """EmbedsManager fixture."""
    from src.core import embeds

    embeds.setup(db, messaging_manager, server_manager)
    return embeds._manager


@pytest.fixture
def events_module(rel_manager, server_manager, messaging_manager):
    """Events module fixture."""
    from src.core import events

    events.setup(rel_manager, server_manager, messaging_manager)
    return events


@pytest.fixture
def reports(db):
    """Reports module fixture (setup already done in db fixture)."""
    from src.core import reports

    return reports


@pytest.fixture
def feedback(db):
    """Feedback module fixture (setup already done in db fixture)."""
    from src.core import feedback

    return feedback
