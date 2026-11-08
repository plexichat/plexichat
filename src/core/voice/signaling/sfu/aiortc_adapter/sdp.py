"""SDP parsing helpers for the aiortc SFU adapter."""

from __future__ import annotations

import re
from typing import Any, Dict, List


def _parse_ice_params_from_sdp(sdp: str) -> Dict[str, str]:
    """Extract ICE ufrag/pwd from an SDP string."""
    ice_ufrag = ""
    ice_pwd = ""
    for line in (sdp or "").splitlines():
        if line.startswith("a=ice-ufrag:"):
            ice_ufrag = line.split(":", 1)[1].strip()
        elif line.startswith("a=ice-pwd:"):
            ice_pwd = line.split(":", 1)[1].strip()
    return {"usernameFragment": ice_ufrag, "password": ice_pwd}


def _parse_ice_candidates_from_sdp(sdp: str) -> List[Dict[str, Any]]:
    """Extract ICE candidates from an SDP string."""
    candidates: List[Dict[str, Any]] = []
    for line in (sdp or "").splitlines():
        if not line.startswith("a=candidate:"):
            continue
        parts = line[12:].split()
        if len(parts) < 8:
            continue
        try:
            candidates.append(
                {
                    "foundation": parts[0],
                    "component": int(parts[1]),
                    "protocol": parts[2].lower(),
                    "priority": int(parts[3]),
                    "ip": parts[4],
                    "port": int(parts[5]),
                    "type": parts[7],
                }
            )
        except (ValueError, IndexError):
            continue
    return candidates


def _parse_dtls_params_from_sdp(sdp: str) -> Dict[str, Any]:
    fingerprints = []
    for m in re.finditer(r"a=fingerprint:(\S+) (\S+)", sdp or ""):
        fingerprints.append(
            {
                "algorithm": m.group(1),
                "value": m.group(2),
            }
        )
    return {
        "fingerprints": fingerprints,
        "role": "auto",
    }


__all__ = [
    "_parse_ice_params_from_sdp",
    "_parse_ice_candidates_from_sdp",
    "_parse_dtls_params_from_sdp",
]
