"""Regression tests: notification previews cannot carry control characters,
and the post-insert read-back is owner-scoped.

Previews are copied verbatim into websocket events, push payloads, log lines
and the HTTP response. C0/C1 controls and DEL allow log/terminal injection;
bidi overrides and zero-width format characters allow visual spoofing of a
link or the apparent direction of the text. The manager must sanitize them on
both write and read. The read-back in ``_create_notification`` must also carry
the owner predicate: an unscoped lookup is an IDOR primitive that can hand a
generated-id collision row (owned by someone else) to the caller.
"""

import pytest

from src.core.notifications.manager import creator as creator_module
from src.core.notifications.manager.creator import NotificationCreatorMixin
from src.core.notifications.manager.helpers import (
    row_to_notification,
    sanitize_notification_content,
    truncate_content,
)
from src.core.notifications.models import MentionType, Notification

pytestmark = [pytest.mark.unit, pytest.mark.security]

OWNER = 100
ATTACKER = 200

# C0 controls, DEL, C1 NEL, RLO (visual reversal), ZWSP and BOM.
DIRTY = "hello\x00\x1b[31m\r\n\x7f\x85world\u202e\u200b\ufeff"


def _clean(text):
    """The set of characters the sanitizer must never leave in a preview."""
    return [
        c
        for c in text
        if ord(c) < 0x20
        or ord(c) == 0x7F
        or 0x80 <= ord(c) <= 0x9F
        or c in "\u200b\u200c\u200d\u200e\u200f\u2028\u2029\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff\u061c\u00ad"
    ]


class TestSanitizeNotificationContent:
    def test_strips_all_control_and_format_characters(self):
        cleaned = sanitize_notification_content(DIRTY)
        assert _clean(cleaned) == []
        assert "hello" in cleaned and "world" in cleaned
        # Adjacent words must not be glued together by removal.
        assert "\x00" not in cleaned

    def test_preserves_ordinary_and_non_latin_text(self):
        assert sanitize_notification_content("hello world") == "hello world"
        assert sanitize_notification_content("مرحبا bye") == "مرحبا bye"
        assert sanitize_notification_content("") == ""
        assert sanitize_notification_content(None) == ""

    def test_truncate_sanitizes_before_cutting(self):
        preview = truncate_content(DIRTY, {"content_preview_length": 100})
        assert _clean(preview) == []
        assert "hello" in preview


class TestRowToNotificationSanitizes:
    def test_legacy_plaintext_preview_is_sanitized(self):
        row = {
            "id": 1,
            "user_id": OWNER,
            "sender_id": 7,
            "message_id": 500,
            "conversation_id": 600,
            "server_id": None,
            "channel_id": None,
            "thread_id": None,
            "mention_type": "user",
            "content_preview": DIRTY,
            "content_preview_encrypted": None,
            "read": 0,
            "created_at": 1,
        }
        notification = row_to_notification(row)
        assert _clean(notification.content_preview) == []
        assert "hello" in notification.content_preview


class _DbStub:
    def __init__(self):
        self.inserts = []

    def execute(self, sql, params=()):
        self.inserts.append((sql, tuple(params)))


class _Creator(NotificationCreatorMixin):
    """Minimal creator with a colliding row owned by ``ATTACKER``.

    ``get_notification`` returns that foreign row only for an unscoped lookup,
    exactly as a real DB would when a generated id collides with an existing
    row. A scoped lookup must refuse it.
    """

    def __init__(self, collide=True):
        self._db = _DbStub()
        self.collide = collide
        self.lookups = []
        self.events = []
        self.pushes = []

    def _generate_id(self):
        return 999

    def get_notification(self, notification_id, user_id=None):
        self.lookups.append((notification_id, user_id))
        if self.collide and user_id is None:
            return Notification(
                id=notification_id,
                user_id=ATTACKER,
                author_id=7,
                message_id=500,
                conversation_id=600,
                content_preview="another user's private body",
            )
        return None

    def _dispatch_notification_event(self, user_id, event_type, data):
        self.events.append((user_id, event_type, data))

    def _queue_push_delivery(self, notifications):
        self.pushes.append(list(notifications))


def _call_create(creator):
    return creator._create_notification(
        user_id=OWNER,
        author_id=7,
        message_id=500,
        conversation_id=600,
        server_id=None,
        channel_id=None,
        thread_id=None,
        mention_type=MentionType.USER,
        content_preview="hello world",
        created_at=1,
    )


class TestOwnerScopedReadBack:
    def test_read_back_carries_the_owner_predicate(self, monkeypatch):
        monkeypatch.setattr(creator_module, "_encrypt_content_preview", lambda *a: None)
        creator = _Creator(collide=True)

        result = _call_create(creator)

        assert creator.lookups == [(999, OWNER)]
        # The colliding row belongs to someone else, so no notification is
        # returned and no foreign content is dispatched or pushed.
        assert result is None
        assert creator.events == []
        assert creator.pushes == []

    def test_owner_can_still_read_their_own_notification(self, monkeypatch):
        monkeypatch.setattr(creator_module, "_encrypt_content_preview", lambda *a: None)
        creator = _Creator(collide=False)
        creator.get_notification = lambda nid, user_id=None: Notification(
            id=nid,
            user_id=user_id or OWNER,
            author_id=7,
            message_id=500,
            conversation_id=600,
            mention_type=MentionType.USER,
            content_preview="hello world",
        )

        result = _call_create(creator)

        assert result is not None
        assert len(creator.events) == 1
        assert creator.events[0][0] == OWNER
