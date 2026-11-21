"""Private bot-profile disclosure must reject a malformed owner row.

``BotManagementMixin.get_bot_profile`` gates a private profile on
``int(owner["owner_id"]) != int(viewer_id)`` from an authoritative
``app_applications.owner_id`` row. A malformed-but-coercible value
("05", " 5 ", 5.9, True) makes a non-owner viewer look like the owner and
discloses a profile explicitly marked private (the directory filters it out).
Only a genuine integer id from the row may satisfy the gate.
"""

import pytest

from src.core.applications.manager.bot_management import BotManagementMixin

pytestmark = pytest.mark.unit

_APP_ID = 500
_PROFILE_ROW = {
    "application_id": _APP_ID,
    "description": "SECRET-DESCRIPTION",
    "short_description": "secret",
    "avatar_url": None,
    "banner_url": None,
    "website_url": None,
    "support_url": None,
    "github_url": None,
    "tags": None,
    "nsfw": 0,
    "private": 1,
    "updated_at": 0,
}


class _DB:
    def __init__(self, owner_value):
        self.owner_value = owner_value

    def fetch_one(self, query, params=None):
        if "app_bot_profiles" in query:
            return dict(_PROFILE_ROW)
        if "app_applications" in query:
            return {"owner_id": self.owner_value}
        return None


class _Service(BotManagementMixin):
    def __init__(self, owner_value):
        self._db = _DB(owner_value)


def _profile_visible(owner_value, viewer_id):
    return _Service(owner_value).get_bot_profile(_APP_ID, viewer_id) is not None


_MALFORMED = [
    ("05", 5),
    (" 5 ", 5),
    (5.9, 5),
    ("5", 5),
    (True, 1),
]


@pytest.mark.parametrize("owner_value, coerced_id", _MALFORMED)
def test_malformed_owner_row_does_not_disclose_private_profile(
    owner_value, coerced_id
):
    assert _profile_visible(owner_value, coerced_id) is False, (
        f"malformed owner_id {owner_value!r} disclosed a private bot profile "
        f"to viewer {coerced_id}"
    )


def test_genuine_integer_owner_still_sees_private_profile():
    assert _profile_visible(5, 5) is True


def test_non_owner_integer_row_fails_closed():
    assert _profile_visible(7, 5) is False
