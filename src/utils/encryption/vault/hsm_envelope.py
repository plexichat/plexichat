"""HSM envelope parsing/validation."""

from __future__ import annotations

import base64

from .constants import (
    HSM_ENVELOPE_PREFIX,
    HSM_MAX_ENVELOPE_BYTES,
    HSM_MIN_ENVELOPE_BYTES,
)
from .errors import HsmCryptoError


def parse_hsm_envelope(envelope: str) -> bytes:
    if not envelope.startswith(HSM_ENVELOPE_PREFIX):
        raise HsmCryptoError("not an HSM envelope")
    try:
        blob = base64.b64decode(envelope[len(HSM_ENVELOPE_PREFIX) :], validate=True)
    except Exception as exc:
        raise HsmCryptoError(f"malformed HSM envelope: {exc}") from exc
    if len(blob) < HSM_MIN_ENVELOPE_BYTES:
        raise HsmCryptoError("HSM envelope too short")
    if len(blob) > HSM_MAX_ENVELOPE_BYTES:
        raise HsmCryptoError("HSM envelope too large")
    return blob
