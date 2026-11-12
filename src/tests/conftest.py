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
from src.tests._conftest import config_env as config_env

from src.tests._conftest.config_env import (
    DEFAULT_TEST_CONFIG as DEFAULT_TEST_CONFIG,
    address_whitelisted as address_whitelisted,
    _test_always_valid_email as _test_always_valid_email,
)
from src.tests._conftest.db_fixtures import (
    _TEST_DB_POOL_CONFIG as _TEST_DB_POOL_CONFIG,
    _isolate_decorator_cache as _isolate_decorator_cache,
    _isolate_global_config as _isolate_global_config,
    _isolate_process_globals as _isolate_process_globals,
    db as db,
    fake_crypto as fake_crypto,
    lenient_email_validation as lenient_email_validation,
    migrated_db_template as migrated_db_template,
    setup_config as setup_config,
    test_db as test_db,
)
from src.tests._conftest.manager_fixtures import (
    app_manager as app_manager,
    auth_manager as auth_manager,
    automod_manager as automod_manager,
    embeds_manager as embeds_manager,
    events_module as events_module,
    feedback as feedback,
    media_manager as media_manager,
    messaging_manager as messaging_manager,
    notification_manager as notification_manager,
    poll_manager as poll_manager,
    presence_manager as presence_manager,
    reaction_manager as reaction_manager,
    rel_manager as rel_manager,
    reports as reports,
    search_manager as search_manager,
    server_manager as server_manager,
    settings_manager as settings_manager,
    soundboard_manager as soundboard_manager,
    sticker_manager as sticker_manager,
    thread_manager as thread_manager,
    webhook_manager as webhook_manager,
)
from src.tests._conftest.user_fixtures import (
    TestPIIGenerator as TestPIIGenerator,
    dm_with_message as dm_with_message,
    fresh_server as fresh_server,
    fresh_server_tuple as fresh_server_tuple,
    fresh_users as fresh_users,
    fresh_users_with_dm as fresh_users_with_dm,
    fresh_users_with_dm_and_relationships as fresh_users_with_dm_and_relationships,
    friends_pair as friends_pair,
    group_with_message as group_with_message,
    logged_in_user as logged_in_user,
    pii_gen as pii_gen,
    poll_with_options as poll_with_options,
    registered_user as registered_user,
    server_with_channel as server_with_channel,
    server_with_channels as server_with_channels,
    server_with_members as server_with_members,
    server_with_moderator as server_with_moderator,
    server_with_voice as server_with_voice,
    test_dm as test_dm,
    test_server as test_server,
    test_user as test_user,
    test_user_with_token as test_user_with_token,
    three_users as three_users,
    two_users as two_users,
    users_with_dm as users_with_dm,
    users_with_dm_and_reaction as users_with_dm_and_reaction,
    users_with_server as users_with_server,
    users_with_server_search as users_with_server_search,
    webhook_with_token as webhook_with_token,
)
from src.tests._conftest.api_fixtures import (
    _reset_wired_module_globals as _reset_wired_module_globals,
    _shared_app as _shared_app,
    _teardown_wired_modules as _teardown_wired_modules,
    _wire_module_graph as _wire_module_graph,
    api_module as api_module,
    auth_headers as auth_headers,
    rate_limit_client as rate_limit_client,
    test_client as test_client,
)
from src.tests._conftest.module_registry import (
    DatabaseAndAuth as DatabaseAndAuth,
    ModuleBundle as ModuleBundle,
    ModuleRegistry as ModuleRegistry,
    UserPool as UserPool,
    db_and_auth as db_and_auth,
    db_and_modules as db_and_modules,
    db_and_search as db_and_search,
    modules as modules,
    user_pool as user_pool,
)
from src.tests._conftest.misc_fixtures import (
    _FakeRateLimitClock as _FakeRateLimitClock,
    email_sender as email_sender,
    memory_storage as memory_storage,
    mocker as mocker,
    rate_limit_clock as rate_limit_clock,
    rate_limit_manager as rate_limit_manager,
    setup_ratelimit as setup_ratelimit,
    test_channel_id as test_channel_id,
    test_user_id as test_user_id,
    test_webhook_id as test_webhook_id,
)
from src.tests._conftest.logger_isolation import (
    isolated_logger as isolated_logger,
    _app_logger_level_guard as _app_logger_level_guard,
    _global_singleton_guard as _global_singleton_guard,
)
from src.tests._conftest.hooks import (
    pytest_configure as pytest_configure,
    pytest_collection_modifyitems as pytest_collection_modifyitems,
    pytest_sessionfinish as pytest_sessionfinish,
)

# PostgreSQL Docker fixtures: pytest only sees fixtures defined in (or
# imported into) a conftest on the collection path, so they are re-exported
# here. With them exposed, the Postgres tests SKIP VISIBLY ("PostgreSQL not
# available") in environments without a backend and RUN where one exists —
# instead of erroring on a missing fixture or being silently deselected.
from src.tests.fixtures.postgres_docker import (
    clean_postgres_db as clean_postgres_db,
    postgres_config as postgres_config,
    postgres_connection_pool_tester as postgres_connection_pool_tester,
    postgres_db as postgres_db,
    postgres_db_with_constraints as postgres_db_with_constraints,
    postgres_db_with_table as postgres_db_with_table,
    postgres_manager as postgres_manager,
)

# Valkey Docker fixtures, re-exported for the same reason: without a live
# server the Valkey-backed tests skip visibly ("Valkey not available") and
# run wherever one exists (CI service container, local Docker, ...).
from src.tests.fixtures.valkey_docker import (
    async_valkey_client as async_valkey_client,
    valkey_client as valkey_client,
    valkey_config as valkey_config,
    valkey_manager as valkey_manager,
)

__all__ = [
    "config_env",
    "DEFAULT_TEST_CONFIG",
    "address_whitelisted",
    "_test_always_valid_email",
    "_isolate_global_config",
    "_isolate_decorator_cache",
    "_isolate_process_globals",
    "pytest_configure",
    "pytest_collection_modifyitems",
    "pytest_sessionfinish",
]
