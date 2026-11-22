"""Unwired settings must not turn a private profile into a public one.

``_get_profile_visibility`` falls back to the documented default (``public``)
whenever the raw stored value is falsy. ``_read_profile_visibility_raw`` returns
``None`` for *two* distinct states: "no visibility row exists" (public is the
right default) and "the settings module is unwired, so the row cannot be read".
A ``profile_visibility`` row lives in the database independently of the settings
module being wired in this process, so conflating the two states lets any viewer
read the full profile of an owner who stored ``private`` / ``friends`` /
``servers`` whenever that module is unwired.

Each test first proves the private row is visible when settings IS wired, then
unwires only the settings module and requires the restricted ``limited``
projection. That control makes the test non-vacuous: the stored value is real,
so a fail-closed result is attributable to the unwired gate.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]


class _FakeSettings:
    """A settings module whose backing store holds ``profile_visibility``."""

    def __init__(self, value: str) -> None:
        self.value = value

    def get_setting(self, user_id: int, key: str) -> str:
        return self.value


def _reset_cache(owner_id: int) -> None:
    from src.api.routes.features import profiles

    profiles.invalidate_profile_visibility_cache(owner_id)


def test_unwired_settings_fails_closed_to_limited():
    """Unwired settings + a stored private row must NOT resolve to public."""
    from src.api.routes.features import profiles

    owner = 900_001
    _reset_cache(owner)

    # The row exists in the backing store: a wired settings module reads it.
    with patch("src.api.get_settings", return_value=_FakeSettings("private")):
        assert profiles._get_profile_visibility(owner) == "private"

    # Only the module wiring changes; the row is still there.
    _reset_cache(owner)
    with patch("src.api.get_settings", return_value=None):
        visibility = profiles._get_profile_visibility(owner)

    assert visibility == profiles._FAIL_CLOSED_VISIBILITY
    assert visibility != profiles._DEFAULT_VISIBILITY


def test_unwired_settings_restricts_projection_for_other_viewer():
    """The fail-closed visibility must deny a full cross-user profile read."""
    from src.api.routes.features import profiles

    owner = 900_002
    viewer = SimpleNamespace(user_id=900_102, token_type="user")
    _reset_cache(owner)

    # Non-vacuity: when settings is wired and the stored value is public, the
    # other viewer gets the full projection (None).
    with (
        patch("src.api.get_settings", return_value=_FakeSettings("everyone")),
        patch.object(profiles, "_is_blocked_between", return_value=False),
    ):
        assert profiles._profile_projection_for(viewer, owner) is None

    # Now a private row with the module unwired: must be restricted even though
    # the cached default would otherwise say "public".
    _reset_cache(owner)
    with (
        patch("src.api.get_settings", return_value=None),
        patch.object(profiles, "_is_blocked_between", return_value=False),
    ):
        fields = profiles._profile_projection_for(viewer, owner)

    assert fields == profiles._LIMITED_PROFILE_FIELDS
