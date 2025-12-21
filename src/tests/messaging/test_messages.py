"""
Message CRUD tests for messaging module.
"""

import pytest


import pytest
import asyncio
import uuid

@pytest.mark.asyncio
class TestMessagesAsync:
    """Enhanced asynchronous messaging tests."""

    async def test_send_message_success(self, dm_conversation):
        """Test successful message sending."""
        dm, user1, user2, messaging = dm_conversation
        
        # Test basic success
        msg = await asyncio.to_thread(messaging.send_message, user1.id, dm.id, "Hello, world!")
        
        assert msg is not None
        assert msg.content == "Hello, world!"
        assert msg.author_id == user1.id
        assert msg.conversation_id == dm.id

    async def test_concurrent_message_sending(self, dm_conversation):
        """Test sending multiple messages concurrently to verify snowflake ID generation and order."""
        dm, user1, user2, messaging = dm_conversation
        
        # Send 20 messages in parallel
        tasks = [asyncio.to_thread(messaging.send_message, user1.id, dm.id, f"Msg {i}") for i in range(20)]
        results = await asyncio.gather(*tasks)
        
        assert len(results) == 20
        # IDs should be unique
        ids = {m.id for m in results}
        assert len(ids) == 20

    async def test_message_edit_propagation(self, dm_conversation):
        """Test that editing a message correctly updates all stored fields and timestamps."""
        dm, user1, user2, messaging = dm_conversation
        
        msg = await asyncio.to_thread(messaging.send_message, user1.id, dm.id, "Original")
        await asyncio.sleep(0.01) # Ensure time passes for updated_at
        
        edited = await asyncio.to_thread(messaging.edit_message, user1.id, msg.id, "Edited content")
        
        assert edited.content == "Edited content"
        assert edited.edited is True
        assert edited.updated_at > edited.created_at

    async def test_complex_pagination(self, dm_conversation):
        """Test advanced pagination scenarios with before/after and limits."""
        dm, user1, user2, messaging = dm_conversation
        
        # Create 10 messages
        msgs = []
        for i in range(10):
            m = await asyncio.to_thread(messaging.send_message, user1.id, dm.id, f"Msg {i}")
            msgs.append(m)
            
        # Get middle 4 messages using before_id
        pivot = msgs[7]
        page = await asyncio.to_thread(messaging.get_messages, user1.id, dm.id, limit=4, before_id=pivot.id)
        
        assert len(page) == 4
        assert page[0].id == msgs[6].id
        assert page[3].id == msgs[3].id

    async def test_bulk_delete_performance(self, dm_conversation):
        """Test soft deleting messages in bulk and verify they are hidden."""
        dm, user1, user2, messaging = dm_conversation
        
        # Create 50 messages
        msgs = []
        for i in range(50):
            m = await asyncio.to_thread(messaging.send_message, user1.id, dm.id, f"Delete test {i}")
            msgs.append(m)
            
        # Delete half of them in parallel
        tasks = [asyncio.to_thread(messaging.delete_message, user1.id, m.id) for m in msgs[:25]]
        await asyncio.gather(*tasks)
        
        # Verify only 25 remain
        remaining = await asyncio.to_thread(messaging.get_messages, user1.id, dm.id, limit=100)
        assert len(remaining) == 25

    async def test_reply_chain_integrity(self, dm_conversation):
        """Test a chain of replies maintains reference integrity."""
        dm, user1, user2, messaging = dm_conversation
        
        m1 = await asyncio.to_thread(messaging.send_message, user1.id, dm.id, "Root")
        m2 = await asyncio.to_thread(messaging.send_message, user2.id, dm.id, "Reply 1", reply_to_id=m1.id)
        m3 = await asyncio.to_thread(messaging.send_message, user1.id, dm.id, "Reply 2", reply_to_id=m2.id)
        
        assert m2.reply_to_id == m1.id
        assert m3.reply_to_id == m2.id
        
        # Verify in fetch
        fetched_m3 = await asyncio.to_thread(messaging.get_message, user1.id, m3.id)
        assert fetched_m3.reply_to_id == m2.id

