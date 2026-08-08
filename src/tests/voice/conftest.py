"""
Voice test fixtures.
"""

import pytest

# common_utils is now a native package.


import utils.config as config  # noqa: E402
import utils.version as version  # noqa: E402

# Setup config before importing voice modules
DEFAULT_TEST_CONFIG = {
    "voice": {
        "enabled": True,
        "janus_url": "http://localhost:8088/janus",
    },
}

# Merge into the root test config instead of re-initializing it:
# config.setup() replaces the entire config, clobbering the
# authentication/search/media sections other suites depend on
# (lockout thresholds, discovery limits, compression defaults).
for _key, _value in DEFAULT_TEST_CONFIG.items():
    _existing = config.get(_key)
    if isinstance(_existing, dict) and isinstance(_value, dict):
        _merged = dict(_existing)
        _merged.update(_value)
        _value = _merged
    config.set(_key, _value)
version.setup(current_version="r.1.0-1", min_supported_version="a.1.0-1")


@pytest.fixture
def mock_voice_config():
    """Mock voice configuration for tests."""
    return {
        "enabled": True,
        "janus_url": "http://localhost:8088/janus",
        "turn_servers": [],
    }


@pytest.fixture
def voice_manager(db, auth_manager, server_manager, rel_manager):
    """Build a VoiceManager on the shared test managers.

    Required by the root ``server_with_voice`` / ``server_with_moderator``
    fixtures; the signaling ``setup(voice_module=...)`` calls in the voice
    tests invoke manager methods (get_voice_channel, set_streaming, ...).
    """
    from src.core.voice.manager import VoiceManager

    return VoiceManager(
        db,
        auth_module=auth_manager,
        servers_module=server_manager,
        relationships_module=rel_manager,
        presence_module=None,
    )


@pytest.fixture
def db_and_modules(db, auth_manager, server_manager, voice_manager):
    """Voice-tests override of the root ``db_and_modules``.

    The voice tests unpack ``(db, auth, servers, relationships, presence,
    voice)`` from this tuple and hand ``voice`` to the signaling setup; the
    root fixture returns a different, messaging-oriented shape.
    """
    return db, auth_manager, server_manager, None, None, voice_manager
