"""Length caps and format checks for TLS / certificate-pinning config strings.

Hostnames, SAN entries and pin fingerprints are operator-supplied strings that
are copied into generated certificates and compared against presented chains.
An unbounded string can blow up a certificate, and a malformed "pin" that is
accepted silently means pinning is not actually enforced. These helpers bound
every such string and require the shape each value claims to have.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Iterable, List

from src.core.security.hardening import reject_control_characters

MAX_HOSTNAME_LENGTH = 253
MAX_LABEL_LENGTH = 63
MAX_SAN_ENTRIES = 100
MAX_PINS = 16
MAX_CONFIG_STRING = 1024
_FINGERPRINT = re.compile(r"^[0-9a-fA-F]{64}$")
_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class TLSConfigValidationError(ValueError):
    """A TLS configuration string failed a length or format check."""


def cap_config_string(
    value: Any, field: str, *, max_length: int = MAX_CONFIG_STRING
) -> str:
    """Return a bounded, control-free config string."""
    if not isinstance(value, str) or not value.strip():
        raise TLSConfigValidationError(f"{field} must be a non-empty string")
    if len(value) > max_length:
        raise TLSConfigValidationError(f"{field} is too long")
    try:
        return reject_control_characters(value, field=field)
    except ValueError as exc:
        raise TLSConfigValidationError(str(exc)) from exc


def validate_hostname(value: Any, field: str = "hostname") -> str:
    """Validate a DNS hostname (labels, length, no control characters)."""
    name = (
        cap_config_string(value, field, max_length=MAX_HOSTNAME_LENGTH)
        .strip()
        .rstrip(".")
    )
    if not name or len(name) > MAX_HOSTNAME_LENGTH:
        raise TLSConfigValidationError(f"{field} has an invalid length")
    for label in name.split("."):
        if not label or len(label) > MAX_LABEL_LENGTH or not _LABEL.match(label):
            raise TLSConfigValidationError(f"{field} has an invalid label")
    return name


def validate_san_entry(value: Any, field: str = "san") -> str:
    """Validate one SAN entry as either an IP address or a DNS name."""
    text = cap_config_string(value, field, max_length=MAX_HOSTNAME_LENGTH).strip()
    try:
        ipaddress.ip_address(text)
        return text
    except ValueError:
        pass
    return validate_hostname(text, field=field)


def validate_fingerprint(value: Any, field: str = "pin") -> str:
    """Validate a SHA-256 fingerprint (64 hex chars, optional colon separators)."""
    text = cap_config_string(value, field, max_length=MAX_CONFIG_STRING).strip().lower()
    compact = text.replace(":", "")
    if not _FINGERPRINT.match(compact):
        raise TLSConfigValidationError(f"{field} must be a SHA-256 fingerprint")
    return compact


def validate_pin_list(values: Iterable[Any], *, max_pins: int = MAX_PINS) -> List[str]:
    """Validate and bound a list of pin fingerprints."""
    if isinstance(max_pins, bool) or not isinstance(max_pins, int) or max_pins < 1:
        raise TLSConfigValidationError("max_pins must be a positive integer")
    result: List[str] = []
    for value in values:
        if len(result) >= max_pins:
            raise TLSConfigValidationError("too many certificate pins")
        result.append(validate_fingerprint(value))
    return result


__all__ = [
    "MAX_CONFIG_STRING",
    "MAX_HOSTNAME_LENGTH",
    "MAX_PINS",
    "MAX_SAN_ENTRIES",
    "TLSConfigValidationError",
    "cap_config_string",
    "validate_fingerprint",
    "validate_hostname",
    "validate_pin_list",
    "validate_san_entry",
]
