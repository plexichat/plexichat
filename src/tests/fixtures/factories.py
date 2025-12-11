"""
Factory fixtures for creating test entities.

Provides efficient creation of users, servers, channels, etc.
with optional pooling for frequently-used entities.
"""

import uuid
from typing import Any, List, Optional
from dataclasses import dataclass, field

from .config import TEST_PASSWORD


@dataclass
class UserFactory:
    """
    Factory for creating test users.
    
    Supports both pooled users (pre-created, fast) and
    fresh users (created on demand, slower but isolated).
    """

    auth_module: Any
    _pool: List[Any] = field(default_factory=list)
    _pool_index: int = 0
    _created: List[Any] = field(default_factory=list)

    def create(
        self,
        username: Optional[str] = None,
        email: Optional[str] = None,
        password: str = TEST_PASSWORD,
        use_pool: bool = True,
        **kwargs
    ) -> Any:
        """
        Create or retrieve a test user.
        
        Args:
            username: Optional username (auto-generated if not provided)
            email: Optional email (auto-generated if not provided)
            password: Password for the user
            use_pool: If True and no custom username, return from pool
            **kwargs: Additional arguments for registration
            
        Returns:
            User object
        """
        # If no custom username and pool available, use pool
        if username is None and use_pool and self._pool_index < len(self._pool):
            user = self._pool[self._pool_index]
            self._pool_index += 1
            return user

        # Create new user
        unique_id = uuid.uuid4().hex[:8]
        username = username or f"user_{unique_id}"
        email = email or f"{username}@test.example.com"

        user = self.auth_module.register(
            username=username,
            email=email,
            password=password,
            **kwargs
        )
        self._created.append(user)
        return user

    def create_many(self, count: int, **kwargs) -> List[Any]:
        """Create multiple users."""
        return [self.create(**kwargs) for _ in range(count)]

    def create_with_login(
        self,
        username: Optional[str] = None,
        password: str = TEST_PASSWORD,
        **kwargs
    ) -> tuple:
        """
        Create a user and log them in.
        
        Returns:
            Tuple of (user, token)
        """
        user = self.create(username=username, password=password, use_pool=False, **kwargs)
        result = self.auth_module.login(user.username, password)
        return user, result.token

    def populate_pool(self, size: int = 50):
        """
        Pre-populate the user pool.
        
        Call this at session start for faster user creation.
        """
        for i in range(size):
            user = self.auth_module.register(
                username=f"pooluser_{i}_{uuid.uuid4().hex[:4]}",
                email=f"pooluser_{i}_{uuid.uuid4().hex[:4]}@test.example.com",
                password=TEST_PASSWORD
            )
            self._pool.append(user)

    def reset_pool_index(self):
        """Reset pool index to reuse pooled users."""
        self._pool_index = 0


@dataclass
class ServerFactory:
    """Factory for creating test servers."""

    servers_module: Any
    user_factory: UserFactory

    def create(
        self,
        owner: Optional[Any] = None,
        name: Optional[str] = None,
        description: str = "Test server",
        **kwargs
    ) -> Any:
        """
        Create a test server.
        
        Args:
            owner: Owner user (created if not provided)
            name: Server name (auto-generated if not provided)
            description: Server description
            **kwargs: Additional arguments
            
        Returns:
            Server object
        """
        if owner is None:
            owner = self.user_factory.create()

        assert owner is not None  # Created above if None
        unique_id = uuid.uuid4().hex[:6]
        name = name or f"Test Server {unique_id}"

        return self.servers_module.create_server(
            owner_id=owner.id,
            name=name,
            description=description,
            **kwargs
        )

    def create_with_members(
        self,
        owner: Optional[Any] = None,
        member_count: int = 2,
        **kwargs
    ) -> tuple:
        """
        Create a server with members.
        
        Returns:
            Tuple of (server, owner, [members])
        """
        if owner is None:
            owner = self.user_factory.create()

        server = self.create(owner=owner, **kwargs)

        members = []
        for _ in range(member_count):
            member = self.user_factory.create()
            self.servers_module.add_member(server.id, member.id)
            members.append(member)

        return server, owner, members

    def create_with_channels(
        self,
        owner: Optional[Any] = None,
        channel_names: Optional[List[str]] = None,
        **kwargs
    ) -> tuple:
        """
        Create a server with additional channels.
        
        Returns:
            Tuple of (server, owner, [channels])
        """
        if owner is None:
            owner = self.user_factory.create()

        assert owner is not None  # Created above if None
        server = self.create(owner=owner, **kwargs)

        # Get default general channel
        channels = self.servers_module.get_channels(owner.id, server.id)

        # Create additional channels
        channel_names = channel_names or ["announcements", "random"]
        for name in channel_names:
            channel = self.servers_module.create_channel(
                user_id=owner.id,
                server_id=server.id,
                name=name
            )
            channels.append(channel)

        return server, owner, channels


@dataclass
class ConversationFactory:
    """Factory for creating test conversations (DMs and groups)."""

    messaging_module: Any
    user_factory: UserFactory

    def create_dm(
        self,
        user1: Optional[Any] = None,
        user2: Optional[Any] = None,
    ) -> tuple:
        """
        Create a DM conversation.
        
        Returns:
            Tuple of (conversation, user1, user2)
        """
        if user1 is None:
            user1 = self.user_factory.create()
        if user2 is None:
            user2 = self.user_factory.create()

        assert user1 is not None
        assert user2 is not None
        dm = self.messaging_module.create_dm(user1.id, user2.id)
        return dm, user1, user2

    def create_group(
        self,
        owner: Optional[Any] = None,
        participant_count: int = 2,
        name: Optional[str] = None,
    ) -> tuple:
        """
        Create a group conversation.
        
        Returns:
            Tuple of (group, owner, [participants])
        """
        if owner is None:
            owner = self.user_factory.create()

        assert owner is not None
        participants = self.user_factory.create_many(participant_count)
        participant_ids = [p.id for p in participants]

        unique_id = uuid.uuid4().hex[:6]
        name = name or f"Test Group {unique_id}"

        group = self.messaging_module.create_group(
            owner_id=owner.id,
            name=name,
            participant_ids=participant_ids
        )

        return group, owner, participants


@dataclass
class WebhookFactory:
    """Factory for creating test webhooks."""

    webhooks_module: Any
    server_factory: ServerFactory

    def create(
        self,
        user: Optional[Any] = None,
        channel: Optional[Any] = None,
        name: Optional[str] = None,
        **kwargs
    ) -> tuple:
        """
        Create a test webhook.
        
        Returns:
            Tuple of (webhook, channel, server, owner)
        """
        if channel is None:
            server, owner, channels = self.server_factory.create_with_channels()
            channel = channels[0]
            user = owner
        else:
            server = None
            owner = user

        assert user is not None
        assert channel is not None
        unique_id = uuid.uuid4().hex[:6]
        name = name or f"Test Webhook {unique_id}"

        webhook = self.webhooks_module.create_webhook(
            user_id=user.id,
            channel_id=channel.id,
            name=name,
            **kwargs
        )

        return webhook, channel, server, owner
