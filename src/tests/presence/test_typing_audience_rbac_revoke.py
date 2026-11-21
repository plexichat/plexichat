"""Regression: a role revoke must retire a cached typing-audience verdict.

``TypingAudienceCache`` memoizes each server channel's ``channels.view``-
filtered viewer set. It used to key only on ``(time_epoch, server, channel)``,
so a role edit that removed a member's ``channels.view`` left that member in
the warm viewer set and they kept receiving typing frames for the whole TTL —
a stale ALLOW with no RBAC key. The cache now folds the server's RBAC epoch
(bumped by every role/membership/override/ownership mutation) into the key, so
the stale verdict is unreachable on the very next read.

The cache clock is frozen in these tests: the *only* thing that can invalidate
an entry is an RBAC epoch bump, which makes the regression non-vacuous.
"""

from src.core.presence.typing_audience import TypingAudienceCache
from src.core.servers.channel_access_cache import get_channel_access_cache

SERVER = 8_000_013
CHANNEL = 8_000_013
VIEWERS = [11, 22, 33]


def _cache():
    return TypingAudienceCache(clock=lambda: 1000.0)


def test_role_revoke_retires_stale_viewer_set():
    cache = _cache()
    cache.put(SERVER, CHANNEL, VIEWERS)
    assert cache.get(SERVER, CHANNEL) == VIEWERS

    # The servers module bumps this epoch on role assignment/removal, role
    # permission edits/deletion, override changes and ownership transfers.
    # The time epoch is frozen, so only the RBAC epoch can retire the entry.
    get_channel_access_cache().bump(SERVER, 33)

    assert cache.get(SERVER, CHANNEL) is None


def test_fresh_verdict_is_cached_under_new_epoch():
    """The fix must not disable the memo: post-revoke hits stay cached."""
    cache = _cache()
    get_channel_access_cache().bump(SERVER, 33)

    cache.put(SERVER, CHANNEL, [11, 22])
    assert cache.get(SERVER, CHANNEL) == [11, 22]
    assert cache.get(SERVER, CHANNEL) == [11, 22]
    assert cache.stats()["hits"] == 2
