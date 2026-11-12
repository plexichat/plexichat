"""Stable peer/room identity for the mediasoup WebSocket adapter.

Identifiers are derived from a deployment-scoped secret so the same logical
peer reconnects to the same mediasoup-demo room instead of creating a new one
on every call.
"""

import hashlib
import hmac
import os

import utils.logger as logger
import utils.config as _voice_config  # noqa: F401

# Deployment-scoped secret used to derive stable peer / room
# identifiers. Loaded once per process so the same `peer_id`
# reproduces across calls — preventing Janus mediasoup-demo
# reconnections from spinning a brand new room every time.
_STABLE_PEER_SECRET: bytes = b""


def _load_stable_peer_secret() -> bytes:
    """Return the deployment-scoped secret used for stable peer IDs.

    Order of precedence:
    1. ``voice.mediated.stable_peer_secret`` config value (operators
       who want long-lived PIDs across restarts set this),
    2. ``PLEXICHAT_VOICE_STABLE_PEER_SECRET`` env var,
    3. ``PLEXICHAT_SYSTEM_KEY`` env var (the same keyring secret
       used by everything else; reuse keeps cross-module correlation
       deterministic),
    4. ``rate_limiting.bypass_secret`` (also random + stored in keyring),
    5. Final fallback: an empty bytes blob (deterministic but NOT
       secret — emits a single CRITICAL log so operators know).
    """
    global _STABLE_PEER_SECRET
    if _STABLE_PEER_SECRET:
        return _STABLE_PEER_SECRET
    try:
        voice_cfg = _voice_config.get("voice", {}) or {}
        mediated = voice_cfg.get("mediated", {}) or {}
        secret = mediated.get("stable_peer_secret")
        if secret:
            _STABLE_PEER_SECRET = secret.encode("utf-8")
            return _STABLE_PEER_SECRET
    except Exception:
        pass

    for env_name in (
        "PLEXICHAT_VOICE_STABLE_PEER_SECRET",
        "PLEXICHAT_SYSTEM_KEY",
    ):
        env_v = os.environ.get(env_name)
        if env_v:
            _STABLE_PEER_SECRET = env_v.encode("utf-8")
            return _STABLE_PEER_SECRET

    try:
        rl_cfg = _voice_config.get("rate_limiting", {}) or {}
        secret = rl_cfg.get("bypass_secret")
        if secret:
            _STABLE_PEER_SECRET = secret.encode("utf-8")
            return _STABLE_PEER_SECRET
    except Exception:
        pass

    # Best-effort: this runs at import time (see the module-level call below),
    # which can precede ``logger.setup()``. An unconfigured logger raises,
    # and a crash here would take the whole signaling package with it. The
    # notice is not lost: no secret means the next ``_stable_peer_id`` call
    # resolves it again, with a configured logger by then.
    try:
        logger.critical(
            "mediasoup_ws: NO deployment-scoped stable-peer secret was "
            "configured. peer_id values will be CONSTANT per instance "
            "(any restart / redeploy rotates them). Set "
            "voice.mediated.stable_peer_secret or "
            "PLEXICHAT_VOICE_STABLE_PEER_SECRET in production."
        )
    except RuntimeError:
        pass  # Logger not configured yet
    return b""


def _stable_peer_id(purpose: bytes) -> str:
    """Compute a stable, deterministic hex-encoded peer/room identifier.

    The output is identical across processes / restarts because we
    mix a deployment-scoped secret with the ``purpose`` payload via
    HMAC-SHA256. Two callers using the same ``purpose`` will get the
    same hex string, so Janus mediasoup reconnections latch onto the
    same room rather than spinning a fresh one every time.

    If the secret is empty (no deployment config), this still returns
    a *deterministic* value — the operator just loses the
    deploy-wide uniqueness guarantee. Operators MUST configure a
    secret before production use.
    """
    secret = _load_stable_peer_secret() or b"plexichat-dev-fallback"
    digest = hmac.new(secret, purpose, hashlib.sha256).hexdigest()
    return digest[:32]


# Resolve the secret at import time so a missing deployment secret is reported
# once, as early as possible; ``_stable_peer_id`` resolves it again lazily.
_load_stable_peer_secret()
