from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import (
    AutomodRuleResponse,
    AutomodRuleCreateRequest,
    AutomodRuleUpdateRequest,
    AutomodViolationResponse,
)
from src.api.schemas.common import SnowflakeID, SuccessResponse

import utils.logger as logger
from .helpers import _automod_rule_to_response

router = APIRouter()


@router.get(
    "/{server_id}/automod/rules",
    response_model=List[AutomodRuleResponse],
    summary="Get server automod rules",
)
async def get_server_automod_rules(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[AutomodRuleResponse]:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod

    try:
        sid = int(server_id)
        servers_mod.require_permission(current_user.user_id, sid, "server.automod")

        rules = automod.get_server_rules(sid)
        return [_automod_rule_to_response(r) for r in rules]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get automod rules for server {server_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod
    from src.core.automod.models import RuleType

    try:
        sid = int(server_id)
        servers_mod.require_permission(current_user.user_id, sid, "server.automod")

        try:
            rule_type = RuleType(body.rule_type)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid rule type")

        applied_roles = (
            [int(r) for r in body.applied_roles]
            if body.applied_roles is not None
            else None
        )
        exempt_roles = (
            [int(r) for r in body.exempt_roles]
            if body.exempt_roles is not None
            else None
        )
        exempt_channels = (
            [int(c) for c in body.exempt_channels]
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
        raise HTTPException(status_code=400, detail=str(e))


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
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod

    try:
        sid = int(server_id)
        rid = int(rule_id)
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
            update_kwargs["exempt_roles"] = [int(r) for r in body.exempt_roles]
        if body.applied_roles is not None:
            update_kwargs["applied_roles"] = [int(r) for r in body.applied_roles]
        if body.exempt_channels is not None:
            update_kwargs["exempt_channels"] = [int(c) for c in body.exempt_channels]
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
        raise HTTPException(status_code=400, detail=str(e))


@router.delete(
    "/{server_id}/automod/rules/{rule_id}",
    response_model=SuccessResponse,
    summary="Delete server automod rule",
)
async def delete_server_automod_rule(
    server_id: str, rule_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod

    try:
        sid = int(server_id)
        rid = int(rule_id)
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
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{server_id}/automod/violations",
    response_model=List[AutomodViolationResponse],
    summary="Get server automod violations",
)
async def get_server_automod_violations(
    server_id: str,
    user_id: Optional[str] = None,
    limit: int = 50,
    before: Optional[str] = None,
    current_user: TokenInfo = Depends(get_current_user),
) -> List[AutomodViolationResponse]:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod

    try:
        sid = int(server_id)
        servers_mod.require_permission(current_user.user_id, sid, "server.automod")

        target_user_id = int(user_id) if user_id else None
        before_id = int(before) if before else None

        violations = automod.get_violations(
            sid, user_id=target_user_id, limit=limit, before_id=before_id
        )

        return [
            AutomodViolationResponse(
                id=SnowflakeID(v.id),
                user_id=SnowflakeID(v.user_id),
                channel_id=SnowflakeID(v.channel_id),
                rule_id=SnowflakeID(v.rule_id),
                rule_type=v.rule_type.value
                if hasattr(v.rule_type, "value")
                else str(v.rule_type),
                matched_content=v.matched_content,
                severity=v.severity.value
                if hasattr(v.severity, "value")
                else str(v.severity),
                actions_taken=[
                    a.value if hasattr(a, "value") else str(a) for a in v.actions_taken
                ],
                created_at=v.created_at,
                metadata=v.metadata or {},
            )
            for v in violations
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get automod violations for server {server_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
