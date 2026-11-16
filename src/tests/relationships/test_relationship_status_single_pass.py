"""``get_relationship`` must read each candidate row once, not probe then refetch."""

import pytest

from src.core.relationships.models import RelationshipStatus


@pytest.mark.relationships
class TestGetRelationshipSinglePass:
    def test_blocked_branch_skips_existence_probe(
        self, rel_manager, two_users, monkeypatch
    ):
        user1, user2 = two_users
        rel_manager.block_user(user1.id, user2.id)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("redundant existence probe was called")

        monkeypatch.setattr(rel_manager, "_is_blocked", forbidden)

        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.BLOCKED
        assert rel.created_at > 0

    def test_friend_branch_skips_existence_probe(
        self, rel_manager, two_users, monkeypatch
    ):
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, req.id)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("redundant existence probe was called")

        monkeypatch.setattr(rel_manager, "_are_friends", forbidden)

        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.FRIEND
        assert rel.created_at > 0
