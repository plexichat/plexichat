"""Hardened parsing for IP allow/deny lists and CIDR rules.

Allow/deny lists are operator-supplied strings that become routing decisions.
``ipaddress.ip_network`` accepts host bits (silently widening a rule), and an
unbounded list of entries can turn a config file into a CPU/memory sink. These
helpers parse strictly, bound every string and the entry count, and centralize
the "deny wins over allow" decision so every caller agrees on it.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Iterable, List, Tuple

from src.core.security.hardening import reject_control_characters

MAX_ENTRY_LENGTH = 128
MAX_ENTRIES = 1000


class IPRuleError(ValueError):
    """An IP allow/deny entry failed validation."""


def parse_cidr(value: Any, field: str = "cidr") -> ipaddress._BaseNetwork:
    """Parse a strict CIDR (host bits are rejected, not masked away)."""
    if not isinstance(value, str) or not value.strip():
        raise IPRuleError(f"{field} must be a non-empty string")
    if len(value) > MAX_ENTRY_LENGTH:
        raise IPRuleError(f"{field} is too long")
    try:
        text = reject_control_characters(value, field=field).strip()
    except ValueError as exc:
        raise IPRuleError(str(exc)) from exc
    try:
        # strict=True: "10.0.0.1/8" is a misconfiguration, not "10.0.0.0/8".
        return ipaddress.ip_network(text, strict=True)
    except ValueError as exc:
        raise IPRuleError(f"{field} is not a valid CIDR") from exc


def parse_ip_entry(value: Any, field: str = "ip rule") -> ipaddress._BaseNetwork:
    """Parse a plain IP or CIDR into a network (plain IPs become /32 or /128)."""
    if not isinstance(value, str) or not value.strip():
        raise IPRuleError(f"{field} must be a non-empty string")
    if len(value) > MAX_ENTRY_LENGTH:
        raise IPRuleError(f"{field} is too long")
    try:
        text = reject_control_characters(value, field=field).strip()
    except ValueError as exc:
        raise IPRuleError(str(exc)) from exc
    try:
        ip = ipaddress.ip_address(text)
        return ipaddress.ip_network(f"{ip}/{ip.max_prefixlen}", strict=False)
    except ValueError:
        return parse_cidr(text, field=field)


def parse_rule_list(
    values: Iterable[Any], *, max_entries: int = MAX_ENTRIES, field: str = "ip list"
) -> Tuple[ipaddress._BaseNetwork, ...]:
    """Parse and bound a list of IP/CIDR entries."""
    if (
        isinstance(max_entries, bool)
        or not isinstance(max_entries, int)
        or max_entries < 0
    ):
        raise IPRuleError("max_entries must be a non-negative integer")
    result: List[ipaddress._BaseNetwork] = []
    for value in values:
        if len(result) >= max_entries:
            raise IPRuleError(f"{field} has too many entries")
        result.append(parse_ip_entry(value, field=field))
    return tuple(result)


def ip_in_rules(ip: Any, rules: Iterable[ipaddress._BaseNetwork]) -> bool:
    """Whether ``ip`` is contained in any of ``rules``."""
    try:
        address = ipaddress.ip_address(ip)
    except (ValueError, TypeError):
        return False
    return any(address in network for network in rules)


def is_ip_permitted(
    ip: Any,
    allow: Iterable[ipaddress._BaseNetwork],
    deny: Iterable[ipaddress._BaseNetwork],
    *,
    default_allow: bool = True,
) -> bool:
    """Decide access: a deny match always wins; otherwise allow/default."""
    if ip_in_rules(ip, deny):
        return False
    if ip_in_rules(ip, allow):
        return True
    return default_allow


__all__ = [
    "MAX_ENTRIES",
    "MAX_ENTRY_LENGTH",
    "IPRuleError",
    "ip_in_rules",
    "is_ip_permitted",
    "parse_cidr",
    "parse_ip_entry",
    "parse_rule_list",
]
