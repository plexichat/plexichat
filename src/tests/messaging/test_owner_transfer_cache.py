"""Regression tests: owner promotion invalidates the conversation cache.

M14 (audit): when a conversation owner leaves, the service promotes the
next owner via ``_repo.update_owner`` but the original code never
invalidated the ``conv_data:*int:<conversation_id>:*`` cache pattern used
by every other mutating path (update/delete). Readers holding a cached
conversation row kept seeing the stale previous owner until TTL expiry.

The fix calls ``invalidate_pattern`` right after the promotion. These
tests pin that contract by intercepting ``invalidate_pattern`` in the
service module namespace.
"""

import src.core.messaging.services.conversation as conv_module
from src.core.messaging.services.conversation import ConversationService


class _RecordingInvalidator:
    def __init__(self):
        self.patterns = []

    def __call__(self, pattern: str) -> int:
        self.patterns.append(pattern)
        return 0


def test_owner_promotion_invalidates_conversation_cache(
    monkeypatch, messaging_manager, two_users
):
    user1, user2 = two_users
    conv = messaging_manager.create_group(
        owner_id=user1.id,
        name="transfer test",
        participant_ids=[user2.id],
    )

    service = messaging_manager._conversation_svc
    assert isinstance(service, ConversationService)

    recorder = _RecordingInvalidator()
    monkeypatch.setattr(conv_module, "invalidate_pattern", recorder)

    # Owner leaves; next owner (user2) is promoted.
    service.leave_conversation(user1.id, conv.id)

    assert any(p == f"conv_data:*int:{conv.id}:*" for p in recorder.patterns), (
        "owner promotion must invalidate conv_data:*int:<conversation_id>:* "
        f"(saw: {recorder.patterns})"
    )

    # And the DB actually shows the new owner.
    row = service._repo.get_by_id(conv.id)
    assert row is not None
    assert int(row["owner_id"]) == int(user2.id)
