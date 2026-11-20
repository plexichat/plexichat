"""Regression: non-finite volume overrides must fail safe at the manager layer.

The route schemas constrain ``volume`` to [0, 1] but direct manager callers
(other modules, tests, migrations) bypass them, and ``float`` accepts
``nan``/``inf``. The old ``max(0.0, min(1.0, float(v)))`` stored NaN as 1.0.
"""

import pytest


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_update_sound_non_finite_volume_fails_safe(server_with_sound, bad):
    owner, server, sound, soundboard, servers = server_with_sound

    updated = soundboard.update_sound(owner.id, sound.id, volume=bad)

    assert updated.volume == 0.0
    again = soundboard.get_sound(sound.id, owner.id)
    assert again.volume == 0.0


def test_update_sound_finite_volume_still_applied(server_with_sound):
    owner, server, sound, soundboard, servers = server_with_sound

    updated = soundboard.update_sound(owner.id, sound.id, volume=0.4)

    assert updated.volume == 0.4


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_upload_sound_non_finite_duration_rejected(server_with_owner, bad):
    """A NaN duration must not be persisted and break listing responses."""
    import uuid

    from src.core.soundboard import SoundFormat
    from src.core.soundboard.exceptions import SoundTooLongError

    owner, server, soundboard, servers = server_with_owner

    with pytest.raises(SoundTooLongError):
        soundboard.upload_sound(
            owner.id,
            server.id,
            f"bad-duration-{uuid.uuid4().hex[:8]}",
            SoundFormat.MP3,
            "https://cdn.example.com/sounds/bad.mp3",
            100,
            duration_seconds=bad,
        )


def test_upload_sound_non_finite_volume_fails_safe(server_with_owner):
    """The core persistence path must clamp non-finite volume too."""
    import uuid

    from src.core.soundboard import SoundFormat

    owner, server, soundboard, servers = server_with_owner

    sound = soundboard.upload_sound(
        owner.id,
        server.id,
        f"bad-volume-{uuid.uuid4().hex[:8]}",
        SoundFormat.MP3,
        "https://cdn.example.com/sounds/bad.mp3",
        100,
        duration_seconds=1.0,
        volume=float("nan"),
    )

    assert sound.volume == 0.0
