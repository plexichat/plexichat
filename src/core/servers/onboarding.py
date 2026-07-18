"""Onboarding operations - welcome screen, onboarding steps and progress."""

from typing import Any, Dict, List, Optional

from src.core.base import SnowflakeID

from .models import (
    WelcomeScreen,
    OnboardingStep,
    OnboardingProgress,
    OnboardingStepType,
)


_manager: Any = None


def _get_manager() -> Any:
    """Get the server manager instance."""
    global _manager
    if _manager is None:
        from . import _get_manager as _get_global_manager

        _manager = _get_global_manager()
    return _manager


def set_welcome_screen(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    description: Optional[str] = None,
    welcome_channels: Optional[List[Dict[str, Any]]] = None,
    enabled: bool = True,
) -> WelcomeScreen:
    """Set or update the welcome screen for a server."""
    return _get_manager().set_welcome_screen(
        user_id, server_id, description, welcome_channels, enabled
    )


def get_welcome_screen(
    server_id: SnowflakeID, user_id: SnowflakeID
) -> Optional[WelcomeScreen]:
    """Get the welcome screen for a server."""
    return _get_manager().get_welcome_screen(server_id, user_id)


def delete_welcome_screen(user_id: SnowflakeID, server_id: SnowflakeID) -> bool:
    """Delete the welcome screen for a server."""
    return _get_manager().delete_welcome_screen(user_id, server_id)


def create_onboarding_step(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    step_type: OnboardingStepType,
    title: str,
    description: Optional[str] = None,
    required: bool = False,
    options: Optional[Dict[str, Any]] = None,
) -> OnboardingStep:
    """Create an onboarding step."""
    return _get_manager().create_onboarding_step(
        user_id, server_id, step_type, title, description, required, options
    )


def get_onboarding_step(
    step_id: SnowflakeID, user_id: SnowflakeID
) -> Optional[OnboardingStep]:
    """Get an onboarding step by ID."""
    return _get_manager().get_onboarding_step(step_id, user_id)


def get_onboarding_steps(
    user_id: SnowflakeID, server_id: SnowflakeID
) -> List[OnboardingStep]:
    """Get all onboarding steps for a server."""
    return _get_manager().get_onboarding_steps(user_id, server_id)


def update_onboarding_step(
    user_id: SnowflakeID,
    step_id: SnowflakeID,
    title: Optional[str] = None,
    description: Optional[str] = None,
    required: Optional[bool] = None,
    options: Optional[Dict[str, Any]] = None,
    position: Optional[int] = None,
) -> OnboardingStep:
    """Update an onboarding step."""
    return _get_manager().update_onboarding_step(
        user_id, step_id, title, description, required, options, position
    )


def delete_onboarding_step(user_id: SnowflakeID, step_id: SnowflakeID) -> bool:
    """Delete an onboarding step."""
    return _get_manager().delete_onboarding_step(user_id, step_id)


def start_onboarding(
    user_id: SnowflakeID, server_id: SnowflakeID
) -> OnboardingProgress:
    """Start onboarding for a user."""
    return _get_manager().start_onboarding(user_id, server_id)


def complete_onboarding_step(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    step_id: SnowflakeID,
    response: Optional[Dict[str, Any]] = None,
) -> OnboardingProgress:
    """Mark an onboarding step as complete."""
    return _get_manager().complete_onboarding_step(
        user_id, server_id, step_id, response
    )


def get_onboarding_progress(
    user_id: SnowflakeID, server_id: SnowflakeID
) -> Optional[OnboardingProgress]:
    """Get onboarding progress for a user."""
    return _get_manager().get_onboarding_progress(user_id, server_id)


def reset_onboarding_progress(user_id: SnowflakeID, server_id: SnowflakeID) -> bool:
    """Reset onboarding progress for a user."""
    return _get_manager().reset_onboarding_progress(user_id, server_id)
