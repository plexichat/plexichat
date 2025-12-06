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

from typing import Optional

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
_organizations = None
_setup_complete = False


def setup(
    db,
    auth_module=None,
    messaging_module=None,
    servers_module=None,
    relationships_module=None,
    presence_module=None,
    reactions_module=None,
    embeds_module=None,
    notifications_module=None,
    webhooks_module=None,
    threads_module=None,
    media_module=None,
    settings_module=None,
    organizations_module=None
):
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
        organizations_module: Organizations module for org management
    """
    global _db, _auth, _messaging, _servers, _relationships, _presence
    global _reactions, _embeds, _notifications, _webhooks, _threads, _media, _settings, _organizations, _setup_complete

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
    _organizations = organizations_module
    _setup_complete = True


def get_db():
    """Get database instance."""
    if not _setup_complete:
        raise RuntimeError("API module not initialized. Call api.setup() first.")
    return _db


def get_auth():
    """Get auth module."""
    return _auth


def get_messaging():
    """Get messaging module."""
    return _messaging


def get_servers():
    """Get servers module."""
    return _servers


def get_relationships():
    """Get relationships module."""
    return _relationships


def get_presence():
    """Get presence module."""
    return _presence


def get_reactions():
    """Get reactions module."""
    return _reactions


def get_embeds():
    """Get embeds module."""
    return _embeds


def get_notifications():
    """Get notifications module."""
    return _notifications


def get_webhooks():
    """Get webhooks module."""
    return _webhooks


def get_threads():
    """Get threads module."""
    return _threads


def get_media():
    """Get media module."""
    return _media


def get_settings():
    """Get settings module."""
    return _settings


def get_organizations():
    """Get organizations module."""
    return _organizations


def is_setup() -> bool:
    """Check if API module is initialized."""
    return _setup_complete
