from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from .common import parse_id, raise_bad_request, raise_forbidden, raise_internal

router = APIRouter()


class OnboardingPresetRequest(BaseModel):
    server_id: str = Field(..., description="Server ID")
    preset: str = Field(
        ...,
        description="Preset name: community, gaming, education, business, open_source",
    )


ONBOARDING_PRESETS = {
    "community": {
        "description": "Welcome to our community! Get started by picking your interests.",
        "steps": [
            {
                "type": "select_roles",
                "title": "Pick your interests",
                "required": False,
                "options_key": "role_ids",
            },
            {
                "type": "visit_channel",
                "title": "Check out the rules",
                "required": True,
                "options_key": "channel_id",
            },
            {
                "type": "visit_channel",
                "title": "Say hello!",
                "required": False,
                "options_key": "channel_id",
            },
        ],
    },
    "gaming": {
        "description": "Welcome, gamer! Choose your games and find your squad.",
        "steps": [
            {
                "type": "select_roles",
                "title": "Choose your games",
                "required": False,
                "options_key": "role_ids",
            },
            {
                "type": "visit_channel",
                "title": "Read the server rules",
                "required": True,
                "options_key": "channel_id",
            },
            {
                "type": "visit_channel",
                "title": "Find a team",
                "required": False,
                "options_key": "channel_id",
            },
        ],
    },
    "education": {
        "description": "Welcome to the class! Set up your profile and get started.",
        "steps": [
            {
                "type": "select_roles",
                "title": "Select your course",
                "required": True,
                "options_key": "role_ids",
            },
            {
                "type": "visit_channel",
                "title": "Read the syllabus",
                "required": True,
                "options_key": "channel_id",
            },
            {
                "type": "visit_channel",
                "title": "Introduce yourself",
                "required": False,
                "options_key": "channel_id",
            },
        ],
    },
    "business": {
        "description": "Welcome to the team! Set up your department and access.",
        "steps": [
            {
                "type": "select_roles",
                "title": "Select your department",
                "required": True,
                "options_key": "role_ids",
            },
            {
                "type": "visit_channel",
                "title": "Read company guidelines",
                "required": True,
                "options_key": "channel_id",
            },
            {
                "type": "visit_channel",
                "title": "Introduce yourself",
                "required": False,
                "options_key": "channel_id",
            },
        ],
    },
    "open_source": {
        "description": "Welcome to the project! Get started by choosing your contribution areas.",
        "steps": [
            {
                "type": "select_roles",
                "title": "Choose your contribution area",
                "required": False,
                "options_key": "role_ids",
            },
            {
                "type": "visit_channel",
                "title": "Read the contributing guide",
                "required": True,
                "options_key": "channel_id",
            },
            {
                "type": "visit_channel",
                "title": "Find good first issues",
                "required": False,
                "options_key": "channel_id",
            },
        ],
    },
}


@router.get(
    "/onboarding/presets",
    summary="List onboarding presets",
    responses={401: {"model": ErrorResponse}},
)
async def list_onboarding_presets(
    current_user: TokenInfo = Depends(get_current_user),
):
    return {"presets": list(ONBOARDING_PRESETS.keys()), "details": ONBOARDING_PRESETS}


@router.post(
    "/onboarding/apply-preset",
    summary="Apply onboarding preset to server",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def apply_onboarding_preset(
    body: OnboardingPresetRequest, current_user: TokenInfo = Depends(get_current_user)
):
    if body.preset not in ONBOARDING_PRESETS:
        raise_bad_request(
            f"Invalid preset. Available: {', '.join(ONBOARDING_PRESETS.keys())}"
        )

    server_id = parse_id(body.server_id, "server ID")

    servers_mod = api.get_servers()
    if not servers_mod:
        raise_internal("Servers module not available")

    has_perm = False
    try:
        servers_mod.require_permission(
            current_user.user_id, server_id, "onboarding.manage"
        )
        has_perm = True
    except Exception:
        pass
    if not has_perm:
        try:
            servers_mod.require_permission(
                current_user.user_id, server_id, "server.manage"
            )
            has_perm = True
        except Exception:
            pass
    if not has_perm:
        raise_forbidden("Missing onboarding.manage or server.manage permission")

    preset = ONBOARDING_PRESETS[body.preset]

    db = api.get_db()
    if not db:
        raise_internal("Database not available")

    from src.core.servers.onboarding import OnboardingManager

    onboarding_mgr = OnboardingManager(db, servers_mod)

    try:
        onboarding_mgr.set_welcome_screen(
            user_id=current_user.user_id,
            server_id=server_id,
            description=preset["description"],
            enabled=True,
        )
    except Exception as e:
        logger.debug(f"Failed to set welcome screen from preset: {e}")

    created_steps = []
    for step_template in preset["steps"]:
        try:
            from src.core.servers.models import OnboardingStepType

            step_type = OnboardingStepType(step_template["type"])
            step = onboarding_mgr.create_onboarding_step(
                user_id=current_user.user_id,
                server_id=server_id,
                step_type=step_type,
                title=step_template["title"],
                required=step_template.get("required", False),
            )
            created_steps.append(
                {"id": str(step.id), "title": step.title, "type": step.step_type.value}
            )
        except Exception as e:
            logger.warning(f"Failed to create onboarding step from preset: {e}")
            created_steps.append(
                {
                    "title": step_template["title"],
                    "type": step_template["type"],
                    "error": str(e),
                }
            )

    logger.info(
        f"Applied onboarding preset '{body.preset}' to server {server_id} by user {current_user.user_id}"
    )
    return {
        "success": True,
        "preset": body.preset,
        "welcome_screen": {"description": preset["description"]},
        "steps_created": created_steps,
    }
