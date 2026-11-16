"""
Inbound federation routes for cross-instance artifact traffic (PlexiJoin).

These routes are the receiving half of the shipped artifact federation
transport. A remote Plexichat instance forwards live artifact ops/events to
``/api/v1/federation/artifact-op`` and ``/artifact-event``; this module
authenticates each payload with the connection's shared key (HMAC-SHA256, the
same secret the outbound transport signs with), then relays the op to local
subscribers through the existing WebSocket artifact fabric.

Security model:
- The route is unauthenticated by JWT (remote instances have no local user
  token); the HMAC signature over the raw request body is the credential. A
  request is accepted only if its signature matches the shared key of an
  *active* outbound PlexiJoin connection.
- Inbound ops are relayed with ``forward_to_federation=False`` so an op never
  ping-pongs between two instances forever.
- This module never grants local permissions. A remote op is only delivered to
  local subscribers that already pass the normal ``artifact.view`` access
  check inside ``relay_artifact_op``.
"""

from src.api.request_guards import MAX_FEDERATION_BODY, read_body_capped
from src.core.security.hardening import HardeningViolation, require_https_url
from src.utils.security import URLValidator, pinned_request_target

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urljoin, urlparse

from fastapi import APIRouter, HTTPException, Request

import utils.logger as logger

router = APIRouter()


# --- SSRF hardening for remote federation endpoints ----------------------
#
# ``remote_url`` is attacker-supplied in a signed join-request envelope. It is
# persisted for admin review and later becomes an *outbound* federation target,
# so validating only its shape (as ``require_https_url`` does) is not enough: a
# URL such as ``https://169.254.169.254/latest/meta-data/`` is a valid absolute
# HTTPS URL but points at the cloud metadata service. Every remote endpoint is
# therefore resolved once here, every resolved address is checked against the
# shared SSRF rules, and the approved address is what callers must connect to.
# Redirect targets are resolved *again* — a hostname that answers a public IP
# for the first lookup and a private one for the next is the classic DNS
# rebinding attack, and re-checking on each hop is what defeats it.

MAX_URL_LENGTH = 2048
MAX_REDIRECTS = 5


class FederationEndpointRejected(ValueError):
    """A remote federation endpoint failed the SSRF gate."""


@dataclass(frozen=True)
class ResolvedEndpoint:
    """An endpoint whose hostname has been resolved and address approved.

    ``address`` is the only address a connection may use; reconnecting by
    ``hostname`` re-opens the DNS rebinding window the resolution closed.
    """

    url: str
    hostname: str
    address: str


def _reject_forbidden_address(host: str, field: str) -> None:
    """Reject an IP literal in a non-global range (defence in depth).

    ``URLValidator.is_private_ip`` already covers loopback, link-local (the
    ``169.254.0.0/16`` metadata range), CGNAT, NAT64, IPv4-mapped IPv6,
    multicast, reserved and unspecified addresses. Running it on literals
    before DNS (and again on the resolved IP) means an obfuscated numeric host
    that a resolver accepts cannot slip past the gate.
    """
    import ipaddress

    candidate = host.strip("[]")
    try:
        addr = ipaddress.ip_address(candidate)
    except ValueError:
        return  # a DNS name; its resolved addresses are checked by the caller
    if URLValidator().is_private_ip(str(addr)):
        raise FederationEndpointRejected(f"{field} targets a non-public address")


def resolve_remote_endpoint(
    value: Any, field: str = "remote_url", *, require_https: bool = False
) -> ResolvedEndpoint:
    """Resolve and validate an attacker-supplied federation endpoint.

    Returns the approved ``ResolvedEndpoint`` or raises
    ``FederationEndpointRejected``. The exception detail never includes the
    resolved IP, so a rejected URL is not a free internal port scan.
    """
    candidate = str(value or "").strip()
    if not candidate:
        raise FederationEndpointRejected(f"{field} must not be empty")
    if len(candidate) > MAX_URL_LENGTH:
        raise FederationEndpointRejected(f"{field} is too long")

    parsed = urlparse(candidate)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise FederationEndpointRejected(f"{field} must use http or https")
    if require_https and scheme != "https":
        raise FederationEndpointRejected(f"{field} must use https")
    if not parsed.hostname:
        raise FederationEndpointRejected(f"{field} must include a host")
    if parsed.username or parsed.password:
        raise FederationEndpointRejected(f"{field} must not embed credentials")

    # Literal IPs (including integer/hex forms a resolver may accept) before DNS.
    _reject_forbidden_address(parsed.hostname, field)

    try:
        hostname, address = URLValidator().validate_url_for_request(candidate)
    except Exception as exc:
        # Log the reason server-side; the caller sees only the field name.
        logger.warning("Rejected federation %s failing SSRF check: %s", field, exc)
        raise FederationEndpointRejected(
            f"{field} resolves to a forbidden address"
        ) from exc
    _reject_forbidden_address(address, field)
    return ResolvedEndpoint(url=candidate, hostname=hostname, address=address)


def validate_redirect_chain(
    initial_url: Any,
    locations: Iterable[Any],
    field: str = "remote_url",
    *,
    require_https: bool = True,
) -> ResolvedEndpoint:
    """Validate an endpoint and every redirect target in its chain.

    Each ``Location`` is resolved relative to the current URL and passed back
    through :func:`resolve_remote_endpoint`, so a redirect to loopback, a
    metadata address, or a hostname that resolves to a private IP on the second
    lookup is rejected. The hop count and a visited-set bound traffic that tries
    to loop forever.
    """
    endpoint = resolve_remote_endpoint(
        initial_url, field, require_https=require_https
    )
    visited = {endpoint.url}
    for index, location in enumerate(locations):
        if index >= MAX_REDIRECTS:
            raise FederationEndpointRejected(f"{field} exceeded the redirect limit")
        target = urljoin(endpoint.url, str(location or ""))
        if target in visited:
            raise FederationEndpointRejected(f"{field} redirect loop")
        visited.add(target)
        endpoint = resolve_remote_endpoint(
            target, field, require_https=require_https
        )
    return endpoint


# --- HTTPS-only transport with pinned certificate policy ------------------
#
# The shared key signed into every federation envelope is a bearer secret: it
# must never cross the wire in cleartext. Remote endpoints are therefore
# HTTPS-only, including after redirects. TLS also authenticates the peer, so a
# connection must verify the certificate against the system trust store using
# the *real* hostname for SNI/Host while connecting to the address the SSRF
# gate approved. Disabling verification (``verify=False`` / an unverified SSL
# context) would let an on-path attacker impersonate the peer and steal the
# key, so the helper below makes that an explicit, testable refusal. This is a
# policy helper rather than a socket wrapper because the transport lives in
# the PlexiJoin manager; ``build_pinned_target`` returns exactly the pieces an
# HTTP client needs and never hands back a raw, re-resolvable hostname URL.

FEDERATION_TLS_POLICY = "system-trust"


@dataclass(frozen=True)
class PinnedTarget:
    """The pinned request target for a validated federation endpoint."""

    request_url: str
    headers: Dict[str, str]
    extensions: Dict[str, Any]
    tls_policy: str = FEDERATION_TLS_POLICY


def require_secure_remote_endpoint(
    value: Any, field: str = "remote_url"
) -> ResolvedEndpoint:
    """Resolve an endpoint, requiring HTTPS on the initial URL and every hop.

    ``validate_redirect_chain`` is fed by the outbound transport as each
    ``Location`` is followed; its default ``require_https=True`` enforces the
    same rule on redirects, so an HTTP downgrade mid-chain is rejected too.
    """
    return resolve_remote_endpoint(value, field, require_https=True)


def assert_tls_verification_enabled(verify: Any) -> bool:
    """Refuse the TLS-verification escapes a caller might pass to an client.

    Federation traffic carries a shared secret, so ``verify=False`` (or an
    equivalent unverified SSL context) is never acceptable. ``True`` and a
    CA-bundle path are; the latter is the "pinned certificate policy where
    feasible" escape hatch for deployments with a private CA.
    """
    if verify is False or verify is None:
        raise FederationEndpointRejected("federation TLS verification must be enabled")
    if hasattr(verify, "check_hostname") and not verify.check_hostname:
        raise FederationEndpointRejected("federation TLS must verify the hostname")
    if hasattr(verify, "verify_mode"):
        import ssl

        if verify.verify_mode == ssl.CERT_NONE:
            raise FederationEndpointRejected("federation TLS must verify certificates")
    return True


def build_pinned_target(endpoint: ResolvedEndpoint) -> PinnedTarget:
    """Build a request target that connects to the approved address only.

    ``pinned_request_target`` rewrites the authority to the resolved IP while
    keeping the real hostname as the ``Host`` header and TLS SNI, so virtual
    hosts and certificate checks still work without a second DNS lookup.
    """
    scheme = (urlparse(endpoint.url).scheme or "").lower()
    if scheme != "https":
        raise FederationEndpointRejected("federation endpoints must use https")
    request_url, headers, extensions = pinned_request_target(
        endpoint.url, endpoint.hostname, endpoint.address
    )
    return PinnedTarget(
        request_url=request_url,
        headers=headers,
        extensions=extensions,
    )


def _get_plexijoin_manager():
    """Build a PlexiJoin manager for the inbound route (mirrors admin routes)."""
    import src.api as api_mod

    db = api_mod.get_db()
    if db is None:
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": 503, "message": "Database not available"}},
        )

    import utils.licensing as license_module

    if not license_module.has_feature("plexijoin", default=False):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {"code": 403, "message": "PlexiJoin feature is not licensed"}
            },
        )

    try:
        import utils.encryption as enc_mod

        class _EncryptionService:
            @staticmethod
            def encrypt(data: str) -> str:
                return enc_mod.encrypt_data(data)

            @staticmethod
            def decrypt(data: str) -> str:
                return enc_mod.decrypt_data(data)

        encryption_service = _EncryptionService()
    except Exception:
        encryption_service = None

    if encryption_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": 503,
                    "message": "PlexiJoin encryption service unavailable",
                }
            },
        )

    from src.core.plexijoin import PlexiJoinManager

    return PlexiJoinManager(
        db=db, admin_logger=None, encryption_service=encryption_service
    )


def _authenticate(
    manager,
    body: bytes,
    signature: Optional[str],
    timestamp: Optional[str],
    nonce: Optional[str],
    statuses=("active",),
) -> Optional[Dict[str, Any]]:
    """Verify the HMAC signature against a connection's shared key.

    Returns the matching connection dict (so callers can attribute traffic),
    or ``None`` when no known connection's key verifies the payload.
    """
    from src.core.artifacts.federation import verify_payload_signature

    if not signature or not timestamp or not nonce:
        return None
    try:
        timestamp_int = int(timestamp)
    except (TypeError, ValueError):
        return None
    for status in statuses:
        try:
            result = manager.list_connections(status=status)
            raw = result.get("connections") if isinstance(result, dict) else []
            connections = raw if isinstance(raw, list) else []
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("federation inbound: list_connections failed: %s", e)
            continue
        for conn in connections:
            if not isinstance(conn, dict):
                continue
            if verify_payload_signature(
                conn.get("shared_key"), body, signature, timestamp, nonce
            ) and manager.consume_replay_nonce(int(conn["id"]), nonce, timestamp_int):
                return conn
    return None


def _parse_json_body(body: bytes) -> Dict[str, Any]:
    import json

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid federation payload"}},
        )
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid federation payload"}},
        )
    return payload


async def _relay_op(artifact_id, op, actor_id) -> int:
    from src.api.websocket import get_dispatcher
    from src.api.websocket.artifacts import relay_artifact_op

    dispatcher = get_dispatcher()
    if dispatcher is None:
        return 0
    return await relay_artifact_op(
        dispatcher,
        int(artifact_id),
        op,
        int(actor_id) if actor_id is not None else 0,
        forward_to_federation=False,
    )


@router.post(
    "/artifact-op",
    status_code=200,
    summary="Receive a live artifact op forwarded from a remote instance",
)
async def artifact_op_inbound(request: Request):
    """Authenticate and relay a cross-instance artifact op."""
    manager = _get_plexijoin_manager()
    body = await read_body_capped(request, MAX_FEDERATION_BODY)
    signature = request.headers.get("x-plexijoin-signature")
    timestamp = request.headers.get("x-plexijoin-timestamp")
    nonce = request.headers.get("x-plexijoin-nonce")
    if not _authenticate(manager, body, signature, timestamp, nonce):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid federation signature"}},
        )

    payload = _parse_json_body(body)

    artifact_id = payload.get("artifact_id")
    op = payload.get("op")
    actor_id = payload.get("actor_id", 0)
    if artifact_id is None or not isinstance(op, dict) or not op.get("op_type"):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Malformed artifact op payload"}},
        )

    delivered = await _relay_op(artifact_id, op, actor_id)
    return {"ok": True, "delivered": delivered}


@router.post(
    "/artifact-event",
    status_code=200,
    summary="Receive an artifact-level event forwarded from a remote instance",
)
async def artifact_event_inbound(request: Request):
    """Authenticate and acknowledge a cross-instance artifact event.

    Artifact CRUD remains gated by local permissions; a remote event is an
    informational notification and is recorded, not applied to local state.
    """
    manager = _get_plexijoin_manager()
    body = await read_body_capped(request, MAX_FEDERATION_BODY)
    signature = request.headers.get("x-plexijoin-signature")
    timestamp = request.headers.get("x-plexijoin-timestamp")
    nonce = request.headers.get("x-plexijoin-nonce")
    if not _authenticate(manager, body, signature, timestamp, nonce):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid federation signature"}},
        )

    payload = _parse_json_body(body)

    logger.info(
        "federation inbound: received artifact event %s for artifact %s",
        payload.get("event_type"),
        (payload.get("artifact") or {}).get("id")
        if isinstance(payload.get("artifact"), dict)
        else None,
    )
    return {"ok": True}


@router.post(
    "/join-request",
    status_code=200,
    summary="Receive a federation join request from a remote instance",
)
async def join_request_inbound(request: Request):
    """Record an inbound PlexiJoin join request for admin review.

    The request must be HMAC-signed with the shared key of a connection this
    instance already knows about (pending or active) — that key exchange is
    what bootstraps trust. Verified requests are inserted into
    ``plexijoin_inbound_requests`` as ``pending`` and surface through the
    admin review endpoints.
    """
    manager = _get_plexijoin_manager()
    body = await read_body_capped(request, MAX_FEDERATION_BODY)
    signature = request.headers.get("x-plexijoin-signature")
    timestamp = request.headers.get("x-plexijoin-timestamp")
    nonce = request.headers.get("x-plexijoin-nonce")
    conn = _authenticate(
        manager,
        body,
        signature,
        timestamp,
        nonce,
        statuses=("active", "pending"),
    )
    if not conn:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid federation signature"}},
        )

    payload = _parse_json_body(body)

    remote_instance_id = str(payload.get("instance_id") or "").strip()
    if not remote_instance_id:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "instance_id is required"}},
        )

    # The remote_url is attacker-supplied in the signed envelope and is later
    # surfaced to an admin for approval, after which it becomes an outbound
    # federation target. A relative URL or one carrying embedded credentials
    # must never be persisted as a target. Keep the historical pass-through
    # for an absent URL so a peer that omits it is still recorded.
    remote_url = str(payload.get("remote_url") or conn.get("remote_url") or "")
    if remote_url:
        try:
            # Shape check first (absolute, no embedded credentials), then the
            # full SSRF gate: resolve DNS and reject private/link-local/
            # metadata addresses before the target is ever persisted.
            remote_url = require_https_url(remote_url, "remote_url")
            remote_url = require_secure_remote_endpoint(remote_url, "remote_url").url
        except (HardeningViolation, FederationEndpointRejected) as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": str(exc)}},
            ) from exc

    row = manager.submit_inbound_request(
        remote_instance_id=remote_instance_id,
        remote_url=remote_url,
        requested_by=payload.get("requested_by"),
        note=payload.get("note"),
    )
    # ``request_id`` is the ``plexijoin_inbound_requests`` INTEGER PRIMARY KEY
    # rowid, so it stays numeric (only 64-bit snowflakes need stringifying).
    return {"ok": True, "request_id": row["id"]}


@router.post(
    "/message",
    status_code=200,
    summary="Receive a federated message from a remote instance",
)
async def message_inbound(request: Request):
    """Receive a cross-instance message over an ACTIVE federation link.

    The payload is HMAC-verified against the sending connection's shared
    key; verified traffic is recorded via ``record_traffic`` so the inbound
    counters stay accurate. Delivery into local channels is limited to the
    logged relay until the per-server channel-mapping layer exists — the
    message is acknowledged, accounted, and logged.
    """
    manager = _get_plexijoin_manager()
    body = await read_body_capped(request, MAX_FEDERATION_BODY)
    signature = request.headers.get("x-plexijoin-signature")
    timestamp = request.headers.get("x-plexijoin-timestamp")
    nonce = request.headers.get("x-plexijoin-nonce")
    conn = _authenticate(
        manager, body, signature, timestamp, nonce, statuses=("active",)
    )
    if not conn:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid federation signature"}},
        )

    payload = _parse_json_body(body)
    if not payload.get("message_type"):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "message_type is required"}},
        )

    try:
        manager.record_traffic(int(conn["id"]), "inbound", 1)
    except Exception as e:  # pragma: no cover - defensive accounting
        logger.debug("federation inbound: record_traffic failed: %s", e)

    logger.info(
        "federation inbound: %s message over connection %s from %s",
        payload.get("message_type"),
        conn.get("id"),
        conn.get("remote_instance_id"),
    )
    return {"ok": True}
