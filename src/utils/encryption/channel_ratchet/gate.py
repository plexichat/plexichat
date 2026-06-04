"""
Licence gate for the v3 channel ratchet.

Single source of truth for the question "is the running PlexiChat
instance licensed to use server-managed v3 channel ratchet encryption?".

The function is intentionally a thin wrapper around
``utils.common_utils.utils.licensing.has_feature`` so that:

* there is exactly one place in the codebase that consults the
  licence for this feature (and thus exactly one place to change if
  the licence schema ever evolves);
* tests can monkeypatch the gate in one location;
* the call sites in the messaging service stay short and readable.

The function is **not** cached. ``has_feature`` is a dict lookup and
the ratchet is not on the per-message critical path in a way that
warrants caching; keeping it uncached also makes the licence-down-at-
runtime case straightforward to reason about.
"""

from __future__ import annotations

LICENCE_FEATURE_NAME = "channel_ratchet_encryption"


def ratchet_encryption_licensed() -> bool:
    """Return True iff the running licence enables the v3 channel ratchet.

    On a free-tier install (no licence, or a licence that does not
    list the feature) this returns ``False`` and the messaging
    service falls back to the per-message v1/v2 keyring envelope and
    the client manages its own key cache.

    On a licensed install with the feature enabled, this returns
    ``True`` and the messaging service uses the v3 channel ratchet
    (server-managed ``start_key``, rotation, split-on-delete,
    ``RATCHET_UPDATE`` websocket broadcasts).

    The default of the underlying :func:`has_feature` call is
    ``False`` (not ``True``): a missing feature key in the licence
    means the feature is off, not on. This is the safe direction.
    """
    from src.utils.common_utils.utils.licensing import has_feature

    try:
        return bool(has_feature(LICENCE_FEATURE_NAME, default=False))
    except Exception:
        return False


__all__ = [
    "LICENCE_FEATURE_NAME",
    "ratchet_encryption_licensed",
]
