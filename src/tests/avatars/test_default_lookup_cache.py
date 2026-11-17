"""Default-avatar lookups are cached, and invalidation clears them.

Users and servers without an uploaded image used to re-query the checksum,
binary payload and profile/name tables on every GET. These tests pin that the
negative verdict and the default initials are reused, and that an upload
invalidation makes the next request re-check storage.
"""

from __future__ import annotations
from typing import Any, cast

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import src.api as api
from src.api.routes import avatars as avatar_routes
from src.api.routes.avatars import get_server_icon, get_user_avatar

UID = 555111
SID = 555222
SVG = '<svg width="128" height="128"></svg>'


@pytest.fixture(autouse=True)
def _clear_caches():
    for cache in (
        avatar_routes._avatar_checksum_cache,
        avatar_routes._avatar_absent_cache,
        avatar_routes._user_initials_cache,
        avatar_routes._icon_checksum_cache,
        avatar_routes._icon_absent_cache,
        avatar_routes._server_initials_cache,
        avatar_routes._default_svg_cache,
    ):
        cache.clear()
    yield


@pytest.mark.asyncio
async def test_user_default_skips_repeated_storage_and_profile_lookups():
    fake_avatars = MagicMock()
    fake_avatars.get_user_avatar_checksum.return_value = None
    fake_avatars.get_user_avatar_data.return_value = None
    auth = MagicMock()
    auth.get_user_profiles_bulk.return_value = {str(UID): {"username": "alice"}}
    request = SimpleNamespace(headers={})
    with (
        patch.object(api, "get_avatars", return_value=fake_avatars),
        patch.object(api, "get_auth", return_value=auth),
        patch("src.core.avatars.generate_default_svg", return_value=SVG) as gen_svg,
    ):
        first = await get_user_avatar(cast(Any, UID), cast(Any, request))
        second = await get_user_avatar(cast(Any, UID), cast(Any, request))

    assert first.body == second.body == SVG.encode()
    assert fake_avatars.get_user_avatar_checksum.call_count == 1
    assert fake_avatars.get_user_avatar_data.call_count == 1
    assert auth.get_user_profiles_bulk.call_count == 1
    # The rendered default template (and its ETag) is reused, not regenerated.
    assert gen_svg.call_count == 1


@pytest.mark.asyncio
async def test_user_invalidation_forces_a_fresh_lookup():
    fake_avatars = MagicMock()
    fake_avatars.get_user_avatar_checksum.return_value = None
    fake_avatars.get_user_avatar_data.return_value = None
    request = SimpleNamespace(headers={})
    with (
        patch.object(api, "get_avatars", return_value=fake_avatars),
        patch.object(api, "get_auth", return_value=None),
        patch("src.core.avatars.generate_default_svg", return_value=SVG),
    ):
        await get_user_avatar(cast(Any, UID), cast(Any, request))
        avatar_routes._invalidate_user_avatar_cache(UID)
        await get_user_avatar(cast(Any, UID), cast(Any, request))

    assert fake_avatars.get_user_avatar_checksum.call_count == 2
    assert fake_avatars.get_user_avatar_data.call_count == 2


@pytest.mark.asyncio
async def test_server_default_skips_repeated_name_lookups():
    fake_avatars = MagicMock()
    fake_avatars.get_server_icon_checksum.return_value = None
    fake_avatars.get_server_icon_data.return_value = None
    db = MagicMock()
    db.fetch_one.return_value = {"name": "Rocket League"}
    request = SimpleNamespace(headers={})
    with (
        patch.object(api, "get_avatars", return_value=fake_avatars),
        patch.object(api, "get_db", return_value=db),
        patch("src.core.avatars.generate_default_svg", return_value=SVG),
    ):
        await get_server_icon(cast(Any, SID), cast(Any, request))
        await get_server_icon(cast(Any, SID), cast(Any, request))

    assert fake_avatars.get_server_icon_checksum.call_count == 1
    assert fake_avatars.get_server_icon_data.call_count == 1
    assert db.fetch_one.call_count == 1
