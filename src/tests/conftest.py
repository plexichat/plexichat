"""
Root conftest.py - Simple, fast fixtures for all tests.

PRINCIPLES:
- Function-scoped databases for complete test isolation
- Fake hashing for instant test execution
- Simple, clear fixture dependencies
- No complex user pools or session-scoped state
- No legacy compatibility layers

The fixture/hook implementation lives in ``src/tests/_conftest/`` (split to
keep every module small); everything is re-imported here so pytest's
conftest discovery sees exactly the same names as before.
"""

# Import-time environment setup MUST run first: it installs the test config,
# logger, and validator shims that every fixture depends on.
from src.tests._conftest import config_env as config_env  # noqa: F401

from src.tests._conftest.config_env import (  # noqa: F401
    DEFAULT_TEST_CONFIG,
    address_whitelisted,
    _test_always_valid_email,
)
from src.tests._conftest.db_fixtures import *  # noqa: F401,F403
from src.tests._conftest.db_fixtures import (  # noqa: F401
    _isolate_global_config,
)
from src.tests._conftest.manager_fixtures import *  # noqa: F401,F403
from src.tests._conftest.user_fixtures import *  # noqa: F401,F403
from src.tests._conftest.api_fixtures import *  # noqa: F401,F403
from src.tests._conftest.module_registry import *  # noqa: F401,F403
from src.tests._conftest.misc_fixtures import *  # noqa: F401,F403
from src.tests._conftest.logger_isolation import (  # noqa: F401
    isolated_logger,
    _app_logger_level_guard,
)
from src.tests._conftest.hooks import (  # noqa: F401
    pytest_configure,
    pytest_collection_modifyitems,
    pytest_sessionfinish,
)

# PostgreSQL Docker fixtures: pytest only sees fixtures defined in (or
# imported into) a conftest on the collection path, so they are re-exported
# here. With them exposed, the Postgres tests SKIP VISIBLY ("PostgreSQL not
# available") in environments without a backend and RUN where one exists —
# instead of erroring on a missing fixture or being silently deselected.
from src.tests.fixtures.postgres_docker import (  # noqa: F401
    clean_postgres_db,
    postgres_config,
    postgres_connection_pool_tester,
    postgres_db,
    postgres_db_with_constraints,
    postgres_db_with_table,
    postgres_manager,
)

# Valkey Docker fixtures, re-exported for the same reason: without a live
# server the Valkey-backed tests skip visibly ("Valkey not available") and
# run wherever one exists (CI service container, local Docker, ...).
from src.tests.fixtures.valkey_docker import (  # noqa: F401
    async_valkey_client,
    valkey_client,
    valkey_config,
    valkey_manager,
)

__all__ = [
    "config_env",
    "DEFAULT_TEST_CONFIG",
    "address_whitelisted",
    "_test_always_valid_email",
    "_isolate_global_config",
    "pytest_configure",
    "pytest_collection_modifyitems",
    "pytest_sessionfinish",
]
