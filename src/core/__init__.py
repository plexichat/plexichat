"""
Core modules for PlexiChat application.

Contains:
- database: Database connectivity for SQLite and PostgreSQL
- auth: Authentication, sessions, 2FA, bots, permissions
- settings: User settings key-value store
"""

from . import database
from . import auth
from . import settings

__all__ = ['database', 'auth', 'settings']
