"""Regressions for cache-key tag injection and session tenant spoofing.

* ``generate_cache_key`` bounded and delimiter-escaped *values* but not the
  ``tag`` they are paired with. Because an object's ``__name__`` is forgeable
  (``type("Evil:Manager", (), {})``), one argument could inject extra
  ``:``-delimited segments and forge another caller's key.
* ``cache_session`` unpacked caller data *after* the trusted ownership fields,
  so a payload key named ``user_id`` reassigned the session to another tenant
  and broke tenant-scoped invalidation.
* ``_glob_safe_id`` accepted unicode digit strings that ``int`` cannot parse,
  leaking a ``ValueError`` instead of rejecting the unsafe id.
"""

from typing import cast

import pytest

from src.core.database.cache import operations, session
from src.core.database.cache.base import _mem_cache
from src.core.database.cache.serialization import generate_cache_key


# ---------------------------------------------------------------------------
# cache key tag (type/class name) injection
# ---------------------------------------------------------------------------


def test_forged_manager_class_name_cannot_inject_key_segments():
    forged = type("Evil:Manager", (), {})()
    key = generate_cache_key("p", forged)
    # Pre-fix this was "p:Core:Evil:Manager" -- one argument, three segments.
    assert key != "p:Core:Evil:Manager"
    assert key.count(":") == 1  # prefix + exactly one component


def test_forged_dict_subclass_name_cannot_inject_key_segments():
    forged = type("dict:int", (dict,), {})({"a": 1})
    key = generate_cache_key("p", forged)
    # Pre-fix this was "p:dict:int:<digest>" -- the class name forged two
    # extra segments. The unsafe tag must now be a single 24-hex digest.
    assert ":dict:int:" not in key
    parts = key.split(":")
    assert len(parts) == 3  # prefix, hashed tag, value digest
    assert len(parts[1]) == 24


def test_forged_class_names_stay_distinct():
    a = generate_cache_key("p", type("Evil:Manager", (), {})())
    b = generate_cache_key("p", type("Other:Manager", (), {})())
    assert a != b


def test_ordinary_manager_and_numeric_key_shapes_are_preserved():
    # Hashing unsafe tags must not disturb the legacy shapes that invalidation
    # globs and callers rely on.
    assert generate_cache_key("p", type("MigrationManager", (), {})()) == (
        "p:Core:MigrationManager"
    )
    assert generate_cache_key("p", 7) == "p:int:7"
    assert generate_cache_key("p", {"a": 1}).startswith("p:dict:")


# ---------------------------------------------------------------------------
# session tenant ownership / invalidation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _offline_mem_only(monkeypatch):
    _mem_cache.clear()
    for module in (session, operations):
        monkeypatch.setattr(module, "get_client", lambda: None)
        monkeypatch.setattr(module, "is_available", lambda: False)
    yield
    _mem_cache.clear()


def test_session_payload_cannot_override_authenticated_user_id():
    session.cache_session(
        "sess-tenant", user_id=7, data={"user_id": 8, "ip": "1.2.3.4"}
    )
    cached = session.get_cached_session("sess-tenant")
    assert cached is not None
    assert cached["user_id"] == 7
    assert cached["ip"] == "1.2.3.4"

    # The spoofed tenant must not be able to evict this session...
    assert session.invalidate_user_sessions(8) == 0
    assert session.get_cached_session("sess-tenant") is not None
    # ...while the real owner's "logout everywhere" still must.
    assert session.invalidate_user_sessions(7) >= 1
    assert session.get_cached_session("sess-tenant") is None


def test_session_payload_cannot_override_created_at():
    session.cache_session("sess-clock", user_id=1, data={"created_at": 0.0})
    cached = session.get_cached_session("sess-clock")
    assert cached is not None
    assert cached["created_at"] > 0.0


# ---------------------------------------------------------------------------
# glob-injection guard rejects malformed ids without raising
# ---------------------------------------------------------------------------


def test_glob_safe_id_rejects_unicode_digits_int_cannot_parse():
    # "²".isdigit() is True but int("²") raises -- the guard must return None.
    assert session._glob_safe_id("²") is None
    assert session.invalidate_server_channels(cast(int, "²")) == 0
    assert session.invalidate_user_servers(cast(int, "²")) == 0


def test_glob_safe_id_still_normalizes_valid_ids():
    assert session._glob_safe_id("0042") == "42"
    assert session._glob_safe_id("1:*") is None
