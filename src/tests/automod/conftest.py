"""
AutoMod test fixtures.

Provides session-scoped automod setup and test helpers.
"""

import pytest
import uuid

from src.core import automod
from src.core.automod import RuleType


@pytest.fixture(scope="session")
def automod_module(modules):
    """Initialize automod module for tests."""
    automod.setup(
        modules._db,
        modules.servers,
        modules.messaging,
        getattr(modules, 'notifications', None)
    )
    return automod


@pytest.fixture
def test_server_for_automod(modules, user_pool):
    """Create a test server for automod tests."""
    owner = user_pool.get_user()
    server = modules.servers.create_server(
        owner_id=owner.id,
        name=f"AutoMod Test Server {uuid.uuid4().hex[:6]}"
    )

    channel = modules.servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name="general",
        channel_type=modules.servers.ChannelType.TEXT
    )

    return server, channel, owner


@pytest.fixture
def keyword_rule(automod_module, test_server_for_automod):
    """Create a keyword filter rule."""
    server, channel, owner = test_server_for_automod

    rule = automod_module.create_rule(
        user_id=owner.id,
        server_id=server.id,
        name="Test Keyword Filter",
        rule_type=RuleType.KEYWORD,
        rule_config={
            "keywords": ["badword", "spam", "scam"],
            "case_sensitive": False,
            "whole_word": True
        },
        actions=[
            {"action_type": "delete_message"},
            {"action_type": "log_only"}
        ]
    )

    return rule, server, channel, owner


@pytest.fixture
def regex_rule(automod_module, test_server_for_automod):
    """Create a regex pattern rule."""
    server, channel, owner = test_server_for_automod

    rule = automod_module.create_rule(
        user_id=owner.id,
        server_id=server.id,
        name="Test Regex Filter",
        rule_type=RuleType.REGEX,
        rule_config={
            "patterns": [
                {"pattern": r"\b\d{16}\b", "name": "credit_card", "severity": "high"},
                {"pattern": r"free\s+money", "name": "scam_phrase", "severity": "medium"}
            ]
        },
        actions=[
            {"action_type": "delete_message"},
            {"action_type": "alert_moderators"}
        ]
    )

    return rule, server, channel, owner


@pytest.fixture
def spam_rule(automod_module, test_server_for_automod):
    """Create a spam detection rule."""
    server, channel, owner = test_server_for_automod

    rule = automod_module.create_rule(
        user_id=owner.id,
        server_id=server.id,
        name="Test Spam Detection",
        rule_type=RuleType.MESSAGE_SPAM,
        rule_config={
            "max_messages": 3,
            "window_seconds": 5,
            "duplicate_threshold": 2,
            "duplicate_window_seconds": 30
        },
        actions=[
            {"action_type": "timeout_user", "duration_seconds": 60}
        ]
    )

    return rule, server, channel, owner


@pytest.fixture
def mention_rule(automod_module, test_server_for_automod):
    """Create a mention spam rule."""
    server, channel, owner = test_server_for_automod

    rule = automod_module.create_rule(
        user_id=owner.id,
        server_id=server.id,
        name="Test Mention Spam",
        rule_type=RuleType.MENTION_SPAM,
        rule_config={
            "max_user_mentions": 3,
            "max_role_mentions": 2,
            "max_total_mentions": 5,
            "block_everyone": True
        },
        actions=[
            {"action_type": "delete_message"}
        ]
    )

    return rule, server, channel, owner


def pytest_configure(config):
    """Register automod marker."""
    config.addinivalue_line("markers", "automod: AutoMod module tests")
