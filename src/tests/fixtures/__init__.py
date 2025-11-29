"""
Shared test fixtures module.

This module provides reusable fixtures for all tests:
- Database session management with transaction isolation
- Module lazy loading and caching
- User/Server/Channel factories
- Shared test configuration
"""

from .config import get_test_config, TEST_PASSWORD
from .database import DatabaseManager
from .modules import ModuleRegistry

__all__ = [
    "get_test_config",
    "TEST_PASSWORD",
    "DatabaseManager",
    "ModuleRegistry",
]
