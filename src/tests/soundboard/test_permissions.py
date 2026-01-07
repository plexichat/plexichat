"""
Tests for sound permissions.
"""

import pytest
import uuid
from src.core.soundboard import SoundFormat, PermissionDeniedError


class TestSoundPermissions:
    """Tests for sound usage permissions."""

    def test_set_permissions_success(self, db_and_modules):
        """Test setting sound permissions successfully."""
        db, auth, messaging, servers, soundboard = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"perm_owner_{unique_id}",
            email=f"perm_owner_{unique_id}@example.com",
            password="TestPass123!",
        )

        server = servers.create_server(owner.id, f"Perm Server {unique_id}")

        role = servers.create_role(
            user_id=owner.id, server_id=server.id, name="Test Role"
        )

        sound = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="perm_sound",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/sounds/perm.mp3",
            size=100000,
            duration_seconds=2.0,
        )

        perm = soundboard.set_sound_permissions(
            user_id=owner.id, sound_id=sound.id, role_id=role.id, can_use=True
        )

        assert perm is not None
        assert perm.sound_id == sound.id
        assert perm.role_id == role.id
        assert perm.can_use is True

    def test_deny_permissions(self, db_and_modules):
        """Test denying sound permissions."""
        db, auth, messaging, servers, soundboard = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"deny_owner_{unique_id}",
            email=f"deny_owner_{unique_id}@example.com",
            password="TestPass123!",
        )

        server = servers.create_server(owner.id, f"Deny Server {unique_id}")

        role = servers.create_role(
            user_id=owner.id, server_id=server.id, name="Denied Role"
        )

        sound = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="deny_sound",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/sounds/deny.mp3",
            size=100000,
            duration_seconds=2.0,
        )

        perm = soundboard.set_sound_permissions(
            user_id=owner.id, sound_id=sound.id, role_id=role.id, can_use=False
        )

        assert perm.can_use is False

    def test_update_permissions(self, db_and_modules):
        """Test updating existing permissions."""
        db, auth, messaging, servers, soundboard = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"update_owner_{unique_id}",
            email=f"update_owner_{unique_id}@example.com",
            password="TestPass123!",
        )

        server = servers.create_server(owner.id, f"Update Server {unique_id}")

        role = servers.create_role(
            user_id=owner.id, server_id=server.id, name="Update Role"
        )

        sound = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="update_sound",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/sounds/update.mp3",
            size=100000,
            duration_seconds=2.0,
        )

        soundboard.set_sound_permissions(
            user_id=owner.id, sound_id=sound.id, role_id=role.id, can_use=True
        )

        updated = soundboard.set_sound_permissions(
            user_id=owner.id, sound_id=sound.id, role_id=role.id, can_use=False
        )

        assert updated.can_use is False

    def test_non_owner_cannot_set_permissions(self, db_and_modules):
        """Test non-owner cannot set permissions."""
        db, auth, messaging, servers, soundboard = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"no_perm_owner_{unique_id}",
            email=f"no_perm_owner_{unique_id}@example.com",
            password="TestPass123!",
        )
        member = auth.register(
            username=f"no_perm_member_{unique_id}",
            email=f"no_perm_member_{unique_id}@example.com",
            password="TestPass123!",
        )

        server = servers.create_server(owner.id, f"No Perm Server {unique_id}")
        servers.add_member(server.id, member.id)

        role = servers.create_role(
            user_id=owner.id, server_id=server.id, name="No Perm Role"
        )

        sound = soundboard.upload_sound(
            user_id=owner.id,
            server_id=server.id,
            name="no_perm_sound",
            format=SoundFormat.MP3,
            url="https://cdn.example.com/sounds/noperm.mp3",
            size=100000,
            duration_seconds=2.0,
        )

        with pytest.raises(PermissionDeniedError):
            soundboard.set_sound_permissions(
                user_id=member.id, sound_id=sound.id, role_id=role.id, can_use=True
            )
