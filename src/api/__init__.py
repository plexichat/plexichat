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
    _setup_complete = True


def get_db() -> Optional[Any]:
    """Get database instance."""
    if not _setup_complete:
        raise RuntimeError("API module not initialized. Call api.setup() first.")
    return _db


def get_auth() -> Optional[Any]:
    """Get auth module."""
    return _auth


def get_messaging() -> Optional[Any]:
    """Get messaging module."""
    return _messaging


def get_servers() -> Optional[Any]:
    """Get servers module."""
    return _servers


def get_relationships() -> Optional[Any]:
    """Get relationships module."""
    return _relationships


def get_presence() -> Optional[Any]:
    """Get presence module."""
    return _presence


def get_reactions() -> Optional[Any]:
    """Get reactions module."""
    return _reactions


def get_embeds() -> Optional[Any]:
    """Get embeds module."""
    return _embeds


def get_notifications() -> Optional[Any]:
    """Get notifications module."""
    return _notifications


def get_webhooks() -> Optional[Any]:
    """Get webhooks module."""
    return _webhooks


def get_threads() -> Optional[Any]:
    """Get threads module."""
    return _threads


def get_media() -> Optional[Any]:
    """Get media module."""
    return _media


def get_settings() -> Optional[Any]:
    """Get settings module."""
    return _settings


def get_features() -> Optional[Any]:
    """Get features module."""
    return _features


def get_avatars() -> Optional[Any]:
    """Get avatars module."""
    return _avatars


def get_events() -> Optional[Any]:
    """Get events module."""
    return _events


def get_reports() -> Optional[Any]:
    """Get reports module."""
    return _reports


def get_feedback() -> Optional[Any]:
    """Get feedback module."""
    return _feedback


def get_admin() -> Optional[Any]:
    """Get admin module."""
    return _admin


def is_setup() -> bool:
    """Check if API module is initialized."""
    return _setup_complete
