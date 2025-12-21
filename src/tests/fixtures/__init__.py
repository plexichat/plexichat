"""
Shared test fixtures module.

This module provides reusable fixtures for all tests:
- Database session management with transaction isolation
- Module lazy loading and caching
- User/Server/Channel factories
- Shared test configuration
- Security testing utilities and payloads
"""

from .config import get_test_config, TEST_PASSWORD
from .database import DatabaseManager
from .modules import ModuleRegistry
from .security import (
    XSSPayloads,
    SQLInjectionPayloads,
    MalformedInputs,
    AuthenticationPayloads,
    SecurityAssertions,
    SecurityTestHelper,
    test_xss_vectors,
    test_sql_injection,
    test_input_validation,
)

__all__ = [
    "get_test_config",
    "TEST_PASSWORD",
    "DatabaseManager",
    "ModuleRegistry",
    "XSSPayloads",
    "SQLInjectionPayloads",
    "MalformedInputs",
    "AuthenticationPayloads",
    "SecurityAssertions",
    "SecurityTestHelper",
    "test_xss_vectors",
    "test_sql_injection",
    "test_input_validation",
]
