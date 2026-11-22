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
from src.core.security.hardening import (
    SNOWFLAKE_MAX,
    HardeningViolation,
    bounded_int,
    require_https_url,
)
from src.core.security.outbound import (
    OutboundUrlRejected,
    parse_outbound_url,
    reject_forbidden_ip_literal,
)
from src.utils.security import pinned_request_target

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urljoin, urlparse

from fastapi import APIRouter, HTTPException, Request

import re

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

    The shared outbound gate is the only place allowed to run the IP rules; it
    covers loopback, link-local (the ``169.254.0.0/16`` metadata range), CGNAT,
    NAT64, IPv4-mapped IPv6, multicast, reserved and unspecified addresses.
    Running it on literals before DNS (and the gate then re-checking the
    resolved address) means an obfuscated numeric host that a resolver accepts
    cannot slip past.
    """
    try:
        reject_forbidden_ip_literal(host, field)
    except OutboundUrlRejected as exc:
        raise FederationEndpointRejected(
            f"{field} targets a non-public address"
        ) from exc


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
        resolved = parse_outbound_url(candidate, field)
    except OutboundUrlRejected as exc:
        # Log the reason server-side; the caller sees only the field name.
        logger.warning("Rejected federation %s failing SSRF check: %s", field, exc)
        raise FederationEndpointRejected(
            f"{field} resolves to a forbidden address"
        ) from exc
    return ResolvedEndpoint(
        url=resolved.url, hostname=resolved.hostname, address=resolved.address
    )


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
    endpoint = resolve_remote_endpoint(initial_url, field, require_https=require_https)
    visited = {endpoint.url}
    for index, location in enumerate(locations):
        if index >= MAX_REDIRECTS:
            raise FederationEndpointRejected(f"{field} exceeded the redirect limit")
        target = urljoin(endpoint.url, str(location or ""))
        if target in visited:
            raise FederationEndpointRejected(f"{field} redirect loop")
        visited.add(target)
        endpoint = resolve_remote_endpoint(target, field, require_https=require_https)
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


# --- Strict format/range checks for federation identifiers ---------------
#
# Remote ids, op/message types and invite codes all arrive inside a signed
# envelope, but a valid signature only proves the peer sent them — it does not
# make the values safe to persist, log, or use in a lookup. Bind each to a
# restrictive character class and length, and numeric ids to the snowflake
# range, so a peer cannot smuggle control characters, oversized strings or
# sentinel integers past the boundary.

MAX_REMOTE_ID_LENGTH = 128
MAX_INVITE_CODE_LENGTH = 64
MAX_TYPE_LENGTH = 128

# Use ``fullmatch`` (never ``match`` with a ``$`` anchor): in Python ``$`` also
# matches immediately before a trailing newline, so a value such as ``"code\n"``
# (or a max-length value with one extra newline) used to pass the invite-code
# and type validators, which see the raw value, and carry a control character
# into a replayed message, a log line or a lookup. The restrictive classes below
# exclude every control character in full, but only when the *entire* string is
# matched. ``validate_remote_id`` strips its input first (so it was not
# directly bypassable), but uses ``fullmatch`` too for a uniform contract. The
# PlexiJoin token validator made the same switch for exactly this reason.
_REMOTE_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_INVITE_CODE_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_TYPE_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


def _bad_field(field: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": {"code": 400, "message": f"Invalid {field}"}},
    )


def validate_remote_id(value: Any, field: str = "instance_id") -> str:
    """Return a bounded remote instance id free of control characters."""
    if not isinstance(value, str):
        raise _bad_field(field)
    text = value.strip()
    if (
        not text
        or len(text) > MAX_REMOTE_ID_LENGTH
        or not _REMOTE_ID_RE.fullmatch(text)
    ):
        raise _bad_field(field)
    return text


def validate_invite_code(value: Any, field: str = "invite_code") -> str:
    """Return a well-formed invite code or reject it."""
    if not isinstance(value, str) or not _INVITE_CODE_RE.fullmatch(value):
        raise _bad_field(field)
    return value


def validate_numeric_id(value: Any, field: str, *, minimum: int = 1) -> int:
    """Parse a numeric federation id inside the snowflake range."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _bad_field(field)
    try:
        return bounded_int(value, field, minimum=minimum, maximum=SNOWFLAKE_MAX)
    except (ValueError, TypeError, HardeningViolation):
        raise _bad_field(field)


def validate_type_field(value: Any, field: str) -> str:
    """Return a bounded op/message type token."""
    if not isinstance(value, str) or not _TYPE_RE.fullmatch(value):
        raise _bad_field(field)
    return value


def validate_payload_text(value: Any, field: str, max_length: int) -> Optional[str]:
    """Bound an optional signed text field, failing with a 400, never a 500.

    ``_bound_payload_value`` proves only that a value is a *bounded* JSON
    scalar — a signed envelope can legally carry ``{"requested_by": 7}`` or an
    oversized ``note``. ``submit_inbound_request`` then calls ``bound_text``,
    which raises ``HardeningViolation`` (a plain ``ValueError``) for a non-str
    or over-length value. On this unauthenticated-by-JWT route that escaped as
    an unhandled exception and a 500 instead of a validation rejection. Apply
    the same bound here and translate the failure to the route's 400 contract;
    this tightens the boundary without relaxing the stored field's limits.
    """
    from src.core.plexijoin.validation import bound_text

    try:
        return bound_text(value, field, max_length)
    except HardeningViolation:
        raise _bad_field(field)


def validate_invite_codes(value: Any, field: str = "payload") -> None:
    """Recursively validate any ``invite_code`` fields in a signed payload."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and "invite_code" in key:
                validate_invite_code(item, key)
            else:
                validate_invite_codes(item, key if isinstance(key, str) else field)
    elif isinstance(value, (list, tuple)):
        for item in value:
            validate_invite_codes(item, field)


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


# The x-plexijoin-* headers are the ONLY credential on the inbound federation
# routes (there is no local JWT). They must therefore be checked to a canonical
# shape before they reach signature verification: ``hmac.compare_digest`` raises
# ``TypeError`` on a non-ASCII str and ``str.encode('ascii')`` raises
# ``UnicodeEncodeError`` on one, both of which used to escape as a 500 on a
# public endpoint instead of a clean 401. Accepting non-canonical spellings
# (leading zeros, PEP 515 underscores, uppercase hex) also let one logical
# envelope have several byte-level representations in the HMAC material and the
# replay-nonce key. Pin every value to the exact grammar the transports emit.
_SIGNATURE_RE = re.compile(r"[0-9a-f]{64}")
_TIMESTAMP_RE = re.compile(r"[0-9]{1,10}")
_NONCE_RE = re.compile(r"[A-Za-z0-9_-]{1,256}")
_MAX_FEDERATION_TIMESTAMP = 4_102_444_800


def _valid_signature_headers(
    signature: Optional[str],
    timestamp: Optional[str],
    nonce: Optional[str],
) -> bool:
    """Only canonical signature envelopes reach the HMAC and replay gates."""
    if not signature or not timestamp or not nonce:
        return False
    if not isinstance(signature, str) or not _SIGNATURE_RE.fullmatch(signature):
        return False
    if not isinstance(timestamp, str) or not _TIMESTAMP_RE.fullmatch(timestamp):
        return False
    if int(timestamp) > _MAX_FEDERATION_TIMESTAMP:
        return False
    if not isinstance(nonce, str) or not _NONCE_RE.fullmatch(nonce):
        return False
    return True


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

    if timestamp is None or not _valid_signature_headers(signature, timestamp, nonce):
        return None
    # Header shapes are pinned above, so these conversions cannot raise.
    timestamp_int = int(timestamp)
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


def _invalid_payload() -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": {"code": 400, "message": "Invalid federation payload"}},
    )


# A signed envelope is small and flat. Bound both the raw body (in
# ``read_body_capped``) and the parsed structure so a valid HMAC over a huge or
# deeply nested JSON document cannot force pathological parsing/allocation on
# the receiver.
MAX_PAYLOAD_KEYS = 128
MAX_PAYLOAD_SEQUENCE = 256
MAX_PAYLOAD_NESTING = 8
MAX_PAYLOAD_TEXT = 4096
MAX_PAYLOAD_KEY_LENGTH = 128
MAX_PAYLOAD_INT = 2**63 - 1


def _bound_payload_value(value: Any, depth: int = 0) -> None:
    if depth > MAX_PAYLOAD_NESTING:
        raise _invalid_payload()
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > MAX_PAYLOAD_INT:
            raise _invalid_payload()
        return
    if isinstance(value, float):
        # Python's ``json.loads`` accepts the non-standard ``NaN``,
        # ``Infinity`` and ``-Infinity`` literals (and a huge exponent such as
        # ``1e400`` parses to ``inf``), so a signed envelope can carry them
        # despite not being valid RFC 8259 JSON. They are not bounded by the
        # integer check above and previously sailed through into the relayed
        # op and application logs; re-serialising them emits bare ``NaN`` /
        # ``Infinity`` tokens that strict JSON parsers (including browser
        # clients) reject, a parser differential. Refuse them at the boundary.
        if not math.isfinite(value):
            raise _invalid_payload()
        return
    if isinstance(value, str):
        if len(value) > MAX_PAYLOAD_TEXT:
            raise _invalid_payload()
        return
    if isinstance(value, dict):
        if len(value) > MAX_PAYLOAD_KEYS:
            raise _invalid_payload()
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > MAX_PAYLOAD_KEY_LENGTH:
                raise _invalid_payload()
            _bound_payload_value(item, depth + 1)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_PAYLOAD_SEQUENCE:
            raise _invalid_payload()
        for item in value:
            _bound_payload_value(item, depth + 1)
        return
    raise _invalid_payload()


def _require_json_content_type(request: Request) -> None:
    """Reject non-JSON bodies *before* they are read or parsed.

    Federation peers sign a JSON envelope; anything else (HTML, form data, an
    uploaded file) is either a probe or an attempt to make a parser consume a
    different content type than the signature covered.
    """
    raw = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if raw != "application/json" and not (
        raw.startswith("application/") and raw.endswith("+json")
    ):
        raise HTTPException(
            status_code=415,
            detail={
                "error": {
                    "code": 415,
                    "message": "Federation payloads must be application/json",
                }
            },
        )


def _parse_json_body(body: bytes) -> Dict[str, Any]:
    import json

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        raise _invalid_payload()
    if not isinstance(payload, dict):
        raise _invalid_payload()
    _bound_payload_value(payload)
    return payload


async def _read_federation_body(request: Request) -> bytes:
    """Enforce the content type, then cap and return the raw HMAC-covered body.

    Authentication still happens before JSON parsing; the content type is
    checked first so an unexpected body never reaches a parser at all.
    """
    _require_json_content_type(request)
    return await read_body_capped(request, MAX_FEDERATION_BODY)


async def _relay_op(artifact_id: int, op, actor_id: int) -> int:
    """Relay a validated op.

    Callers must pass ids already checked by :func:`validate_numeric_id`. The
    former bare ``int()`` here accepted zero, negatives and Python-only
    spellings such as ``"1_2"`` (parsed as 12) — a signed envelope could name
    one artifact/actor but have a different one looked up and broadcast to
    local subscribers.
    """
    from src.api.websocket import get_dispatcher
    from src.api.websocket.artifacts import relay_artifact_op

    dispatcher = get_dispatcher()
    if dispatcher is None:
        return 0
    return await relay_artifact_op(
        dispatcher,
        artifact_id,
        op,
        actor_id,
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
    body = await _read_federation_body(request)
    signature = request.headers.get("x-plexijoin-signature")
    timestamp = request.headers.get("x-plexijoin-timestamp")
    nonce = request.headers.get("x-plexijoin-nonce")
    if not _authenticate(manager, body, signature, timestamp, nonce):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid federation signature"}},
        )

    payload = _parse_json_body(body)

    op = payload.get("op")
    if payload.get("artifact_id") is None or not isinstance(op, dict):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Malformed artifact op payload"}},
        )
    # A valid HMAC proves the peer sent this op; it does not make the op token
    # safe to relay. ``message_inbound`` already bounds its ``message_type``
    # this way, but the op path only checked truthiness — a signed envelope
    # could carry a non-string or control-character-bearing ``op_type`` and
    # have it broadcast verbatim to local artifact subscribers. Bind it to the
    # same restrictive token grammar before the op reaches the relay.
    validate_type_field(op.get("op_type"), "op_type")

    # A valid HMAC only proves the peer sent these values; it does not make
    # them safe to use. ``int()`` accepted negatives, zero and Python-only
    # spellings (``"1_2"`` -> 12), which then reached the artifact lookup and
    # the relayed event as a *different* id than the peer wrote. Route both
    # ids through the snowflake guard so a signed envelope cannot spoof the
    # target artifact or actor.
    artifact_id = validate_numeric_id(payload.get("artifact_id"), "artifact_id")
    actor_id = validate_numeric_id(payload.get("actor_id", 0), "actor_id", minimum=0)

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
    body = await _read_federation_body(request)
    signature = request.headers.get("x-plexijoin-signature")
    timestamp = request.headers.get("x-plexijoin-timestamp")
    nonce = request.headers.get("x-plexijoin-nonce")
    if not _authenticate(manager, body, signature, timestamp, nonce):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid federation signature"}},
        )

    payload = _parse_json_body(body)

    # A valid HMAC proves which peer sent the event, not that its fields are
    # safe to use. ``message_inbound`` binds ``message_type`` and
    # ``artifact_op_inbound`` binds ``op_type`` to the restrictive token
    # grammar, but this route copied ``event_type`` straight into an
    # application log line. A signed envelope carrying a control character
    # (e.g. ``"evt\nFORGED"``) or an oversized value could forge a log entry in
    # any log handler that does not scrub, or blow up log storage. Bind it to
    # the same grammar as its sibling routes before it reaches the logger.
    validate_type_field(payload.get("event_type"), "event_type")

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
    body = await _read_federation_body(request)
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

    # A valid HMAC proves which peer sent the join request, not that the
    # instance id it claims is safe to persist and later show to an admin for
    # approval. ``bound_text`` in the manager caps length and strips control
    # characters, but an instance id is an identifier, not free text: bind it
    # to the same restrictive grammar the outbound validators use so Unicode
    # look-alikes, whitespace and punctuation cannot be smuggled into a stored
    # request, an admin review screen or an exact-match connection lookup.
    remote_instance_id = validate_remote_id(payload.get("instance_id"), "instance_id")

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

    # ``requested_by`` and ``note`` come straight from the signed envelope.
    # A valid HMAC does not make their types safe: bound them to the same
    # limits ``submit_inbound_request`` enforces and reject bad values with a
    # clean 400 rather than letting its HardeningViolation surface as a 500.
    requested_by = validate_payload_text(
        payload.get("requested_by"), "requested_by", 128
    )
    note = validate_payload_text(payload.get("note"), "note", 500)

    row = manager.submit_inbound_request(
        remote_instance_id=remote_instance_id,
        remote_url=remote_url,
        requested_by=requested_by,
        note=note,
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
    body = await _read_federation_body(request)
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
    validate_type_field(payload.get("message_type"), "message_type")
    validate_invite_codes(payload)

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
