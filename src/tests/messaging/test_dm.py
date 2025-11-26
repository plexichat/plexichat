"""
DM-specific tests for messaging module.
"""

import pytest


class TestDMCreation:
    """Test DM-specific creation behavior."""
    
    def test_dm_has_two_participants(self, dm_conversation):
        """Test DM always has exactly two participants."""
        dm, user1, user2, messaging = dm_conversation
        
        participants = messaging.get_participants(user1.id, dm.id)
        
        assert len(participants) == 2
    
    def test_dm_max_participants_is_two(self, dm_conversation):
        """Test DM max participants is 2."""
        dm, user1, user2, messaging = dm_conversation
        
        assert dm.max_participants == 2
    
    def test_dm_has_no_name(self, dm_conversation):
        """Test DM has no name."""
        dm, user1, user2, messaging = dm_conversation
        
        assert dm.name is None
    
    def test_dm_has_no_owner(self, dm_conversation):
        """Test DM has no owner."""
        dm, user1, user2, messaging = dm_conversation
        
        assert dm.owner_id is None
    
    def test_dm_reuse_existing(self, users):
        """Test that creating DM reuses existing conversation."""
        user1, user2, user3, messaging = users
        
        dm1 = messaging.create_dm(user1.id, user2.id)
        dm2 = messaging.create_dm(user1.id, user2.id)
        dm3 = messaging.create_dm(user2.id, user1.id)
        
        assert dm1.id == dm2.id == dm3.id
    
    def test_dm_different_pairs_different_conversations(self, users):
        """Test different user pairs create different DMs."""
        user1, user2, user3, messaging = users
        
        dm1 = messaging.create_dm(user1.id, user2.id)
        dm2 = messaging.create_dm(user1.id, user3.id)
        dm3 = messaging.create_dm(user2.id, user3.id)
        
        assert dm1.id != dm2.id
        assert dm1.id != dm3.id
        assert dm2.id != dm3.id


class TestDMSettings:
    """Test DM settings and restrictions."""
    
    def test_dm_blocked_when_dms_disabled(self, users):
        """Test DM creation blocked when recipient disables DMs."""
        user1, user2, user3, messaging = users
        
        messaging.update_user_message_settings(user2.id, allow_dms_from="none")
        
        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.create_dm(user1.id, user2.id)
    
    def test_dm_allowed_when_dms_enabled(self, users):
        """Test DM creation allowed when recipient enables DMs."""
        user1, user2, user3, messaging = users
        
        messaging.update_user_message_settings(user2.id, allow_dms_from="everyone")
        
        dm = messaging.create_dm(user1.id, user2.id)
        
        assert dm is not None
    
    def test_cannot_add_participant_to_dm(self, dm_conversation, users):
        """Test cannot add third participant to DM."""
        dm, user1, user2, messaging = dm_conversation
        _, _, user3, _ = users
        
        with pytest.raises(messaging.ConversationTypeError):
            messaging.add_participant(user1.id, dm.id, user3.id)
    
    def test_cannot_remove_participant_from_dm(self, dm_conversation):
        """Test cannot remove participant from DM."""
        dm, user1, user2, messaging = dm_conversation
        
        with pytest.raises(messaging.ConversationTypeError):
            messaging.remove_participant(user1.id, dm.id, user2.id)
    
    def test_cannot_update_dm_name(self, dm_conversation):
        """Test cannot update DM name."""
        dm, user1, user2, messaging = dm_conversation
        
        with pytest.raises(messaging.ConversationTypeError):
            messaging.update_conversation(user1.id, dm.id, name="New Name")
    
    def test_cannot_change_roles_in_dm(self, dm_conversation):
        """Test cannot change roles in DM."""
        dm, user1, user2, messaging = dm_conversation
        
        with pytest.raises(messaging.ConversationTypeError):
            messaging.update_participant_role(user1.id, dm.id, user2.id, messaging.ParticipantRole.ADMIN)


class TestDMMessaging:
    """Test messaging in DMs."""
    
    def test_both_users_can_send(self, dm_conversation):
        """Test both users can send messages."""
        dm, user1, user2, messaging = dm_conversation
        
        msg1 = messaging.send_message(user1.id, dm.id, "From user1")
        msg2 = messaging.send_message(user2.id, dm.id, "From user2")
        
        assert msg1 is not None
        assert msg2 is not None
    
    def test_both_users_can_read(self, dm_conversation):
        """Test both users can read messages."""
        dm, user1, user2, messaging = dm_conversation
        
        messaging.send_message(user1.id, dm.id, "Test message")
        
        msgs1 = messaging.get_messages(user1.id, dm.id)
        msgs2 = messaging.get_messages(user2.id, dm.id)
        
        assert len(msgs1) > 0
        assert len(msgs2) > 0
    
    def test_third_party_cannot_read(self, dm_conversation, users):
        """Test third party cannot read DM messages."""
        dm, user1, user2, messaging = dm_conversation
        _, _, user3, _ = users
        
        messaging.send_message(user1.id, dm.id, "Private message")
        
        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.get_messages(user3.id, dm.id)


class TestDMDeletion:
    """Test DM deletion behavior."""
    
    def test_either_user_can_delete_dm(self, users):
        """Test either user can delete DM."""
        user1, user2, user3, messaging = users
        
        dm = messaging.create_dm(user1.id, user2.id)
        
        result = messaging.delete_conversation(user2.id, dm.id)
        
        assert result is True
    
    def test_leaving_dm_deletes_it(self, users):
        """Test leaving DM deletes the conversation."""
        user1, user2, user3, messaging = users
        
        dm = messaging.create_dm(user1.id, user2.id)
        
        messaging.leave_conversation(user1.id, dm.id)
        
        # Both users should no longer see it
        assert messaging.get_conversation(dm.id, user1.id) is None
        assert messaging.get_conversation(dm.id, user2.id) is None


class TestDMAutoCreate:
    """Test DM auto-creation behavior."""
    
    def test_auto_create_enabled_by_default(self, users):
        """Test auto-create is enabled by default."""
        user1, user2, user3, messaging = users
        
        settings = messaging.get_user_message_settings(user1.id)
        
        assert settings.auto_create_dms is True
    
    def test_auto_create_can_be_disabled(self, users):
        """Test auto-create can be disabled."""
        user1, user2, user3, messaging = users
        
        messaging.update_user_message_settings(user1.id, auto_create_dms=False)
        
        settings = messaging.get_user_message_settings(user1.id)
        
        assert settings.auto_create_dms is False
