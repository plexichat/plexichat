"""Single canonical JSON encoding for webhook payloads.

Webhook signatures bind to a specific sequence of bytes. Signing (outbound),
verification (inbound), retry enqueue and delivery all have to agree on those
bytes, so the encoding lives here instead of being repeated at each call site.

The compact form is also what ``httpx`` sends for ``json=<payload>``, so the
bytes covered by the signature are exactly the bytes that go on the wire.
"""

import json
from typing import Any, Dict


def canonical_payload_bytes(payload: Dict[str, Any]) -> bytes:
    """Return the canonical JSON bytes for a webhook payload.

    ``sort_keys`` makes the encoding independent of dict insertion order and
    the compact separators match the outbound HTTP client's JSON body. All
    signers and verifiers must use this one helper; a second inline
    ``json.dumps`` would silently redefine the signed bytes.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
