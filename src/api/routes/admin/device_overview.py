"""
Admin device-trust & session-duration overview.

Read-only aggregation over auth_devices and auth_sessions so admins can see,
per user: which devices are trusted (2FA skip), and how long that user's
sessions last (default vs trusted TTL from the sessions config).

SECURITY: fingerprints are hashed (blind-indexed) server-side. The overview
never returns raw fingerprints or raw IPs — only trust state, duration math,
and coarse metadata.
"""

from typing import Any, Dict, List

import utils.logger as logger

# Config fallbacks mirrored from src/config_defaults_authentication.py so the
# overview reports the *effective* policy even when the config block is sparse.
_DEFAULTS = {
    "expire_hours": 168,
    "trusted_expire_hours": 720,
    "extend_on_activity": True,
    "extend_threshold_hours": 24,
    "trusted_extend_threshold_hours": 168,
}


def _sessions_policy(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return the effective session-duration policy with defaults applied.

    A null trusted_expire_hours means the trusted override is disabled —
    both durations then equal expire_hours.
    """
    sessions = dict(config.get("sessions", {}))
    expire_hours = int(sessions.get("expire_hours", _DEFAULTS["expire_hours"]))
    trusted_raw = sessions.get(
        "trusted_expire_hours", _DEFAULTS["trusted_expire_hours"]
    )
    trusted_expire_hours = None if trusted_raw is None else int(trusted_raw)

    extend_threshold = int(
        sessions.get("extend_threshold_hours", _DEFAULTS["extend_threshold_hours"])
    )
    trusted_extend_raw = sessions.get(
        "trusted_extend_threshold_hours",
        # Falls back to trusted_expire_hours, then extend_threshold_hours.
        sessions.get(
            "trusted_expire_hours", _DEFAULTS["trusted_extend_threshold_hours"]
        ),
    )
    trusted_extend = None if trusted_extend_raw is None else int(trusted_extend_raw)

    return {
        "expire_hours": expire_hours,
        "trusted_expire_hours": trusted_expire_hours,
        "trusted_override_enabled": trusted_expire_hours is not None,
        "effective_trusted_expire_hours": (
            expire_hours if trusted_expire_hours is None else trusted_expire_hours
        ),
        "extend_on_activity": bool(
            sessions.get("extend_on_activity", _DEFAULTS["extend_on_activity"])
        ),
        "extend_threshold_hours": extend_threshold,
        "trusted_extend_threshold_hours": trusted_extend,
    }


def get_device_trust_overview(
    auth_manager: Any, config: Dict[str, Any]
) -> Dict[str, Any]:
    """Build the admin overview: policy + per-user device trust and sessions.

    Returns:
        {
          "policy": {...effective durations...},
          "users": [
            {
              "user_id": ...,
              "devices": [{"id", "name", "device_type", "trusted", "trusted_at",
                           "first_seen_at", "last_seen_at"}],
              "trusted_device_count": n,
              "sessions": [{"id", "device_id", "device_trusted",
                            "duration_hours", "policy_kind", "created_at",
                            "expires_at", "last_activity", "expired"}],
              "session_count": n,
              "active_session_count": n,
            }
          ],
          "totals": {"users", "devices", "trusted_devices", "sessions",
                     "active_sessions", "trusted_device_sessions"},
        }
    """
    policy = _sessions_policy(config)
    now = auth_manager._get_timestamp()

    users: List[Dict[str, Any]] = []
    totals = {
        "users": 0,
        "devices": 0,
        "trusted_devices": 0,
        "sessions": 0,
        "active_sessions": 0,
        "trusted_device_sessions": 0,
    }

    try:
        rows = auth_manager._db.fetch_all(
            "SELECT DISTINCT user_id FROM auth_devices "
            "UNION SELECT DISTINCT user_id FROM auth_sessions WHERE revoked = 0"
        )
        user_ids = sorted(int(r["user_id"]) for r in rows)
    except Exception as e:
        logger.warning(f"Device overview: failed to enumerate users: {e}")
        user_ids = []

    for uid in user_ids:
        try:
            devices = auth_manager.get_devices(uid)
        except Exception as e:
            logger.warning(f"Device overview: get_devices({uid}) failed: {e}")
            devices = []

        # Trust lookup by device id (server-side row), mirroring session.py.
        trusted_by_id: Dict[Any, bool] = {}
        for d in devices:
            trusted_by_id[d.id] = bool(d.trusted)

        try:
            sessions = auth_manager.get_sessions(uid)
        except Exception as e:
            logger.warning(f"Device overview: get_sessions({uid}) failed: {e}")
            sessions = []

        device_rows = [
            {
                # Snowflake ids are emitted as strings: a JS Number cannot
                # hold a 64-bit id exactly, so a numeric id would be rounded
                # by the client and subsequent lookups would miss.
                "id": str(d.id),
                "name": d.name,
                "device_type": d.device_type,
                "trusted": bool(d.trusted),
                "trusted_at": d.trusted_at,
                "first_seen_at": d.first_seen_at,
                "last_seen_at": d.last_seen_at,
            }
            for d in devices
        ]

        session_rows: List[Dict[str, Any]] = []
        for s in sessions:
            device_trusted = bool(s.device_id and trusted_by_id.get(s.device_id))
            if device_trusted and policy["trusted_override_enabled"]:
                duration = policy["effective_trusted_expire_hours"]
                kind = "trusted"
            else:
                duration = policy["expire_hours"]
                kind = "default"
            expired = bool(s.expires_at <= now)
            session_rows.append(
                {
                    "id": str(s.id),
                    "device_id": str(s.device_id) if s.device_id is not None else None,
                    "device_trusted": device_trusted,
                    "duration_hours": duration,
                    "policy_kind": kind,
                    "created_at": s.created_at,
                    "expires_at": s.expires_at,
                    "last_activity": s.last_activity,
                    "expired": expired,
                }
            )

        active = [s for s in session_rows if not s["expired"]]
        trusted_devices = [d for d in device_rows if d["trusted"]]

        users.append(
            {
                "user_id": str(uid),
                "devices": device_rows,
                "trusted_device_count": len(trusted_devices),
                "sessions": session_rows,
                "session_count": len(session_rows),
                "active_session_count": len(active),
            }
        )

        totals["users"] += 1
        totals["devices"] += len(device_rows)
        totals["trusted_devices"] += len(trusted_devices)
        totals["sessions"] += len(session_rows)
        totals["active_sessions"] += len(active)
        totals["trusted_device_sessions"] += sum(
            1 for s in session_rows if s["device_trusted"]
        )

    return {"policy": policy, "users": users, "totals": totals}
