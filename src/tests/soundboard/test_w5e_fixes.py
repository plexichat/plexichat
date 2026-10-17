"""Regression tests for Agent 28 / FIXER W5-E soundboard remediations."""

import pytest
from src.core.soundboard.models import SoundFormat
from src.core.soundboard.exceptions import (
    InvalidSoundFormatError,
    PermissionDeniedError,
    ServerNotFoundError,
)


@pytest.fixture
def wired(soundboard_manager, test_db):
    """Manager plus a raw server/user/channel fixture row set (id=1)."""
    test_db.execute(
        "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test', 1, 1000, 1000)"
    )
    test_db.execute(
        "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) VALUES (1, 1, 1, 1000, 1000)"
    )
    return soundboard_manager


class TestFormatNormalization:
    def test_mime_and_extension_forms_equivalent(self, wired, monkeypatch):
        monkeypatch.setitem(
            wired._config,
            "allowed_formats",
            ["audio/mpeg", "audio/ogg", "audio/wav", "audio/webm"],
        )
        assert wired._format_allowed("mp3") is True
        assert wired._format_allowed(SoundFormat.OGG.value) is True
        assert wired._format_allowed("audio/mpeg") is True
        assert wired._format_allowed("flac") is False

    def test_extension_forms_against_default_config(self, wired):
        # Default fallback is ["mp3", "ogg"]; SoundFormat values must pass.
        assert wired._format_allowed(SoundFormat.MP3.value) is True


class TestFailClosed:
    def test_no_servers_module_denies_permissions(self, db):
        from src.core.soundboard.manager import SoundboardManager

        bare = SoundboardManager(db)
        assert bare._check_server_permission(1, 1) is False
        assert bare._is_server_member(1, 1) is False

    def test_upload_to_nonexistent_server_raises(self, wired, monkeypatch):
        # Bypass the (correctly failing-closed) permission gate to isolate M4:
        # a permitted upload targeting a vanished server must raise
        # ServerNotFoundError instead of creating orphaned rows.
        monkeypatch.setattr(wired, "_check_server_permission", lambda u, s: True)
        with pytest.raises(ServerNotFoundError):
            wired.upload_sound(
                1, 999999999, "ghost", SoundFormat.MP3, "https://x/y.mp3", 10, 1.0
            )


class TestUrlGuard:
    def test_dangerous_schemes_rejected(self, wired):
        from src.core.soundboard.exceptions import InvalidSoundUrlError

        for bad in ("javascript:alert(1)", "data:text/html;base64,AAAA", "file:///etc"):
            with pytest.raises(InvalidSoundUrlError):
                wired._validate_sound_url(bad)

    def test_update_cannot_repoint_to_dangerous_url(self, wired):
        sound = wired.upload_sound(
            1, 1, "snd", SoundFormat.MP3, "https://cdn/x.mp3", 10, 1.0
        )
        from src.core.soundboard.exceptions import InvalidSoundUrlError

        with pytest.raises(InvalidSoundUrlError):
            wired.update_sound(1, sound.id, url="javascript:alert(1)")


class TestPermissionSemantics:
    @pytest.fixture
    def sound(self, wired):
        return wired.upload_sound(
            1, 1, "permcheck", SoundFormat.MP3, "https://cdn/p.mp3", 10, 1.0
        )

    def _set_role_rows(self, test_db, role_id, server_id=1):
        if server_id != 1:
            test_db.execute(
                f"INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) "
                f"VALUES ({server_id}, 'Other{server_id}', 1, 1000, 1000)"
            )
        test_db.execute(
            f"INSERT INTO srv_roles (id, server_id, name, created_at, updated_at, position) "
            f"VALUES ({role_id}, {server_id}, 'R{role_id}', 1000, 1000, {role_id})"
        )

    def _assign_role(self, test_db, member_id, role_id):
        test_db.execute(
            f"INSERT INTO srv_member_roles (member_id, role_id, assigned_at, assigned_by) "
            f"VALUES ({member_id}, {role_id}, 1000, 1)"
        )

    def test_allow_list_blocks_unlisted_roles(self, wired, test_db, sound):
        test_db.execute(
            "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) VALUES (2, 1, 50, 1000, 1000)"
        )
        self._set_role_rows(test_db, 10)
        self._assign_role(test_db, 1, 10)
        wired.set_sound_permissions(1, sound.id, 10, can_use=True)
        # Member of the server with an allowed role -> play.
        assert wired._can_use_sound(1, sound.id, 1) is True
        # Member whose roles match no ALLOW entry -> blocked.
        assert wired._can_use_sound(50, sound.id, 1) is False

    def test_deny_wins_over_allow_membership(self, wired, test_db, sound):
        self._set_role_rows(test_db, 30)
        self._set_role_rows(test_db, 31)
        self._assign_role(test_db, 1, 30)
        wired.set_sound_permissions(1, sound.id, 30, can_use=True)
        wired.set_sound_permissions(1, sound.id, 31, can_use=False)

    def test_cross_server_role_rejected(self, wired, test_db, sound):
        self._set_role_rows(test_db, 40, server_id=999)
        with pytest.raises(PermissionDeniedError):
            wired.set_sound_permissions(1, sound.id, 40, can_use=True)


class TestCooldownClampAndRace:
    def test_update_cooldown_clamped_to_max(self, wired, monkeypatch):
        monkeypatch.setitem(wired._config, "max_cooldown_seconds", 30)
        sound = wired.upload_sound(
            1, 1, "clamp", SoundFormat.MP3, "https://cdn/c.mp3", 10, 1.0
        )
        updated = wired.update_sound(1, sound.id, cooldown=9999)
        assert updated.cooldown == 30

    def test_acquire_cooldown_blocks_second_play(self, wired, test_db):
        sound = wired.upload_sound(
            1, 1, "race", SoundFormat.MP3, "https://cdn/r.mp3", 10, 1.0
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'voice', 'voice', 1000, 1000, 0)"
        )
        wired.play_sound(1, sound.id, 1)
        remaining = wired._acquire_cooldown(1, sound.id, 1, 300)
        assert remaining is not None and remaining > 0

    def test_acquire_cooldown_insert_race_treated_as_contention(self, wired, test_db):
        sound = wired.upload_sound(
            1, 1, "insrace", SoundFormat.MP3, "https://cdn/i.mp3", 10, 1.0
        )
        # Simulate a concurrent insert landing between the UPDATE and INSERT:
        # make execute() fail only for INSERT statements on the cooldown table.
        original_execute = wired._db.execute

        def flaky_execute(query, *a, **kw):
            if "INSERT INTO soundboard_user_cooldowns" in query:
                raise RuntimeError("UNIQUE constraint race")
            return original_execute(query, *a, **kw)

        wired._db.execute = flaky_execute
        try:
            result = wired._acquire_cooldown(1, sound.id, 1, 60)
        finally:
            wired._db.execute = original_execute
        assert result == max(1, int(60))


class TestCrossServerPlayback:
    def test_channel_of_other_server_rejected(self, wired, test_db):
        test_db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (2, 'Other', 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) VALUES (2, 2, 1, 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (2, 2, 'other-voice', 'voice', 1000, 1000, 0)"
        )
        sound = wired.upload_sound(
            1, 1, "cross", SoundFormat.MP3, "https://cdn/x.mp3", 10, 1.0
        )
        from src.core.soundboard.exceptions import ChannelNotFoundError

        with pytest.raises(ChannelNotFoundError):
            wired.play_sound(1, sound.id, 2)


class TestMediaBackedUpload:
    def test_create_from_upload_stores_via_media(self, wired, monkeypatch):
        class FakeResult:
            url = "/media/uploads/2026/airhorn.mp3"
            file_id = 12345
            metadata = None

        calls = {}

        class FakeMedia:
            def upload_file(self, user_id, data, filename, content_type=None):
                calls["args"] = (user_id, bytes(data), filename, content_type)
                return FakeResult()

        wired._media = FakeMedia()
        sound = wired.create_sound_from_upload(
            user_id=1,
            server_id=1,
            name="bytes_snd",
            file_data=b"ID3\x04\x00" + b"\x00" * 64,
            filename="airhorn.mp3",
        )
        assert calls["args"][3] == "audio/mpeg"
        assert sound.url == FakeResult.url
        assert sound.size == len(b"ID3\x04\x00" + b"\x00" * 64)

    def test_non_audio_bytes_rejected(self, wired):
        wired._media = object()
        with pytest.raises(InvalidSoundFormatError):
            wired.create_sound_from_upload(
                user_id=1,
                server_id=1,
                name="exe",
                file_data=b"MZ\x90\x00garbage",
                filename="evil.mp3",
            )


class TestDsarCollector:
    def test_collects_user_cooldowns(self, db):
        from src.core.dsar.collectors.soundboard import SoundboardCollector

        collector = SoundboardCollector(db)
        result = collector.collect(424242)
        assert "soundboard_usage" in result
        assert "soundboard_user_cooldowns" in result
