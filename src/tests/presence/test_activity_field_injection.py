"""Regression tests for activity field injection / weak URL validation.

Activity fields are broadcast with presence to other users. Control characters
can forge log lines or downstream frames, and the old ``startswith("http")``
scheme check accepted embedded credentials and smuggled URLs.
"""

import pytest

from src.core.presence.exceptions import InvalidActivityError
from src.core.presence.models import ActivityType


@pytest.mark.presence
def test_control_characters_in_name_rejected(fresh_users):
    user1, _user2, presence = fresh_users
    with pytest.raises(InvalidActivityError):
        presence.set_activity(user1.id, ActivityType.PLAYING, "Game\r\n[INFO] fake")


@pytest.mark.presence
def test_control_characters_in_details_and_state_rejected(fresh_users):
    user1, _user2, presence = fresh_users
    with pytest.raises(InvalidActivityError):
        presence.set_activity(
            user1.id, ActivityType.PLAYING, "Game", details="line1\nline2"
        )
    with pytest.raises(InvalidActivityError):
        presence.set_activity(user1.id, ActivityType.PLAYING, "Game", state="a\x00b")


@pytest.mark.presence
def test_control_characters_in_assets_rejected(fresh_users):
    user1, _user2, presence = fresh_users
    with pytest.raises(InvalidActivityError):
        presence.set_activity(
            user1.id,
            ActivityType.PLAYING,
            "Game",
            assets={"large_text": "rank\nX-Evil: 1"},
        )


@pytest.mark.presence
def test_embedded_credentials_in_url_rejected(fresh_users):
    user1, _user2, presence = fresh_users
    with pytest.raises(InvalidActivityError):
        presence.set_activity(
            user1.id,
            ActivityType.STREAMING,
            "Stream",
            url="https://user:password@stream.example.com/live",
        )


@pytest.mark.presence
def test_non_http_scheme_in_url_rejected(fresh_users):
    user1, _user2, presence = fresh_users
    with pytest.raises(InvalidActivityError):
        presence.set_activity(
            user1.id, ActivityType.STREAMING, "Stream", url="javascript:alert(1)"
        )


@pytest.mark.presence
def test_non_integer_timestamp_rejected(fresh_users):
    user1, _user2, presence = fresh_users
    with pytest.raises(InvalidActivityError):
        presence.set_activity(
            user1.id,
            ActivityType.PLAYING,
            "Game",
            timestamps={"start": "not-an-int"},
        )


@pytest.mark.presence
def test_valid_activity_still_accepted(fresh_users):
    user1, _user2, presence = fresh_users
    result = presence.set_activity(
        user1.id,
        ActivityType.STREAMING,
        "Stream",
        url="https://stream.example.com/live",
        timestamps={"start": 1700000000000},
        assets={"large_image": "logo", "large_text": "Live now"},
    )
    assert result.activity.url == "https://stream.example.com/live"
    assert result.activity.start_timestamp == 1700000000000
