"""Onboarding operations mixin."""

from typing import Any, Dict, List, Optional

from src.core.base import SnowflakeID

from .models import OnboardingStep, OnboardingProgress, OnboardingStepType


class OnboardingMixin:
    """Mixin for onboarding operations.

    Provides: create_onboarding_step, get_onboarding_step, get_onboarding_steps,
    update_onboarding_step, delete_onboarding_step, start_onboarding,
    complete_onboarding_step, get_onboarding_progress, reset_onboarding_progress
    """

    _onboarding_manager: Any = None

    def create_onboarding_step(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        step_type: OnboardingStepType,
        title: str,
        description: Optional[str] = None,
        required: bool = False,
        options: Optional[Dict[str, Any]] = None,
    ) -> OnboardingStep:
        """Create an onboarding step."""
        return self._onboarding_manager.create_onboarding_step(
            user_id, server_id, step_type, title, description, required, options
        )

    def get_onboarding_step(
        self, step_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[OnboardingStep]:
        """Get an onboarding step by ID."""
        return self._onboarding_manager.get_onboarding_step(step_id, user_id)

    def get_onboarding_steps(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> List[OnboardingStep]:
        """Get all onboarding steps for a server."""
        return self._onboarding_manager.get_onboarding_steps(user_id, server_id)

    def update_onboarding_step(
        self,
        user_id: SnowflakeID,
        step_id: SnowflakeID,
        title: Optional[str] = None,
        description: Optional[str] = None,
        required: Optional[bool] = None,
        options: Optional[Dict[str, Any]] = None,
        position: Optional[int] = None,
    ) -> OnboardingStep:
        """Update an onboarding step."""
        return self._onboarding_manager.update_onboarding_step(
            user_id, step_id, title, description, required, options, position
        )

    def delete_onboarding_step(
        self, user_id: SnowflakeID, step_id: SnowflakeID
    ) -> bool:
        """Delete an onboarding step."""
        return self._onboarding_manager.delete_onboarding_step(user_id, step_id)

    def start_onboarding(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> OnboardingProgress:
        """Start onboarding for a user."""
        return self._onboarding_manager.start_onboarding(user_id, server_id)

    def complete_onboarding_step(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        step_id: SnowflakeID,
        response: Optional[Dict[str, Any]] = None,
    ) -> OnboardingProgress:
        """Mark an onboarding step as complete."""
        return self._onboarding_manager.complete_onboarding_step(
            user_id, server_id, step_id, response
        )

    def get_onboarding_progress(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> Optional[OnboardingProgress]:
        """Get onboarding progress for a user."""
        return self._onboarding_manager.get_onboarding_progress(user_id, server_id)

    def reset_onboarding_progress(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> bool:
        """Reset onboarding progress for a user."""
        return self._onboarding_manager.reset_onboarding_progress(user_id, server_id)
