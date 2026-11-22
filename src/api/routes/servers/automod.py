from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.routes.users.helpers import require_user_token
from src.api.schemas.servers import (
    AutomodRuleResponse,
    AutomodRuleCreateRequest,
    AutomodRuleUpdateRequest,
    AutomodViolationResponse,
)
from src.api.schemas.common import SnowflakeID, SuccessResponse
from src.core.security.hardening import HardeningViolation, parse_id

import utils.logger as logger
from .helpers import _automod_rule_to_response
from ..exception_mapping import raise_mapped

router = APIRouter()


# A violation's ``matched_content`` (and content-bearing ``metadata`` such as
# regex/repeated examples) quotes the offending message body.  ``server.automod``
# alone is not authority to read it: a moderator can hold that server-wide bit
# while a channel override denies view/read on the specific private channel the
# violation occurred in -- the audience N711/N749 already exclude from the
# alert's notification and channel alert.  The audit-log read endpoints are a
# third destination for the same body, so apply the same per-channel gate here.
_VIOLATION_BODY_PERMISSIONS = ("channels.view", "messages.read")


def _viewer_can_read_violation_body(
    servers_mod, viewer_id, server_id, channel_id
) -> bool:
    """Whether ``viewer_id`` may see a violation's offending body/metadata.

    Fail closed: an unwired store, a missing/unparseable channel id, or any
    error from the permission lookup all strip the content.
    """
    if not channel_id:
        return False
    try:
        cid = int(channel_id)
    except (TypeError, ValueError):
        return False
    if not cid:
        return False
    for permission in _VIOLATION_BODY_PERMISSIONS:
        try:
            if not servers_mod.has_permission(viewer_id, server_id, permission, cid):
                return False
        except Exception:
            return False
    return True


def _violation_response(violation, *, viewer_id, servers_mod, server_id):
    """Serialize a violation, redacting the body when the viewer cannot read it."""
    can_read = _viewer_can_read_violation_body(
        servers_mod, viewer_id, server_id, violation.channel_id
    )
    rule_type = violation.rule_type
    severity = violation.severity
    return AutomodViolationResponse(
        id=SnowflakeID(violation.id),
        user_id=SnowflakeID(violation.user_id),
        channel_id=SnowflakeID(violation.channel_id),
        rule_id=SnowflakeID(violation.rule_id),
        rule_type=rule_type.value if hasattr(rule_type, "value") else str(rule_type),
        matched_content=violation.matched_content if can_read else "",
        severity=severity.value if hasattr(severity, "value") else str(severity),
        actions_taken=[
            a.value if hasattr(a, "value") else str(a) for a in violation.actions_taken
        ],
        created_at=violation.created_at,
        metadata=(violation.metadata or {}) if can_read else {},
    )


def _parse_id(value: object, label: str) -> int:
    """Parse an untrusted route/query id with the shared ASCII snowflake gate.

    Every id in this module used to go through bare ``int()``. Python's ``int``
    accepts Unicode decimal homoglyphs (``"１２"`` -> 12), PEP 515 separators
    (``"1_2"`` -> 12), a leading sign/zero and arbitrarily large integers. Those
    spellings reached ``require_permission``, rule/violation lookups and storage
    as a *different* identifier than the caller wrote, and disagreed with every
    other route boundary, which parses ids with ``parse_id``. Route them all
    through the canonical gate so validation and consumption share a single
    interpretation of the token; non-canonical ids fail with 400 before any
    permission check or service call.
    """
    try:
        return parse_id(value, label)
    except HardeningViolation as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": f"Invalid {label}"}},
        ) from exc


@router.get(
    "/{server_id}/automod/rules",
    response_model=List[AutomodRuleResponse],
    summary="Get server automod rules",
)
async def get_server_automod_rules(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[AutomodRuleResponse]:
    require_user_token(current_user)
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod

    try:
        sid = _parse_id(server_id, "server ID")
        servers_mod.require_permission(current_user.user_id, sid, "server.automod")

        rules = automod.get_server_rules(sid)
        return [_automod_rule_to_response(r) for r in rules]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get automod rules for server {server_id}: {e}")
        raise_mapped(
            e,
            not_found_message="Server not found",
            permission_message="Access denied",
            invalid_message="Invalid request",
        )


@router.post(
    "/{server_id}/automod/rules",
    response_model=AutomodRuleResponse,
    summary="Create server automod rule",
)
async def create_server_automod_rule(
    server_id: str,
    body: AutomodRuleCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> AutomodRuleResponse:
    require_user_token(current_user)
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod
    from src.core.automod.models import RuleType

    try:
        sid = _parse_id(server_id, "server ID")
        servers_mod.require_permission(current_user.user_id, sid, "server.automod")

        try:
            rule_type = RuleType(body.rule_type)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid rule type")

        applied_roles = (
            [_parse_id(r, "applied role ID") for r in body.applied_roles]
            if body.applied_roles is not None
            else None
        )
        exempt_roles = (
            [_parse_id(r, "exempt role ID") for r in body.exempt_roles]
            if body.exempt_roles is not None
            else None
        )
        exempt_channels = (
            [_parse_id(c, "exempt channel ID") for c in body.exempt_channels]
            if body.exempt_channels is not None
            else None
        )

        rule = automod.create_rule(
            user_id=current_user.user_id,
            server_id=sid,
            name=body.name,
            rule_type=rule_type,
            rule_config=body.config,
            actions=[a.model_dump() for a in body.actions],
            applied_roles=applied_roles,
            exempt_roles=exempt_roles,
            exempt_channels=exempt_channels,
            priority=body.priority or 0,
            check_all=bool(body.check_all),
        )

        if body.enabled is False:
            automod.set_rule_enabled(current_user.user_id, rule.id, False)
            rule = automod.get_rule(rule.id)

        return _automod_rule_to_response(rule)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create automod rule for server {server_id}: {e}")
        raise_mapped(
            e,
            not_found_message="Server not found",
            permission_message="Access denied",
            invalid_message="Invalid request",
        )


@router.patch(
    "/{server_id}/automod/rules/{rule_id}",
    response_model=AutomodRuleResponse,
    summary="Update server automod rule",
)
async def update_server_automod_rule(
    server_id: str,
    rule_id: str,
    body: AutomodRuleUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> AutomodRuleResponse:
    require_user_token(current_user)
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod

    try:
        sid = _parse_id(server_id, "server ID")
        rid = _parse_id(rule_id, "rule ID")
        servers_mod.require_permission(current_user.user_id, sid, "server.automod")

        rule = automod.get_rule(rid)
        if not rule or int(rule.server_id) != sid:
            raise HTTPException(status_code=404, detail="Rule not found in this server")

        update_kwargs: Dict[str, Any] = {}
        if body.name is not None:
            update_kwargs["name"] = body.name
        if body.config is not None:
            update_kwargs["rule_config"] = body.config
        if body.actions is not None:
            update_kwargs["actions"] = [a.model_dump() for a in body.actions]
        if body.exempt_roles is not None:
            update_kwargs["exempt_roles"] = [
                _parse_id(r, "exempt role ID") for r in body.exempt_roles
            ]
        if body.applied_roles is not None:
            update_kwargs["applied_roles"] = [
                _parse_id(r, "applied role ID") for r in body.applied_roles
            ]
        if body.exempt_channels is not None:
            update_kwargs["exempt_channels"] = [
                _parse_id(c, "exempt channel ID") for c in body.exempt_channels
            ]
        if body.priority is not None:
            update_kwargs["priority"] = body.priority
        if body.check_all is not None:
            update_kwargs["check_all"] = body.check_all

        if update_kwargs:
            automod.update_rule(
                user_id=current_user.user_id, rule_id=rid, **update_kwargs
            )

        if body.enabled is not None:
            automod.set_rule_enabled(current_user.user_id, rid, body.enabled)

        rule = automod.get_rule(rid)
        return _automod_rule_to_response(rule)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update automod rule {rule_id}: {e}")
        raise_mapped(
            e,
            not_found_message="Rule not found",
            permission_message="Access denied",
            invalid_message="Invalid request",
        )


@router.delete(
    "/{server_id}/automod/rules/{rule_id}",
    response_model=SuccessResponse,
    summary="Delete server automod rule",
)
async def delete_server_automod_rule(
    server_id: str, rule_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    require_user_token(current_user)
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod

    try:
        sid = _parse_id(server_id, "server ID")
        rid = _parse_id(rule_id, "rule ID")
        servers_mod.require_permission(current_user.user_id, sid, "server.automod")

        rule = automod.get_rule(rid)
        if not rule or int(rule.server_id) != sid:
            raise HTTPException(status_code=404, detail="Rule not found in this server")

        if not automod.delete_rule(user_id=current_user.user_id, rule_id=rid):
            raise HTTPException(status_code=500, detail="Failed to delete rule")

        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete automod rule {rule_id}: {e}")
        raise_mapped(
            e,
            not_found_message="Rule not found",
            permission_message="Access denied",
            invalid_message="Invalid request",
        )


@router.get(
    "/{server_id}/automod/violations",
    response_model=List[AutomodViolationResponse],
    summary="Get server automod violations",
)
async def get_server_automod_violations(
    server_id: str,
    user_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    before: Optional[str] = None,
    current_user: TokenInfo = Depends(get_current_user),
) -> List[AutomodViolationResponse]:
    require_user_token(current_user)
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod

    try:
        sid = _parse_id(server_id, "server ID")
        servers_mod.require_permission(current_user.user_id, sid, "server.automod")

        target_user_id = _parse_id(user_id, "user ID") if user_id else None
        before_id = _parse_id(before, "before ID") if before else None

        violations = automod.get_violations(
            sid, user_id=target_user_id, limit=limit, before_id=before_id
        )

        return [
            _violation_response(
                v,
                viewer_id=current_user.user_id,
                servers_mod=servers_mod,
                server_id=sid,
            )
            for v in violations
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get automod violations for server {server_id}: {e}")
        raise_mapped(
            e,
            not_found_message="Server not found",
            permission_message="Access denied",
            invalid_message="Invalid request",
        )


@router.get(
    "/{server_id}/automod/violations/{violation_id}",
    response_model=AutomodViolationResponse,
    summary="Get a single server automod violation",
)
async def get_server_automod_violation(
    server_id: str,
    violation_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> AutomodViolationResponse:
    require_user_token(current_user)
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod

    try:
        sid = _parse_id(server_id, "server ID")
        vid = _parse_id(violation_id, "violation ID")
        servers_mod.require_permission(current_user.user_id, sid, "server.automod")

        violation = automod.get_violation(vid, server_id=sid)
        if violation is None:
            raise HTTPException(status_code=404, detail="Violation not found")

        return _violation_response(
            violation,
            viewer_id=current_user.user_id,
            servers_mod=servers_mod,
            server_id=sid,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to get automod violation {violation_id} for server {server_id}: {e}"
        )
        raise_mapped(
            e,
            not_found_message="Violation not found",
            permission_message="Access denied",
            invalid_message="Invalid request",
        )
