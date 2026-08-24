"""FastAPI TestClient fixtures and auth helpers."""

import pytest


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
    from src.core import (
        ratelimit,
        embeds,
        webhooks,
        notifications,
        threads,
        media,
        settings,
        events,
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

    # Setup events
    events.setup(rel_manager, server_manager, messaging_manager)

    # Setup API with all modules
    setup(
        db=db,
        auth_module=auth_manager,
        messaging_module=messaging_manager,
        servers_module=server_manager,
        relationships_module=rel_manager,
        presence_module=presence_manager,
        reactions_module=reaction_manager,
        embeds_module=embeds._manager,
        webhooks_module=webhooks._manager,
        settings_module=settings._manager,
        threads_module=threads._manager,
        notifications_module=notifications._manager,
        media_module=media._manager,
        events_module=events._manager,
        reports_module=reports,
        feedback_module=feedback,
    )

    app = create_app(enable_rate_limiting=False)
    yield TestClient(app)
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
    from src.core import (
        ratelimit,
        embeds,
        webhooks,
        notifications,
        threads,
        media,
        settings,
        events,
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

    # Setup events
    events.setup(rel_manager, server_manager, messaging_manager)

    # Setup API with all modules
    setup(
        db=db,
        auth_module=auth_manager,
        messaging_module=messaging_manager,
        servers_module=server_manager,
        relationships_module=rel_manager,
        presence_module=presence_manager,
        reactions_module=reaction_manager,
        embeds_module=embeds._manager,
        webhooks_module=webhooks._manager,
        settings_module=settings._manager,
        threads_module=threads._manager,
        notifications_module=notifications._manager,
        media_module=media._manager,
        events_module=events._manager,
        reports_module=reports,
        feedback_module=feedback,
    )

    app = create_app(enable_rate_limiting=True)
    yield TestClient(app)
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
