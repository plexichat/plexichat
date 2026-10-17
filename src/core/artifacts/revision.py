"""
Artifact revision helpers - shared by the REST PATCH route and the gateway.

The code/text editor stamps a monotonic ``rev`` on every write. Originally the
rev lived inside ``payload["rev"]``; it now lives in the ``artifacts.rev``
column (see migration 002) and is enforced atomically with a conditional
``UPDATE ... WHERE rev = ?``. These pure helpers back both the legacy
payload-carried rev guard and the gateway op guard.
"""

from typing import Any, Dict, Optional


def payload_revision(payload: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract an editor's monotonic ``rev`` from a payload dict.

    Returns ``None`` when the payload carries no usable integer ``rev``
    (legacy payloads, other artifact types that may use the key differently,
    or non-dict payloads); the rev guard only applies when both sides expose
    one.
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("rev")
    # Booleans are ints in Python; exclude them so a ``True`` flag cannot
    # masquerade as revision 1.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def stale_payload_write(
    persisted_payload: Optional[Dict[str, Any]],
    incoming_payload: Optional[Dict[str, Any]],
) -> bool:
    """Return True when ``incoming_payload`` is not a newer revision.

    An editor write whose ``rev`` is *not* strictly newer than the persisted
    ``rev`` is stale: a concurrent tab already committed a later revision, and
    letting the older payload through would silently overwrite the newer one
    (lost update). The comparison is ``incoming <= persisted`` so an equal
    rev from a racing tab is rejected too — the editor ignores equal-rev WS
    ops, so allowing equal-rev REST writes would let a second tab's content
    win while the first tab's UI silently diverges from the persisted state.

    Only enforced when BOTH payloads carry a numeric ``rev``; writes without
    one (other artifact types, non-editor updates) are never stale, so the
    guard cannot break payloads that happen to use the key differently.
    """
    incoming = payload_revision(incoming_payload)
    persisted = payload_revision(persisted_payload)
    if incoming is None or persisted is None:
        return False
    return incoming <= persisted
