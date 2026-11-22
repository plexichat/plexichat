from fastapi import Request
from typing import Optional, Union, Dict, Any, List
import ipaddress
import utils.config as config


def _normalize_ip(value: Any):
    """Return an ipaddress object with IPv4-mapped IPv6 collapsed to IPv4.

    ``::ffff:a.b.c.d`` names the very same host as ``a.b.c.d``, but
    ``ipaddress`` keeps them in different families: an IPv4-mapped address is
    never ``in`` an IPv4 network and never ``==`` an IPv4 address. Trust
    decisions must not depend on which textual form a proxy used, so collapse
    the mapped form first (the same rule ``ip_validation.ip_in_rules`` uses for
    allow/deny lists). Returns ``None`` for unparsable input.
    """
    try:
        address = ipaddress.ip_address(value)
    except (ValueError, TypeError):
        return None
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped if mapped is not None else address


def _normalize_network(value: str):
    """Like ``_normalize_ip`` for a CIDR entry, including mapped CIDRs.

    ``::ffff:10.0.0.0/104`` describes the same hosts as ``10.0.0.0/8``; without
    collapsing it, a trusted-proxy range written in mapped form would silently
    stop matching the (normalized) IPv4 candidates below.
    """
    try:
        network = ipaddress.ip_network(value, strict=False)
    except (ValueError, TypeError):
        return None
    if network.version == 6 and network.prefixlen >= 96:
        mapped = getattr(network.network_address, "ipv4_mapped", None)
        if mapped is not None:
            try:
                return ipaddress.ip_network(
                    f"{mapped}/{network.prefixlen - 96}", strict=False
                )
            except ValueError:
                return network
    return network


def _ip_matches_trusted(ip_str: str, trusted_proxies: List[str]) -> bool:
    """
    Check if an IP matches a trusted proxy entry (supports CIDR and exact match).

    ``::ffff:a.b.c.d`` and ``a.b.c.d`` are treated as the same host so that a
    dual-stack reverse proxy reported in mapped form is still recognized. A
    missed match here would make ``get_client_ip`` ignore X-Forwarded-For and
    attribute every client to the proxy's own (often loopback) address, which
    collapses IP rate-limit buckets, defeats IP bans and mislabels audit logs.

    Args:
        ip_str: IP address to check (e.g. "192.168.1.1")
        trusted_proxies: List of trusted proxy entries (IPs or CIDR ranges)

    Returns:
        True if the IP matches any trusted proxy entry
    """
    ip = _normalize_ip(ip_str)
    if ip is None:
        return False
    for entry in trusted_proxies:
        if not isinstance(entry, str):
            continue
        candidate = entry.strip()
        if "/" in candidate:
            network = _normalize_network(candidate)
            if network is not None and ip.version == network.version and ip in network:
                return True
        elif ip == _normalize_ip(candidate):
            return True
    return False


def get_client_ip(request: Union[Request, Dict[str, Any]]) -> Optional[str]:
    """
    Extract IP address from a FastAPI Request or ASGI scope.
    Considering trusted proxies configuration.

    Supports CIDR notation for trusted proxies (e.g. "10.0.0.0/8", "192.168.0.0/16").
    Consolidated logic for consistent IP extraction across the application.
    Hardened to prevent X-Forwarded-For spoofing.
    """
    if isinstance(request, Request):
        scope = request.scope
    else:
        scope = request

    # Direct client IP from the socket
    client = scope.get("client")
    direct_ip = client[0] if client else None

    # Get headers from scope
    headers = scope.get("headers", [])

    # ASGI permits the same header name to appear more than once. Reading only
    # the first X-Forwarded-For let a client hide a spoofed value in an earlier
    # duplicate while a trusted proxy appended the real client address in a
    # later one: the attacker's header won and the IP used for rate limiting,
    # IP bans, audit logs and the admin host-restriction gate was forged.
    # Collecting every value in wire order and feeding them all to the
    # rightmost-untrusted-hop walk below keeps the proxy-appended entry last,
    # where the walk starts, so the client-controlled duplicates are ignored.
    #
    # latin-1 is the ASGI-native byte-to-text mapping; it never raises on
    # arbitrary header bytes. ``bytes.decode()`` (UTF-8) turned one 0xFF in a
    # header into an unhandled exception on this security-critical path.
    def get_headers(name: str) -> List[str]:
        name_bytes = name.lower().encode()
        return [v.decode("latin-1") for k, v in headers if k.lower() == name_bytes]

    def get_header(name: str) -> Optional[str]:
        values = get_headers(name)
        # For single-value hints (CF-Connecting-IP) the proxy-inserted value is
        # the last one; prefer it over an earlier client-supplied duplicate.
        return values[-1] if values else None

    trusted_proxies: List[str] = []
    try:
        api_conf = config.get("api", {})
        trusted_proxies = api_conf.get("trusted_proxies", []) or []
    except Exception:
        trusted_proxies = []

    # Only consider X-Forwarded-For if the direct peer is a trusted proxy.
    client_ip = direct_ip
    if direct_ip and _ip_matches_trusted(direct_ip, trusted_proxies):
        # Every X-Forwarded-For header, in wire order, is one logical list.
        # A trusted reverse proxy appends the observed peer to the right, so
        # concatenating in this order preserves the invariant the walk below
        # relies on: the rightmost entry is the one our infrastructure added.
        forwarded_entries = get_headers("X-Forwarded-For")
        forwarded = ",".join(forwarded_entries) if forwarded_entries else None
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            if parts:
                # SECURITY: rightmost-untrusted-hop selection. The LEFTMOST
                # XFF entry is fully attacker-controlled (any downstream
                # client may inject "X-Forwarded-For: <fake>" and every hop
                # appends to the RIGHT). Walking from the right and skipping
                # entries that match configured trusted proxy CIDRs yields
                # the first address we did NOT receive from a trusted proxy
                # — i.e. the address our own infrastructure observed. If
                # every entry is a trusted proxy (or parsing fails) we fall
                # back to CF-Connecting-IP / direct peer below.
                for candidate in reversed(parts):
                    host = candidate
                    # Strip an optional port or bracketed IPv6 suffix form.
                    if host.startswith("[") and "]" in host:
                        host = host[1 : host.index("]")]
                    elif host.count(":") == 1:
                        host = host.rsplit(":", 1)[0]
                    try:
                        ipaddress.ip_address(host)
                    except ValueError:
                        # Malformed entry: attacker-controlled garbage —
                        # ignore it and keep walking left.
                        continue
                    if not _ip_matches_trusted(host, trusted_proxies):
                        client_ip = host
                        break
                else:
                    # All reported hops were trusted proxies — fall through
                    # to CF-Connecting-IP below.
                    client_ip = None

        # Cloudflare sets CF-Connecting-IP to the real client IP. When behind
        # trusted proxies and X-Forwarded-For carried only trusted hops,
        # use CF-Connecting-IP as the authoritative client address rather
        # than the direct peer (which is the last proxy in the chain).
        if client_ip is None:
            cf_connecting_ip = get_header("CF-Connecting-IP")
            if cf_connecting_ip:
                cf_ip = cf_connecting_ip.strip()
                try:
                    ipaddress.ip_address(cf_ip)
                    client_ip = cf_ip
                except ValueError:
                    # Malformed CF-Connecting-IP — fall through to direct_ip.
                    client_ip = direct_ip

    return client_ip or direct_ip
