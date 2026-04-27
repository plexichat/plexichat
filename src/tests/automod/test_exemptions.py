"""Tests for automod exemptions."""

import pytest

from src.core.automod.models import RuleType
from src.core.automod.exceptions import ExemptionError


@pytest.mark.automod
class TestExemptions:
    """Tests for automod exemption management."""

    def test_add_channel_exemption(self, automod_manager, test_server):
        """Test adding a channel exemption."""
        server, owner = test_server
        channel = automod_manager._db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ? LIMIT 1", (server.id,)
        )
        channel_id = channel["id"] if channel else 0

        exemption = automod_manager.add_exemption(
            user_id=owner.id,
            server_id=server.id,
            target_type="channel",
            target_id=channel_id,
        )
        assert exemption.target_type == "channel"
        assert exemption.target_id == channel_id
        assert exemption.server_id == server.id

    def test_add_role_exemption(self, automod_manager, test_server):
        """Test adding a role exemption."""
        server, owner = test_server
        role_id = 12345
        exemption = automod_manager.add_exemption(
            user_id=owner.id,
            server_id=server.id,
            target_type="role",
            target_id=role_id,
        )
        assert exemption.target_type == "role"
        assert exemption.target_id == role_id

    def test_invalid_target_type_rejected(self, automod_manager, test_server):
        """Test that invalid target type is rejected."""
        server, owner = test_server
        with pytest.raises(ExemptionError):
            automod_manager.add_exemption(
                user_id=owner.id,
                server_id=server.id,
                target_type="user",
                target_id=99999,
            )

    def test_duplicate_exemption_rejected(self, automod_manager, test_server):
        """Test that duplicate exemptions are rejected."""
        server, owner = test_server
        automod_manager.add_exemption(
            user_id=owner.id,
            server_id=server.id,
            target_type="role",
            target_id=55555,
        )
        with pytest.raises(ExemptionError):
            automod_manager.add_exemption(
                user_id=owner.id,
                server_id=server.id,
                target_type="role",
                target_id=55555,
            )

    def test_remove_exemption(self, automod_manager, test_server):
        """Test removing an exemption."""
        server, owner = test_server
        exemption = automod_manager.add_exemption(
            user_id=owner.id,
            server_id=server.id,
            target_type="role",
            target_id=77777,
        )
        result = automod_manager.remove_exemption(owner.id, exemption.id)
        assert result is True

    def test_remove_nonexistent_exemption(self, automod_manager, test_server):
        """Test removing nonexistent exemption raises error."""
        server, owner = test_server
        with pytest.raises(ExemptionError):
            automod_manager.remove_exemption(owner.id, 9999999)
