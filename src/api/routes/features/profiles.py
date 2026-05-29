from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Optional, List

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from .common import parse_id, raise_bad_request

router = APIRouter()


class ProfileUpdateRequest(BaseModel):
    bio: Optional[str] = Field(None, max_length=1000, description="Profile bio")
    status: Optional[str] = Field(
        None, max_length=128, description="Custom status text"
    )
    status_emoji: Optional[str] = Field(None, max_length=32, description="Status emoji")
    pronouns: Optional[str] = Field(None, max_length=40, description="Pronouns")
    location: Optional[str] = Field(None, max_length=100, description="Location")
    timezone: Optional[str] = Field(None, max_length=64, description="Timezone")
    banner_url: Optional[str] = Field(None, description="Banner image URL")
    social_links: Optional[List[dict]] = Field(None, description="Social link objects")


@router.get(
    "/users/{user_id}/profile",
    summary="Get user profile",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_user_profile(
    user_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    uid = parse_id(user_id, "user ID")

    db = api.get_db()
    from src.core.profiles.manager import ProfileManager

    svc = ProfileManager(db)

    result = svc.get_profile(uid)
    if not result:
        return {"user_id": uid, "bio": None, "status": None, "social_links": []}
    return {"profile": result}


@router.patch(
    "/users/@me/profile",
    summary="Update your profile",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def update_own_profile(
    body: ProfileUpdateRequest, current_user: TokenInfo = Depends(get_current_user)
):
    db = api.get_db()
    from src.core.profiles.manager import ProfileManager

    svc = ProfileManager(db)

    try:
        result = svc.update_profile(
            user_id=current_user.user_id,
            bio=body.bio,
            pronouns=body.pronouns,
            location=body.location,
            timezone=body.timezone,
            banner_url=body.banner_url,
            social_links=body.social_links,
        )
        return {"success": True, "profile": result}
    except ValueError as e:
        raise_bad_request(str(e))
