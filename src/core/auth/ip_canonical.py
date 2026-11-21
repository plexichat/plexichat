"""One canonical textual form for IP addresses used as security keys/indexes.

Several subsystems key mutable state on a client IP (rate-limit buckets, login
failure budgets, the IP blacklist, blind indexes). ``blind_index`` only
lowercases/strips its input, so without a shared canonicalizer every textual
spelling of one host gets its own key. A client behind a trusted proxy controls
the X-Forwarded-For spelling, and admin-supplied block entries can differ from
the form the resolver produces, so that lets one source rotate keys or evade a
ban. Route every such key through :func:`canonicalize_ip_address` instead.
"""

import ipaddress
from typing import Optional


def canonicalize_ip_address(value: Optional[str]) -> Optional[str]:
    """Collapse every textual spelling of one host to a single stable string.

    * IPv4-mapped IPv6 (``::ffff:1.2.3.4``) folds to ``1.2.3.4``.
    * IPv6 zero-compression / leading hex zeros / case normalize to the
      ``ipaddress`` compressed form (``2001:0db8::1`` -> ``2001:db8::1``).
    * A zone/scope id (``fe80::1%eth0``) names a local *interface*, not a
      distinct host, so it is stripped.

    Unparseable values are returned stripped (so they stay attributable rather
    than being silently dropped); ``None``/empty yields ``None``. Distinct
    hosts are never merged -- ``1.2.3.4`` and ``1.2.3.5`` stay distinct.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return text
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
        return str(parsed.ipv4_mapped)
    canonical = str(parsed)
    if isinstance(parsed, ipaddress.IPv6Address) and "%" in canonical:
        canonical = canonical.split("%", 1)[0]
    return canonical


__all__ = ["canonicalize_ip_address"]
