"""Item grants and download tickets."""

import hashlib
import time
from typing import Optional

import src.api as api
from fastapi import Depends, HTTPException, Query, Request, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.stash import (
    StashDownloadRedeemRequest,
    StashDownloadRedeemResponse,
    StashDownloadTicketResponse,
    StashGrantRequest,
    StashGrantResponse,
)
from src.core.auth.ip_canonical import canonicalize_ip_address
from src.core.database import check_rate_limit
from src.utils.net import get_client_ip
import utils.logger as logger
from .helpers import _error, parse_stash_id, resolve_manager as _manager
from .router import router
from .serializers import (
    _grant_response,
)


# --- Download-ticket redemption guess budget -----------------------------
# A download ticket is a bearer credential: whoever holds it can fetch the
# item's bytes. Redeeming it is the credential-check half of the ticket system,
# so online guessing must be bounded just like share-link password checks and
# invite-code redemption. Ticket *creation* requires the `download` permission
# and is route-limited, but redemption had only the permissive generic
# transport limiter: an authenticated caller could fire unbounded candidate
# codes at `resolve_download_ticket`. These counters are spent through the
# SHARED, atomic rate-limit backend so the budget holds across workers;
# `check_rate_limit` fails closed on a limiter fault.

#: Bounded, honest defaults. Operators may tune them under ``[stash]``:
#: ``redeem_guess_max`` / ``redeem_guess_window_seconds`` (per source) and
#: ``redeem_guess_global_max`` (whole instance; 0 disables the global floor).
#: The global ceiling is deliberately generous so normal download traffic is
#: never refused; it caps a botnet rotating source IPs past the per-IP budget.
_REDEEM_GUESS_MAX = 10
_REDEEM_GUESS_WINDOW_SECONDS = 60
_REDEEM_GUESS_GLOBAL_MAX = 600


def _redeem_guess_config() -> dict:
    try:
        import utils.config as config

        return config.get("stash", {}) or {}
    except Exception:
        return {}


def _positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _nonnegative_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _download_ticket_redeem_guess_key(user_id, ip_address: Optional[str]) -> str:
    """Authoritative per-source guess key: canonical IP first, user second.

    The IP is resolved from the trusted-proxy-aware extractor and canonicalised
    so every textual spelling of one host (IPv4-mapped IPv6, zone ids,
    compressed forms) shares ONE bucket. The user id is a secondary component
    so two accounts from the same host still share the host-level floor. The
    guessed ticket is deliberately NOT part of the key: including it would give
    every candidate its own fresh budget (no protection) and write the secret
    into the rate-limit store. Unknown/missing IPs fall back to a single shared
    bucket rather than becoming an unlimited path.
    """
    canonical = canonicalize_ip_address(ip_address)
    # An unparseable/hostile source string must not become a caller-chosen
    # bucket: fold it into the single shared "unknown" floor (fail closed).
    if not canonical:
        canonical = "unknown"
    else:
        import ipaddress

        try:
            ipaddress.ip_address(canonical)
        except ValueError:
            canonical = "unknown"
    # Fold the authoritative identity into a stable fingerprint so the bucket
    # key is a non-reversible digest of validated inputs, not a raw field.
    fingerprint = hashlib.sha256(f"{canonical}:{user_id}".encode("utf-8")).hexdigest()
    return (
        f"stash:download-ticket-redeem-guess:ip:{canonical}:user:{user_id}"
        f":fp:{fingerprint[:16]}"
    )


def _download_ticket_global_guess_key() -> str:
    """Stable non-owner fingerprint for the instance-wide guess floor."""
    fingerprint = hashlib.sha256(
        b"stash:download-ticket-redeem-guess:global"
    ).hexdigest()
    return f"stash:download-ticket-redeem-guess:global:{fingerprint[:16]}"


def _enforce_download_ticket_redeem_guess_limit(
    user_id, request: Optional[Request]
) -> None:
    """Spend one redemption guess; refuse uniformly once a budget is spent.

    Call this BEFORE ``resolve_download_ticket`` on every attempt so wrong
    guesses count too. A denial is a static 429 that never carries ticket-state
    information: the limiter runs before any lookup, so a throttled answer is
    identical whether the ticket was real-but-dead or never existed. Any
    limiter fault is treated as an exhausted budget (fail closed, never an
    unlimited path).
    """
    cfg = _redeem_guess_config()
    max_per_source = _positive_int(
        cfg.get("redeem_guess_max", _REDEEM_GUESS_MAX), _REDEEM_GUESS_MAX
    )
    window = _positive_int(
        cfg.get("redeem_guess_window_seconds", _REDEEM_GUESS_WINDOW_SECONDS),
        _REDEEM_GUESS_WINDOW_SECONDS,
    )
    global_max = _nonnegative_int(
        cfg.get("redeem_guess_global_max", _REDEEM_GUESS_GLOBAL_MAX),
        _REDEEM_GUESS_GLOBAL_MAX,
    )

    ip_address = None
    if request is not None:
        try:
            ip_address = get_client_ip(request)
        except Exception as exc:
            logger.debug(f"download ticket redeem guess IP resolution failed: {exc}")
            ip_address = None

    allowed = False
    try:
        allowed, _ = check_rate_limit(
            _download_ticket_redeem_guess_key(user_id, ip_address),
            max_per_source,
            window,
        )
        # A global floor bounds a distributed attacker rotating IPs/users so
        # the per-source budget cannot simply be multiplied by source count.
        if allowed and global_max > 0:
            allowed, _ = check_rate_limit(
                _download_ticket_global_guess_key(), global_max, window
            )
    except Exception as exc:
        # A store outage must not reopen the guessing corridor.
        logger.debug(f"download ticket redeem guess limiter failed: {exc}")
        allowed = False
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": 429,
                    "message": "Too many download ticket attempts",
                }
            },
        )


@router.post(
    "/items/{item_id}/grants",
    response_model=StashGrantResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def grant_item(
    item_id: str,
    body: StashGrantRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashGrantResponse:
    try:
        grant = _manager().grant(
            current_user.user_id,
            parse_stash_id(item_id, "item id"),
            int(body.subject_id),
            body.permissions,
            body.expires_at,
        )
        return _grant_response(StashGrantResponse, grant)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid Stash item id or subject id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/items/{item_id}/download-ticket",
    response_model=StashDownloadTicketResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid item or version id"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
    },
)
async def create_download_ticket(
    item_id: str,
    version_id: Optional[str] = Query(None, max_length=32),
    ttl_seconds: int = Query(300, ge=1, le=300),
    current_user: TokenInfo = Depends(get_current_user),
) -> StashDownloadTicketResponse:
    """Create a short-lived opaque ticket after checking download permission."""
    try:
        ticket = _manager().create_download_ticket(
            current_user.user_id,
            parse_stash_id(item_id, "item id"),
            parse_stash_id(version_id, "version id")
            if version_id is not None
            else None,
            ttl_seconds,
        )
        return StashDownloadTicketResponse.model_validate(ticket)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid item or version id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/download-ticket/redeem",
    response_model=StashDownloadRedeemResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        429: {"model": ErrorResponse, "description": "Too many redemption guesses"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def redeem_download_ticket(
    body: StashDownloadRedeemRequest,
    current_user: TokenInfo = Depends(get_current_user),
    request: Request = None,  # type: ignore[assignment]
) -> StashDownloadRedeemResponse:
    """Redeem a ticket once and recheck item authorization at use time."""
    try:
        # Bound online guessing of this bearer credential BEFORE any ticket
        # lookup. Wrong guesses consume the same budget as real attempts; the
        # shared counter is keyed on the authoritative canonical client IP (and
        # secondarily the validated user) and fails closed on limiter faults.
        _enforce_download_ticket_redeem_guess_limit(current_user.user_id, request)
        # Claim the one-time ticket FIRST with the atomic ``redeemed_at IS NULL``
        # CAS, so exactly one concurrent redemption becomes the single winner
        # BEFORE the expensive side effect (minting a signed download grant)
        # runs. The old ordering resolved (non-consuming) and signed the URL
        # first, then redeemed: two concurrent callers could both reach
        # ``sign_url`` and produce two usable signed grants from a ticket that
        # is strictly single-use. ``result`` carries the same target/expiry the
        # old resolve step returned. Signing after the claim means a transient
        # media-storage failure fails closed (the ticket is already burned)
        # rather than handing out an unbacked one-time capability.
        result = _manager().redeem_download_ticket(current_user.user_id, body.ticket)
        media_url = None
        media_object_id = result.get("media_object_id")
        if media_object_id:
            media = api.get_media()
            if media is None:
                raise HTTPException(
                    status_code=503, detail="Media storage is unavailable"
                )
            # Stash stores an opaque reference. Resolve filenames first;
            # numeric IDs remain supported for existing records. Never expose
            # a storage path or unresolved opaque reference as a usable URL.
            media_file = media.get_file_by_filename(str(media_object_id))
            if media_file is None and str(media_object_id).isdigit():
                media_file = media.get_file(int(media_object_id))
            if media_file is None:
                raise HTTPException(
                    status_code=502,
                    detail="Stash media object is unavailable",
                )
            # Bound the signed URL by the ticket's remaining lifetime so a
            # redeemed ticket cannot outlive its own expiry window (sign_url
            # caps at 1h).
            remaining_ms = result.get("expires_at", 0)
            now_ms = int(time.time() * 1000)
            expires_in = 3600
            if remaining_ms:
                expires_in = max(
                    1,
                    min(3600, (int(remaining_ms) - now_ms) // 1000),
                )
            signed = media.sign_url(
                int(media_file.id), expires_in=expires_in, user_id=current_user.user_id
            )
            media_url = signed.url

        result["media_url"] = media_url
        # The API returns only the safe signed URL, not the opaque storage
        # reference that was used to resolve it.
        result.pop("media_object_id", None)
        return StashDownloadRedeemResponse.model_validate(result)
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(exc) from exc


@router.delete(
    "/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def revoke_grant(
    grant_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    """Revoke a share grant; revoked grants cannot redeem future tickets."""
    try:
        _manager().revoke_grant(
            current_user.user_id, parse_stash_id(grant_id, "grant id")
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid grant id") from exc
    except Exception as exc:
        raise _error(exc) from exc
