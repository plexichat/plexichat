"""
Core modules for PlexiChat application.

Contains:
- database: Database connectivity for SQLite and PostgreSQL
- auth: Authentication, sessions, 2FA, bots, permissions
"""

from . import database
from . import auth

__all__ = ['database', 'auth']
