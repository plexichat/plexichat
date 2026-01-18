"""
REST API module - FastAPI application factory for PlexiChat API.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.api import create_app, setup
    setup(db, auth, messaging, servers, relationships, presence, reactions, embeds, notifications, webhooks, threads)
    app = create_app()

    # Run with uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
"""

from typing import Any, Optional

from .app import create_app
from .config import APIConfig, get_api_config

__all__ = [
    "create_app",
    "setup",
    "APIConfig",
    "get_api_config",
    "get_telemetry",
]

_db = None
_auth = None
_messaging = None
_servers = None
_relationships = None
_presence = None
_reactions = None
_embeds = None
_notifications = None
_webhooks = None
_threads = None
_media = None
_settings = None
_features = None
_avatars = None
_reports = None
_feedback = None
_admin = None
_events = None
_telemetry = None
_internal_secret = None
_setup_complete = False


def setup(
    db: Any,
    auth_module: Optional[Any] = None,
    messaging_module: Optional[Any] = None,
    servers_module: Optional[Any] = None,
    relationships_module: Optional[Any] = None,
    presence_module: Optional[Any] = None,
    reactions_module: Optional[Any] = None,
    embeds_module: Optional[Any] = None,
    notifications_module: Optional[Any] = None,
    webhooks_module: Optional[Any] = None,
    threads_module: Optional[Any] = None,
    media_module: Optional[Any] = None,
    settings_module: Optional[Any] = None,
    features_module: Optional[Any] = None,
    avatars_module: Optional[Any] = None,
    reports_module: Optional[Any] = None,
    feedback_module: Optional[Any] = None,
    admin_module: Optional[Any] = None,
    events_module: Optional[Any] = None,
    telemetry_module: Optional[Any] = None,
) -> None:
    """
    Initialize the API module with all dependencies.

    Args:
        db: Database instance (must be connected)
        auth_module: Auth module for authentication
        messaging_module: Messaging module for messages
        servers_module: Servers module for guilds/channels
        relationships_module: Relationships module for friends/blocks
        presence_module: Presence module for user status
        reactions_module: Reactions module for message reactions
        embeds_module: Embeds module for rich embeds
        notifications_module: Notifications module for mentions
        webhooks_module: Webhooks module for webhook execution
        threads_module: Threads module for thread management
        media_module: Media module for file uploads
        settings_module: Settings module for user preferences
        features_module: Features module for user tiers and badges
        avatars_module: Avatars module for user/server avatars
        reports_module: Reports module for message/user reporting
        feedback_module: Feedback module for user feedback
        admin_module: Admin module for administration
        events_module: Events module for event delivery
        telemetry_module: Telemetry module for client metrics
    """
    global _db, _auth, _messaging, _servers, _relationships, _presence
    global \
        _reactions, \
        _embeds, \
        _notifications, \
        _webhooks, \
        _threads, \
        _media, \
        _settings, \
        _features, \
        _avatars, \
        _reports, \
        _feedback, \
        _admin, \
        _events, \
        _telemetry, \
        _setup_complete

    _db = db
    _auth = auth_module
    _messaging = messaging_module
    _servers = servers_module
    _relationships = relationships_module
    _presence = presence_module
    _reactions = reactions_module
    _embeds = embeds_module
    _notifications = notifications_module
    _webhooks = webhooks_module
    _threads = threads_module
    _media = media_module
    _settings = settings_module
    _features = features_module
    _avatars = avatars_module
    _reports = reports_module
    _feedback = feedback_module
    _admin = admin_module
    _events = events_module
    _telemetry = telemetry_module
    _setup_complete = True


def set_internal_secret(secret: str) -> None:
    """Set the one-time internal secret for self-test validation."""
    global _internal_secret
    _internal_secret = secret


def get_internal_secret() -> Optional[str]:
    """Get the one-time internal secret."""
    return _internal_secret


def get_database() -> Optional[Any]:
    """Get database instance."""
    if not _setup_complete:
        # If not setup but we have a database from somewhere else (like test runner)
        # return it, otherwise raise
        if _db:
            return _db
        raise RuntimeError("API module not initialized. Call api.setup() first.")
    return _db


def get_auth() -> Optional[Any]:
    """Get auth module."""
    if _auth:
        return _auth
    if _setup_complete:
        return (
            None  # If setup was called but _auth is None, it's explicitly unavailable
        )
    # Fallback to importing if already setup globally but not passed here
    try:
        from src.core import auth

        return auth
    except ImportError:
        return None


def get_messaging() -> Optional[Any]:
    """Get messaging module."""
    if _messaging:
        return _messaging
    try:
        from src.core import messaging

        return messaging
    except ImportError:
        return None


def get_servers() -> Optional[Any]:
    """Get servers module."""
    if _servers:
        return _servers
    try:
        from src.core import servers

        return servers
    except ImportError:
        return None


def get_relationships() -> Optional[Any]:
    """Get relationships module."""
    if _relationships:
        return _relationships
    try:
        from src.core import relationships

        return relationships
    except ImportError:
        return None


def get_presence() -> Optional[Any]:
    """Get presence module."""
    if _presence:
        return _presence
    try:
        from src.core import presence

        return presence
    except ImportError:
        return None


def get_reactions() -> Optional[Any]:
    """Get reactions module."""
    if _reactions:
        return _reactions
    try:
        from src.core import reactions

        return reactions
    except ImportError:
        return None


def get_embeds() -> Optional[Any]:
    """Get embeds module."""
    if _embeds:
        return _embeds
    try:
        from src.core import embeds

        return embeds
    except ImportError:
        return None


def get_notifications() -> Optional[Any]:
    """Get notifications module."""
    if _notifications:
        return _notifications
    try:
        from src.core import notifications

        return notifications
    except ImportError:
        return None


def get_webhooks() -> Optional[Any]:
    """Get webhooks module."""
    if _webhooks:
        return _webhooks
    try:
        from src.core import webhooks

        return webhooks
    except ImportError:
        return None


def get_threads() -> Optional[Any]:
    """Get threads module."""
    if _threads:
        return _threads
    try:
        from src.core import threads

        return threads
    except ImportError:
        return None


def get_media() -> Optional[Any]:
    """Get media module."""
    if _media:
        return _media
    try:
        from src.core import media

        return media
    except ImportError:
        return None


def get_settings() -> Optional[Any]:
    """Get settings module."""
    if _settings:
        return _settings
    try:
        from src.core import settings

        return settings
    except ImportError:
        return None


def get_features() -> Optional[Any]:
    """Get features module."""
    if _features:
        return _features
    try:
        from src.core import features

        return features
    except ImportError:
        return None


def get_avatars() -> Optional[Any]:
    """Get avatars module."""
    if _avatars:
        return _avatars
    try:
        from src.core import avatars

        return avatars
    except ImportError:
        return None


def get_events() -> Optional[Any]:
    """Get events module."""
    if _events:
        return _events
    try:
        from src.core import events

        return events
    except ImportError:
        return None


def get_reports() -> Optional[Any]:
    """Get reports module."""
    if _reports:
        return _reports
    try:
        from src.core import reports

        return reports
    except ImportError:
        return None


def get_feedback() -> Optional[Any]:
    """Get feedback module."""
    if _feedback:
        return _feedback
    try:
        from src.core import feedback

        return feedback
    except ImportError:
        return None


def get_admin() -> Optional[Any]:
    """Get admin module."""
    if _admin:
        return _admin
    try:
        from src.core import admin

        return admin
    except ImportError:
        return None


def get_telemetry() -> Optional[Any]:
    """Get telemetry module."""
    if _telemetry:
        return _telemetry
    try:
        from src.core import telemetry

        return telemetry
    except ImportError:
        return None


def is_setup() -> bool:
    """Check if API module is initialized."""
    return _setup_complete
