"""Regression: invisible Unicode format characters cannot survive into a
notification preview.

Previews are copied verbatim into push bodies, websocket events, log lines and
the HTTP feed. C0/C1 controls are stripped, but the old sanitizer stopped at
U+2069 and therefore let the word joiner (U+2060), the legacy directional
formats (U+206A-U+206F) and the Syriac abbreviation mark (U+070F) through.
Those are invisible Cf characters an attacker can smuggle into a message to
glue words together or change how a link renders on a device. The shared
``hardening.text.CONTROL_CHARACTERS`` rule already rejects all of them; this
test closes the differential for the notification boundary.
"""

import pytest

from src.core.notifications.manager.helpers import (
    row_to_notification,
    sanitize_notification_content,
    truncate_content,
)
from src.core.security.hardening.text import CONTROL_CHARACTERS

pytestmark = [pytest.mark.unit, pytest.mark.security]

# Rejected by the shared control-character policy, but survived the old
# notification sanitizer.
_GAP_CHARS = "\u2060\u206a\u206b\u206c\u206d\u206e\u206f\u070f"


@pytest.mark.parametrize("char", list(_GAP_CHARS))
def test_sanitizer_strips_chars_the_shared_policy_rejects(char):
    # The shared rule treats this character as a spoofing vector.
    assert CONTROL_CHARACTERS.search(char)

    cleaned = sanitize_notification_content("click" + char + "here")

    assert char not in cleaned
    # Replaced with a space, not deleted, so words do not merge.
    assert cleaned == "click here"


def test_truncate_content_strips_gap_chars_before_cutting():
    preview = truncate_content(
        "hello" + _GAP_CHARS + "world", {"content_preview_length": 100}
    )

    assert not CONTROL_CHARACTERS.search(preview)
    assert "hello" in preview and "world" in preview


def test_row_to_notification_sanitizes_legacy_plaintext_preview():
    row = {
        "id": 1,
        "user_id": 2,
        "sender_id": 3,
        "message_id": 4,
        "conversation_id": 5,
        "server_id": None,
        "channel_id": None,
        "thread_id": None,
        "mention_type": "user",
        "content_preview": "a\u2060b\u206fc\u070fd\x00e",
        "content_preview_encrypted": None,
        "read": 0,
        "created_at": 1,
    }

    preview = row_to_notification(row).content_preview

    assert not CONTROL_CHARACTERS.search(preview)
    assert "\x00" not in preview
    for word in ("a", "b", "c", "d", "e"):
        assert word in preview


def test_push_body_is_free_of_invisible_format_chars():
    from src.core.notifications.manager.push import PushMixin
    from src.core.notifications.models import MentionType, Notification

    notif = Notification(
        id=1,
        user_id=2,
        author_id=3,
        message_id=4,
        conversation_id=5,
        mention_type=MentionType.USER,
        content_preview=sanitize_notification_content("see\u2060this\u070f link"),
    )
    payload = PushMixin._build_push_payload(
        object(), notif, sender_name="alice", badge_count=1
    )

    assert not CONTROL_CHARACTERS.search(payload.body)
    assert "see" in payload.body and "link" in payload.body
