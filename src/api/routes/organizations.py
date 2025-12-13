"""
Organizations API routes - Endpoints for organization management.

Provides endpoints for:
- Organization info and management
- Member management (root only)
- Invite management
- Managed settings
- Server restrictions
"""

from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, Field
from typing import Optional, List

import utils.logger as logger

from src.api.middleware.authentication import get_current_user, TokenInfo

router = APIRouter()


# === Request/Response Models ===

class CreateOrgRequest(BaseModel):
    """Request to create an organization."""
    name: str = Field(..., min_length=2, max_length=32, pattern="^[a-z0-9_-]+$")
    display_name: str = Field(..., min_length=1, max_length=100)


class OrgResponse(BaseModel):
    """Organization response."""
    id: str
    name: str
    display_name: str
    root_user_id: str
    is_default: bool
    created_at: int
    allow_invites: bool
    member_count: Optional[int] = None


class MemberResponse(BaseModel):
    """Organization member response."""
    id: str
    user_id: str
    username: str
    role: str
    joined_at: int


class CreateInviteRequest(BaseModel):
    """Request to create an invite."""
    invite_type: str = Field(..., pattern="^(existing|registration)$")
    target_username: Optional[str] = None
    expires_hours: Optional[int] = Field(None, ge=1, le=720)


class InviteResponse(BaseModel):
    """Invite response."""
    id: str
    org_id: str
    code: str
    invite_type: str
    target_username: Optional[str]
    created_at: int
    expires_at: Optional[int]
    status: str
    user_accepted: bool
    root_approved: bool


class ManagedSettingRequest(BaseModel):
    """Request to set a managed setting."""
    value: str = Field(..., max_length=10000)
    locked: bool = True


class ManagedSettingResponse(BaseModel):
    """Managed setting response."""
    key: str
    value: Optional[str]
    locked: bool


class ServerRestrictionsRequest(BaseModel):
    """Request to update server restrictions."""
    default_servers: Optional[List[str]] = None
    allowed_servers: Optional[List[str]] = None
    blocked_servers: Optional[List[str]] = None


class ResetPasswordRequest(BaseModel):
    """Request to reset a user's password."""
    new_password: str = Field(..., min_length=8, max_length=128)


# === Helper Functions ===

def _get_orgs_module():
    """Get organizations module."""
    try:
        from src.core import organizations
        if organizations.is_setup():
            return organizations
    except ImportError:
        pass
    return None


def _check_org_root(orgs, org_id: int, user_id: int) -> None:
    """Check if user is org root."""
    if not orgs.is_org_root(org_id, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": "Only organization root can perform this action"}}
        )


# === Organization Info Endpoints ===

@router.get("/@me", response_model=OrgResponse)
async def get_my_org(current_user: TokenInfo = Depends(get_current_user)):
    """Get current user's organization."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        org = orgs.get_user_org(current_user.user_id)
        if not org:
            org = orgs.get_default_org()

        members = orgs.get_org_members(org.id)

        return OrgResponse(
            id=str(org.id),
            name=org.name,
            display_name=org.display_name,
            root_user_id=str(org.root_user_id),
            is_default=org.is_default,
            created_at=org.created_at,
            allow_invites=org.allow_invites,
            member_count=len(members)
        )
    except Exception as e:
        logger.error(f"Failed to get user org: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("", response_model=OrgResponse)
async def create_organization(
    body: CreateOrgRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Create a new organization (requires can_create_org feature)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        org = orgs.create_org(body.name, body.display_name, current_user.user_id)

        return OrgResponse(
            id=str(org.id),
            name=org.name,
            display_name=org.display_name,
            root_user_id=str(org.root_user_id),
            is_default=org.is_default,
            created_at=org.created_at,
            allow_invites=org.allow_invites,
            member_count=1
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "FeatureRequired" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": str(e)}}
            )
        elif "Exists" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": {"code": 409, "message": str(e)}}
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": str(e)}}
            )
        logger.error(f"Failed to create org: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get("/{org_id}", response_model=OrgResponse)
async def get_organization(
    org_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Get organization by ID (must be a member)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        oid = int(org_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid organization ID"}}
        )

    try:
        org = orgs.get_org(oid)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Organization not found"}}
            )

        # Check if user is member
        member = orgs.get_member(oid, current_user.user_id)
        if not member and not current_user.permissions.get("administrator", False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": "Not a member of this organization"}}
            )

        members = orgs.get_org_members(oid)

        return OrgResponse(
            id=str(org.id),
            name=org.name,
            display_name=org.display_name,
            root_user_id=str(org.root_user_id),
            is_default=org.is_default,
            created_at=org.created_at,
            allow_invites=org.allow_invites,
            member_count=len(members)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get org {org_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


# === Member Management Endpoints ===

@router.get("/{org_id}/members", response_model=List[MemberResponse])
async def get_org_members(
    org_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Get all members of an organization (root only)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        oid = int(org_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid organization ID"}}
        )

    try:
        _check_org_root(orgs, oid, current_user.user_id)

        members = orgs.get_org_members(oid)

        return [
            MemberResponse(
                id=str(m.id),
                user_id=str(m.user_id),
                username=m.username or "",
                role=m.role.value,
                joined_at=m.joined_at
            )
            for m in members
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get members for org {org_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/{org_id}/members/{user_id}/reset-password")
async def reset_member_password(
    org_id: str,
    user_id: str,
    body: ResetPasswordRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Reset a member's password (root only)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid ID"}}
        )

    try:
        orgs.reset_user_password(current_user.user_id, uid, body.new_password)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "Permission" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": str(e)}}
            )
        elif "NotFound" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": str(e)}}
            )
        logger.error(f"Failed to reset password: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/{org_id}/members/{user_id}/lock")
async def lock_member(
    org_id: str,
    user_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Lock a member's account (root only)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid user ID"}}
        )

    try:
        orgs.lock_user(current_user.user_id, uid)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "Permission" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": str(e)}}
            )
        logger.error(f"Failed to lock user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/{org_id}/members/{user_id}/unlock")
async def unlock_member(
    org_id: str,
    user_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Unlock a member's account (root only)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid user ID"}}
        )

    try:
        orgs.unlock_user(current_user.user_id, uid)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "Permission" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": str(e)}}
            )
        logger.error(f"Failed to unlock user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/{org_id}/members/{user_id}/force-logout")
async def force_logout_member(
    org_id: str,
    user_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Force logout all sessions for a member (root only)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid user ID"}}
        )

    try:
        count = orgs.force_logout(current_user.user_id, uid)
        return {"success": True, "sessions_revoked": count}
    except Exception as e:
        exc_name = type(e).__name__
        if "Permission" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": str(e)}}
            )
        logger.error(f"Failed to force logout: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/{org_id}/members/{user_id}/disinherit")
async def disinherit_member(
    org_id: str,
    user_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Remove member from org and move to default org (root only)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid user ID"}}
        )

    try:
        orgs.disinherit_user(current_user.user_id, uid)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "Permission" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": str(e)}}
            )
        logger.error(f"Failed to disinherit user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


# === Invite Management Endpoints ===

@router.post("/{org_id}/invites", response_model=InviteResponse)
async def create_invite(
    org_id: str,
    body: CreateInviteRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Create an organization invite (root only)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        oid = int(org_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid organization ID"}}
        )

    try:
        invite = orgs.create_invite(
            oid,
            current_user.user_id,
            body.invite_type,
            body.target_username,
            body.expires_hours
        )

        return InviteResponse(
            id=str(invite.id),
            org_id=str(invite.org_id),
            code=invite.code,
            invite_type=invite.invite_type,
            target_username=invite.target_username,
            created_at=invite.created_at,
            expires_at=invite.expires_at,
            status=invite.status,
            user_accepted=invite.user_accepted,
            root_approved=invite.root_approved
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "Permission" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": str(e)}}
            )
        elif "NotFound" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": str(e)}}
            )
        logger.error(f"Failed to create invite: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get("/{org_id}/invites", response_model=List[InviteResponse])
async def get_org_invites(
    org_id: str,
    status_filter: Optional[str] = None,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Get all invites for an organization (root only)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        oid = int(org_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid organization ID"}}
        )

    try:
        _check_org_root(orgs, oid, current_user.user_id)

        invites = orgs.get_org_invites(oid, status_filter)

        return [
            InviteResponse(
                id=str(inv.id),
                org_id=str(inv.org_id),
                code=inv.code,
                invite_type=inv.invite_type,
                target_username=inv.target_username,
                created_at=inv.created_at,
                expires_at=inv.expires_at,
                status=inv.status,
                user_accepted=inv.user_accepted,
                root_approved=inv.root_approved
            )
            for inv in invites
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get invites: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete("/{org_id}/invites/{invite_id}")
async def delete_invite(
    org_id: str,
    invite_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Delete/cancel an invite (root only)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        iid = int(invite_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid invite ID"}}
        )

    try:
        orgs.delete_invite(iid, current_user.user_id)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "Permission" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": str(e)}}
            )
        elif "NotFound" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": str(e)}}
            )
        logger.error(f"Failed to delete invite: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/{org_id}/invites/{invite_id}/approve")
async def approve_invite(
    org_id: str,
    invite_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Approve an invite (root only, step 2 of 2-step flow)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        iid = int(invite_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid invite ID"}}
        )

    try:
        invite = orgs.approve_invite(iid, current_user.user_id)

        return InviteResponse(
            id=str(invite.id),
            org_id=str(invite.org_id),
            code=invite.code,
            invite_type=invite.invite_type,
            target_username=invite.target_username,
            created_at=invite.created_at,
            expires_at=invite.expires_at,
            status=invite.status,
            user_accepted=invite.user_accepted,
            root_approved=invite.root_approved
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "Permission" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": str(e)}}
            )
        elif "NotFound" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": str(e)}}
            )
        logger.error(f"Failed to approve invite: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


# === User Invite Endpoints ===

@router.get("/users/@me/org-invites", response_model=List[InviteResponse])
async def get_my_pending_invites(current_user: TokenInfo = Depends(get_current_user)):
    """Get pending organization invites for current user."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        invites = orgs.get_user_pending_invites(current_user.user_id)

        return [
            InviteResponse(
                id=str(inv.id),
                org_id=str(inv.org_id),
                code=inv.code,
                invite_type=inv.invite_type,
                target_username=inv.target_username,
                created_at=inv.created_at,
                expires_at=inv.expires_at,
                status=inv.status,
                user_accepted=inv.user_accepted,
                root_approved=inv.root_approved
            )
            for inv in invites
        ]
    except Exception as e:
        logger.error(f"Failed to get pending invites: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/users/@me/org-invites/{invite_id}/accept")
async def accept_my_invite(
    invite_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Accept an organization invite (step 1 of 2-step flow)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        iid = int(invite_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid invite ID"}}
        )

    try:
        invite = orgs.accept_invite(iid, current_user.user_id)

        return InviteResponse(
            id=str(invite.id),
            org_id=str(invite.org_id),
            code=invite.code,
            invite_type=invite.invite_type,
            target_username=invite.target_username,
            created_at=invite.created_at,
            expires_at=invite.expires_at,
            status=invite.status,
            user_accepted=invite.user_accepted,
            root_approved=invite.root_approved
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "Permission" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": str(e)}}
            )
        elif "NotFound" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": str(e)}}
            )
        elif "Expired" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={"error": {"code": 410, "message": str(e)}}
            )
        logger.error(f"Failed to accept invite: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/users/@me/org-invites/{invite_id}/reject")
async def reject_my_invite(
    invite_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Reject an organization invite."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        iid = int(invite_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid invite ID"}}
        )

    try:
        orgs.reject_invite(iid, current_user.user_id)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "Permission" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": str(e)}}
            )
        elif "NotFound" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": str(e)}}
            )
        logger.error(f"Failed to reject invite: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


# === Managed Settings Endpoints ===

@router.get("/{org_id}/settings", response_model=List[ManagedSettingResponse])
async def get_managed_settings(
    org_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Get managed settings for an organization (root only)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        oid = int(org_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid organization ID"}}
        )

    try:
        _check_org_root(orgs, oid, current_user.user_id)

        settings = orgs.get_managed_settings(oid)

        return [
            ManagedSettingResponse(
                key=s.setting_key,
                value=s.setting_value,
                locked=s.locked
            )
            for s in settings
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get managed settings: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.put("/{org_id}/settings/{key}", response_model=ManagedSettingResponse)
async def set_managed_setting(
    org_id: str,
    key: str,
    body: ManagedSettingRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Set a managed setting for an organization (root only)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        oid = int(org_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid organization ID"}}
        )

    try:
        setting = orgs.set_managed_setting(oid, current_user.user_id, key, body.value, body.locked)

        return ManagedSettingResponse(
            key=setting.setting_key,
            value=setting.setting_value,
            locked=setting.locked
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "Permission" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": str(e)}}
            )
        elif "Organization" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": str(e)}}
            )
        logger.error(f"Failed to set managed setting: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


# === Server Restrictions Endpoints ===

@router.get("/{org_id}/servers")
async def get_server_restrictions(
    org_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Get server restrictions for an organization (root only)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        oid = int(org_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid organization ID"}}
        )

    try:
        _check_org_root(orgs, oid, current_user.user_id)

        org = orgs.get_org(oid)
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Organization not found"}},
            )

        return {
            "default_servers": [str(s) for s in org.default_servers] if org.default_servers else [],
            "allowed_servers": [str(s) for s in org.allowed_servers] if org.allowed_servers else None,
            "blocked_servers": [str(s) for s in org.blocked_servers] if org.blocked_servers else []
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get server restrictions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.put("/{org_id}/servers")
async def update_server_restrictions(
    org_id: str,
    body: ServerRestrictionsRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Update server restrictions for an organization (root only)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        oid = int(org_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid organization ID"}}
        )

    try:
        # Convert string IDs to ints
        default_servers = [int(s) for s in body.default_servers] if body.default_servers else None
        allowed_servers = [int(s) for s in body.allowed_servers] if body.allowed_servers else None
        blocked_servers = [int(s) for s in body.blocked_servers] if body.blocked_servers else None

        org = orgs.update_server_restrictions(
            oid,
            current_user.user_id,
            default_servers,
            allowed_servers,
            blocked_servers
        )

        return {
            "default_servers": [str(s) for s in org.default_servers] if org.default_servers else [],
            "allowed_servers": [str(s) for s in org.allowed_servers] if org.allowed_servers else None,
            "blocked_servers": [str(s) for s in org.blocked_servers] if org.blocked_servers else []
        }
    except Exception as e:
        exc_name = type(e).__name__
        if "Permission" in exc_name:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": str(e)}}
            )
        logger.error(f"Failed to update server restrictions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )


# === Public Invite Info Endpoint ===

@router.get("/invite/{code}")
async def get_invite_info(code: str):
    """Get public info about an invite (for registration page)."""
    orgs = _get_orgs_module()
    if not orgs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Organizations module not available"}}
        )

    try:
        invite = orgs.get_invite_by_code(code)
        if not invite:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Invite not found"}}
            )

        # Check if expired
        import time
        if invite.expires_at and invite.expires_at < int(time.time()):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={"error": {"code": 410, "message": "Invite has expired"}}
            )

        # Check if already used
        if invite.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={"error": {"code": 410, "message": f"Invite is {invite.status}"}}
            )

        org = orgs.get_org(invite.org_id)

        return {
            "invite_type": invite.invite_type,
            "org_name": org.name if org else None,
            "org_display_name": org.display_name if org else None,
            "expires_at": invite.expires_at
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get invite info: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}}
        )
