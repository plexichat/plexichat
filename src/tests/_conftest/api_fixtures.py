"""FastAPI TestClient fixtures and auth helpers."""

import pytest


# =============================================================================
# Shared production-equivalent module-graph wiring
# =============================================================================


def _wire_module_graph(
    db,
    auth_manager,
    messaging_manager,
    server_manager,
    rel_manager,
    presence_manager,
    reaction_manager,
    reports,
    feedback,
):
    """Set up every core module the production initializer wires.

    Mirrors ``src/server/initializer.py`` + ``src/bootstrap/app.py`` so
    API-level tests validate the REAL configuration instead of a degraded
    half-``None`` module graph (routes that guard on a ``None`` module
    fail open; with the real wiring they exercise their actual behavior).
    """
    from src.core import (
        ratelimit,
        embeds,
        webhooks,
        notifications,
        threads,
        media,
        settings,
        events,
        soundboard,
        polls,
        stickers,
        search,
        features,
        avatars,
        admin,
        telemetry,
        applications,
    )
    from src.core.ratelimit.storage import MemoryStorage

    # Import the canonical licensing module directly: ``from src.utils import
    # licensing`` can resolve to the proxy shim (src/utils/licensing.py) once
    # another test has imported it, and mutating the shim's copied
    # ``_free_tier_mode`` attribute would not touch the real module.
    import src.utils.common_utils.utils.licensing as licensing

    # Admin routes gate on licensing (check_host_restriction -> is_provisional)
    # and the messaging service gates v3 ratchet encryption on it, so the real
    # app calls licensing.setup() during setup_utilities(). Mirror that here,
    # but pin the deterministic free-tier state: licensing.setup() would
    # otherwise load a developer's on-disk license (~/.plexichat/config/license)
    # and flip premium features on, breaking unrelated order-dependent tests.
    licensing.setup()
    licensing._free_tier_mode = True
    licensing._license_manager = None

    # Setup rate limiting with memory storage
    storage = MemoryStorage(cleanup_interval=1.0, max_buckets=1000)
    ratelimit.setup(
        storage_backend=storage,
        bot_multiplier=1.5,
        enable_global_limit=True,
    )

    # Setup embeds
    embeds.setup(db, messaging_manager, server_manager)

    # Setup webhooks
    webhooks.setup(db, auth_manager, messaging_manager, server_manager, embeds._manager)

    # Setup threads
    threads.setup(db, auth_manager, messaging_manager, server_manager)

    # Setup notifications
    notifications.setup(
        db,
        auth_manager,
        messaging_manager,
        server_manager,
        rel_manager,
        presence_manager,
    )

    # Setup media
    media.setup(db, messaging_manager)

    # Setup settings
    settings.setup(db)

    # Setup events (production wires relationships/servers/messaging)
    events.setup(rel_manager, server_manager, messaging_manager)

    # Setup soundboard (media pipeline wired for byte uploads)
    soundboard.setup(db, servers_module=server_manager, media_module=media._manager)

    # Production-parity modules previously passed as None:
    polls.setup(db, messaging_manager, auth_module=auth_manager)
    stickers.setup(
        db,
        messaging_module=messaging_manager,
        servers_module=server_manager,
        media_module=media._manager,
    )
    search.setup(db, auth_manager, messaging_manager, server_manager)
    features.setup(db)
    avatars.setup(db)
    telemetry.setup(db)
    admin.setup(db, auth_module=auth_manager, features_module=features)
    # Applications backs the bot/OAuth routes (they read the module-level
    # singleton). bootstrap/app.py wires it the same way after events.
    applications.setup(
        db,
        auth_module=auth_manager,
        servers_module=server_manager,
        events_module=events._manager,
    )


def _reset_wired_module_globals():
    """Clear module-level manager globals pinned to this test's database.

    Without this, a leaked manager pointing at the deleted temp SQLite file
    makes later suites (that call these modules bare) hit deleted-file
    errors — the same order-dependence the per-fixture resets below guard
    against for media/notifications/soundboard.
    """
    from src.core import (
        polls,
        stickers,
        search,
        features,
        avatars,
        admin,
        telemetry,
        embeds,
        webhooks,
        threads,
        settings,
        events,
        ratelimit,
        applications,
    )

    polls._manager = None
    polls._setup_complete = False
    stickers._manager = None
    stickers._setup_complete = False
    search._manager = None
    search._setup_complete = False
    features._db = None
    features._setup_complete = False
    avatars._manager = None
    admin._db = None
    admin._auth_mod = None
    admin._features = None
    admin._setup_complete = False
    applications._manager = None
    applications._setup_complete = False
    telemetry._db = None
    telemetry._setup_complete = False
    # Remaining modules wired by ``_wire_module_graph`` that were previously
    # left pinned to this test's (now deleted) database.
    embeds._manager = None
    embeds._setup_complete = False
    webhooks._manager = None
    webhooks._setup_complete = False
    threads._manager = None
    threads._setup_complete = False
    settings._manager = None
    settings._setup_complete = False
    events._manager = None
    events._setup_complete = False
    ratelimit._manager = None
    ratelimit._setup_complete = False


# =============================================================================
# API Testing Fixtures
# =============================================================================


@pytest.fixture
def test_client(
    db,
    auth_manager,
    messaging_manager,
    server_manager,
    rel_manager,
    presence_manager,
    reaction_manager,
    reports,
    feedback,
):
    """Create a FastAPI test client with rate limiting disabled."""
    from fastapi.testclient import TestClient
    from src.api import create_app, setup

    _wire_module_graph(
        db,
        auth_manager,
        messaging_manager,
        server_manager,
        rel_manager,
        presence_manager,
        reaction_manager,
        reports,
        feedback,
    )

    from src.core import (
        embeds,
        webhooks,
        notifications,
        threads,
        media,
        settings,
        events,
        soundboard,
        polls,
        stickers,
        search,
        features,
        avatars,
        admin,
        telemetry,
    )

    # Setup API with all modules (production parity — see bootstrap/app.py)
    setup(
        db=db,
        auth_module=auth_manager,
        messaging_module=messaging_manager,
        polls_module=polls,
        servers_module=server_manager,
        relationships_module=rel_manager,
        presence_module=presence_manager,
        reactions_module=reaction_manager,
        stickers_module=stickers,
        soundboard_module=soundboard,
        embeds_module=embeds._manager,
        webhooks_module=webhooks._manager,
        settings_module=settings._manager,
        threads_module=threads._manager,
        notifications_module=notifications._manager,
        media_module=media._manager,
        search_module=search,
        features_module=features,
        avatars_module=avatars,
        reports_module=reports,
        feedback_module=feedback,
        admin_module=admin,
        events_module=events._manager,
        telemetry_module=telemetry,
    )

    app = create_app(enable_rate_limiting=False)
    yield TestClient(app)
    _teardown_wired_modules()


@pytest.fixture
def rate_limit_client(
    db,
    auth_manager,
    messaging_manager,
    server_manager,
    rel_manager,
    presence_manager,
    reaction_manager,
    reports,
    feedback,
):
    """Create a FastAPI test client with rate limiting enabled."""
    from fastapi.testclient import TestClient
    from src.api import create_app, setup

    _wire_module_graph(
        db,
        auth_manager,
        messaging_manager,
        server_manager,
        rel_manager,
        presence_manager,
        reaction_manager,
        reports,
        feedback,
    )

    from src.core import (
        embeds,
        webhooks,
        notifications,
        threads,
        media,
        settings,
        events,
        soundboard,
        polls,
        stickers,
        search,
        features,
        avatars,
        admin,
        telemetry,
    )

    # Setup API with all modules (production parity — see bootstrap/app.py)
    setup(
        db=db,
        auth_module=auth_manager,
        messaging_module=messaging_manager,
        polls_module=polls,
        servers_module=server_manager,
        relationships_module=rel_manager,
        presence_module=presence_manager,
        reactions_module=reaction_manager,
        stickers_module=stickers,
        soundboard_module=soundboard,
        embeds_module=embeds._manager,
        webhooks_module=webhooks._manager,
        settings_module=settings._manager,
        threads_module=threads._manager,
        notifications_module=notifications._manager,
        media_module=media._manager,
        search_module=search,
        features_module=features,
        avatars_module=avatars,
        reports_module=reports,
        feedback_module=feedback,
        admin_module=admin,
        events_module=events._manager,
        telemetry_module=telemetry,
    )

    app = create_app(enable_rate_limiting=True)
    yield TestClient(app)
    _teardown_wired_modules()


def _teardown_wired_modules():
    """Tear down background workers / module globals after each API test."""
    from src.core import media, notifications, soundboard

    # Tear down media background workers before the db fixture removes
    # the temp SQLite file (mmap'd-connection SIGBUS — see close()).
    if media._manager is not None:
        try:
            media._manager.close()
        except Exception:
            pass
        media._manager = None
        media._setup_complete = False

    # Reset the notifications module globals too: the send-path hook consults
    # them, and a leaked manager (pointing at this test's deleted db) makes
    # later suites' bare NotificationManager fixtures double-create rows and
    # hit UNIQUE(user_id, message_id) — the same order-dependence the media
    # reset above guards against.
    notifications._manager = None
    notifications._setup_complete = False

    # Same leak-guard for the soundboard manager: a stale manager pointing
    # at this test's deleted db breaks later suites.
    if soundboard._manager is not None:
        soundboard._manager = None
        soundboard._setup_complete = False

    # And for the production-parity modules wired by this fixture.
    _reset_wired_module_globals()


@pytest.fixture
def auth_headers(test_user_with_token):
    """Get authorization headers for authenticated requests."""
    user_data = test_user_with_token
    token = user_data["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api_module():
    """API module fixture for testing."""
    # This is a marker fixture - the actual API setup is done in the test_client fixture
    # Just return None to satisfy the fixture requirement
    return None
