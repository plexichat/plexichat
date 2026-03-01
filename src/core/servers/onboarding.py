"""
Onboarding manager - Handles welcome screens and onboarding flows.
"""

import time
import json
from typing import Optional, List, Dict, Any

import utils.config as config
from src.utils.encryption import generate_snowflake_id

from .models import (
    WelcomeScreen,
    OnboardingStep,
    OnboardingProgress,
    OnboardingStepType,
    AuditLogAction,
)
from .exceptions import (
    ServerNotFoundError,
    OnboardingStepNotFoundError,
    OnboardingError,
    ChannelNotFoundError,
    RoleNotFoundError,
)


class OnboardingManager:
    """Manages welcome screens and onboarding flows."""

    def __init__(self, db, server_manager):
        """
        Initialize the onboarding manager.

        Args:
            db: Database instance
            server_manager: ServerManager instance
        """
        self._db = db
        self._server_manager = server_manager
        self._config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load onboarding configuration."""
        defaults = {
            "max_onboarding_steps": 10,
            "max_welcome_channels": 5,
            "max_step_options": 25,
        }
        onboarding_config = config.get("servers.onboarding", {})
        return {**defaults, **onboarding_config}

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        """Generate a new Snowflake ID."""
        return generate_snowflake_id()

    def set_welcome_screen(
        self,
        user_id: int,
        server_id: int,
        description: Optional[str] = None,
        welcome_channels: Optional[List[Dict[str, Any]]] = None,
        enabled: bool = True,
    ) -> WelcomeScreen:
        """Set or update the welcome screen for a server."""
        server = self._server_manager.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self._server_manager.require_permission(user_id, server_id, "onboarding.manage")

        if welcome_channels:
            max_channels = self._config.get("max_welcome_channels", 5)
            if len(welcome_channels) > max_channels:
                raise OnboardingError(
                    f"Maximum {max_channels} welcome channels allowed"
                )

            for wc in welcome_channels:
                channel_id = wc.get("channel_id")
                if channel_id:
                    channel = self._server_manager.get_channel(channel_id, user_id)
                    if not channel or channel.server_id != server_id:
                        raise ChannelNotFoundError(f"Channel {channel_id} not found")

        now = self._get_timestamp()
        existing = self._db.fetch_one(
            "SELECT id FROM srv_welcome_screens WHERE server_id = ?",
            (server_id,),
        )

        if existing:
            self._db.execute(
                """UPDATE srv_welcome_screens 
                   SET description = ?, enabled = ?, welcome_channels = ?, updated_at = ?
                   WHERE server_id = ?""",
                (
                    description,
                    1 if enabled else 0,
                    json.dumps(welcome_channels or []),
                    now,
                    server_id,
                ),
            )
        else:
            screen_id = self._generate_id()
            self._db.execute(
                """INSERT INTO srv_welcome_screens 
                   (id, server_id, description, enabled, welcome_channels, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    screen_id,
                    server_id,
                    description,
                    1 if enabled else 0,
                    json.dumps(welcome_channels or []),
                    now,
                    now,
                ),
            )

        self._log_audit(server_id, user_id, AuditLogAction.WELCOME_SCREEN_UPDATE)
        result = self.get_welcome_screen(server_id, user_id)
        assert result is not None  # Should exist since we just created/updated it
        return result

    def get_welcome_screen(
        self, server_id: int, user_id: int
    ) -> Optional[WelcomeScreen]:
        """Get the welcome screen for a server."""
        if not self._server_manager._is_member(server_id, user_id):
            raise ServerNotFoundError("Server not found")

        row = self._db.fetch_one(
            "SELECT * FROM srv_welcome_screens WHERE server_id = ?",
            (server_id,),
        )
        if not row:
            return None

        return self._row_to_welcome_screen(row)

    def delete_welcome_screen(self, user_id: int, server_id: int) -> bool:
        """Delete the welcome screen for a server."""
        server = self._server_manager.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self._server_manager.require_permission(user_id, server_id, "onboarding.manage")

        self._db.execute(
            "DELETE FROM srv_welcome_screens WHERE server_id = ?",
            (server_id,),
        )
        return True

    def create_onboarding_step(
        self,
        user_id: int,
        server_id: int,
        step_type: OnboardingStepType,
        title: str,
        description: Optional[str] = None,
        required: bool = False,
        options: Optional[Dict[str, Any]] = None,
    ) -> OnboardingStep:
        """Create an onboarding step."""
        server = self._server_manager.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self._server_manager.require_permission(user_id, server_id, "onboarding.manage")

        title = title.strip()
        if not title:
            raise OnboardingError("Step title cannot be empty")
        if len(title) > 100:
            raise OnboardingError("Step title cannot exceed 100 characters")

        existing_count = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM srv_onboarding_steps WHERE server_id = ?",
            (server_id,),
        )
        max_steps = self._config.get("max_onboarding_steps", 10)
        if existing_count and existing_count["count"] >= max_steps:
            raise OnboardingError(f"Maximum {max_steps} onboarding steps allowed")

        if options:
            self._validate_step_options(user_id, server_id, step_type, options)

        pos_row = self._db.fetch_one(
            "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM srv_onboarding_steps WHERE server_id = ?",
            (server_id,),
        )
        position = pos_row["next_pos"] if pos_row else 0

        now = self._get_timestamp()
        step_id = self._generate_id()

        self._db.execute(
            """INSERT INTO srv_onboarding_steps 
               (id, server_id, step_type, title, description, position, required, options, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                step_id,
                server_id,
                step_type.value,
                title,
                description,
                position,
                1 if required else 0,
                json.dumps(options) if options else None,
                now,
                now,
            ),
        )

        self._log_audit(
            server_id, user_id, AuditLogAction.ONBOARDING_UPDATE, "step", step_id
        )
        result = self.get_onboarding_step(step_id, user_id)
        assert result is not None  # Should exist since we just created it
        return result

    def _validate_step_options(
        self,
        user_id: int,
        server_id: int,
        step_type: OnboardingStepType,
        options: Dict[str, Any],
    ):
        """Validate step options based on step type."""
        if step_type == OnboardingStepType.SELECT_ROLES:
            role_ids = options.get("role_ids", [])
            max_options = self._config.get("max_step_options", 25)
            if len(role_ids) > max_options:
                raise OnboardingError(f"Maximum {max_options} role options allowed")
            for role_id in role_ids:
                role = self._server_manager.get_role(role_id, user_id)
                if not role or role.server_id != server_id:
                    raise RoleNotFoundError(f"Role {role_id} not found")

        elif step_type == OnboardingStepType.VISIT_CHANNEL:
            channel_id = options.get("channel_id")
            if channel_id:
                channel = self._server_manager.get_channel(channel_id, user_id)
                if not channel or channel.server_id != server_id:
                    raise ChannelNotFoundError(f"Channel {channel_id} not found")

    def get_onboarding_step(
        self, step_id: int, user_id: int
    ) -> Optional[OnboardingStep]:
        """Get an onboarding step by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM srv_onboarding_steps WHERE id = ?",
            (step_id,),
        )
        if not row:
            return None

        if not self._server_manager._is_member(row["server_id"], user_id):
            return None

        return self._row_to_step(row)

    def get_onboarding_steps(
        self, user_id: int, server_id: int
    ) -> List[OnboardingStep]:
        """Get all onboarding steps for a server."""
        if not self._server_manager._is_member(server_id, user_id):
            raise ServerNotFoundError("Server not found")

        rows = self._db.fetch_all(
            "SELECT * FROM srv_onboarding_steps WHERE server_id = ? ORDER BY position",
            (server_id,),
        )
        return [self._row_to_step(row) for row in rows]

    def update_onboarding_step(
        self,
        user_id: int,
        step_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        required: Optional[bool] = None,
        options: Optional[Dict[str, Any]] = None,
        position: Optional[int] = None,
    ) -> OnboardingStep:
        """Update an onboarding step."""
        step = self.get_onboarding_step(step_id, user_id)
        if not step:
            raise OnboardingStepNotFoundError("Step not found")

        self._server_manager.require_permission(
            user_id, step.server_id, "onboarding.manage"
        )

        updates = []
        params = []

        if title is not None:
            title = title.strip()
            if not title:
                raise OnboardingError("Step title cannot be empty")
            updates.append("title = ?")
            params.append(title)

        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if required is not None:
            updates.append("required = ?")
            params.append(1 if required else 0)

        if options is not None:
            self._validate_step_options(
                user_id, step.server_id, step.step_type, options
            )
            updates.append("options = ?")
            params.append(json.dumps(options))

        if position is not None:
            updates.append("position = ?")
            params.append(position)

        if updates:
            updates.append("updated_at = ?")
            params.append(self._get_timestamp())
            params.append(step_id)

            self._db.execute(
                f"UPDATE srv_onboarding_steps SET {', '.join(updates)} WHERE id = ?",  # nosec B608
                tuple(params),
            )

        result = self.get_onboarding_step(step_id, user_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def delete_onboarding_step(self, user_id: int, step_id: int) -> bool:
        """Delete an onboarding step."""
        step = self.get_onboarding_step(step_id, user_id)
        if not step:
            raise OnboardingStepNotFoundError("Step not found")

        self._server_manager.require_permission(
            user_id, step.server_id, "onboarding.manage"
        )

        self._db.execute("DELETE FROM srv_onboarding_steps WHERE id = ?", (step_id,))

        self._db.execute(
            """UPDATE srv_onboarding_progress 
               SET completed_steps = REPLACE(completed_steps, ?, '')
               WHERE server_id = ?""",
            (str(step_id), step.server_id),
        )

        return True

    def start_onboarding(self, user_id: int, server_id: int) -> OnboardingProgress:
        """Start onboarding for a user."""
        if not self._server_manager._is_member(server_id, user_id):
            raise ServerNotFoundError("Server not found")

        existing = self._db.fetch_one(
            "SELECT * FROM srv_onboarding_progress WHERE server_id = ? AND user_id = ?",
            (server_id, user_id),
        )
        if existing:
            return self._row_to_progress(existing)

        now = self._get_timestamp()
        progress_id = self._generate_id()

        self._db.execute(
            """INSERT INTO srv_onboarding_progress 
               (id, server_id, user_id, completed_steps, started_at)
               VALUES (?, ?, ?, ?, ?)""",
            (progress_id, server_id, user_id, json.dumps([]), now),
        )

        result = self.get_onboarding_progress(user_id, server_id)
        assert result is not None  # Should exist since we just created it
        return result

    def complete_onboarding_step(
        self,
        user_id: int,
        server_id: int,
        step_id: int,
        response: Optional[Dict[str, Any]] = None,
    ) -> OnboardingProgress:
        """Mark an onboarding step as complete."""
        step = self.get_onboarding_step(step_id, user_id)
        if not step or step.server_id != server_id:
            raise OnboardingStepNotFoundError("Step not found")

        progress = self.get_onboarding_progress(user_id, server_id)
        if not progress:
            progress = self.start_onboarding(user_id, server_id)

        if step_id in progress.completed_steps:
            return progress

        if step.step_type == OnboardingStepType.SELECT_ROLES and response:
            selected_roles = response.get("selected_roles", [])
            for role_id in selected_roles:
                try:
                    self._server_manager.assign_role(
                        user_id, server_id, user_id, role_id
                    )
                except Exception:
                    pass

        completed_steps = progress.completed_steps + [step_id]
        now = self._get_timestamp()

        all_steps = self.get_onboarding_steps(user_id, server_id)
        required_steps = [s.id for s in all_steps if s.required]
        all_required_complete = all(s in completed_steps for s in required_steps)

        self._db.execute(
            """UPDATE srv_onboarding_progress 
               SET completed_steps = ?, completed = ?, completed_at = ?
               WHERE server_id = ? AND user_id = ?""",
            (
                json.dumps(completed_steps),
                1 if all_required_complete else 0,
                now if all_required_complete else None,
                server_id,
                user_id,
            ),
        )

        result = self.get_onboarding_progress(user_id, server_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def get_onboarding_progress(
        self, user_id: int, server_id: int
    ) -> Optional[OnboardingProgress]:
        """Get onboarding progress for a user."""
        row = self._db.fetch_one(
            "SELECT * FROM srv_onboarding_progress WHERE server_id = ? AND user_id = ?",
            (server_id, user_id),
        )
        if not row:
            return None

        return self._row_to_progress(row)

    def reset_onboarding_progress(self, user_id: int, server_id: int) -> bool:
        """Reset onboarding progress for a user."""
        self._db.execute(
            "DELETE FROM srv_onboarding_progress WHERE server_id = ? AND user_id = ?",
            (server_id, user_id),
        )
        return True

    def _log_audit(
        self,
        server_id: int,
        user_id: int,
        action: AuditLogAction,
        target_type: Optional[str] = None,
        target_id: Optional[int] = None,
    ):
        """Log an audit entry."""
        entry_id = self._generate_id()
        now = self._get_timestamp()

        self._db.execute(
            """INSERT INTO srv_audit_log 
               (id, server_id, user_id, action, target_type, target_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (entry_id, server_id, user_id, action.value, target_type, target_id, now),
        )

    def _row_to_welcome_screen(self, row: Dict) -> WelcomeScreen:
        """Convert database row to WelcomeScreen model."""
        welcome_channels = row["welcome_channels"]
        if isinstance(welcome_channels, str):
            welcome_channels = json.loads(welcome_channels) if welcome_channels else []

        return WelcomeScreen(
            id=row["id"],
            server_id=row["server_id"],
            description=row["description"],
            enabled=bool(row["enabled"]),
            welcome_channels=welcome_channels or [],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_step(self, row: Dict) -> OnboardingStep:
        """Convert database row to OnboardingStep model."""
        options = row["options"]
        if isinstance(options, str):
            options = json.loads(options) if options else None

        return OnboardingStep(
            id=row["id"],
            server_id=row["server_id"],
            step_type=OnboardingStepType(row["step_type"]),
            title=row["title"],
            description=row["description"],
            position=row["position"],
            required=bool(row["required"]),
            options=options,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_progress(self, row: Dict) -> OnboardingProgress:
        """Convert database row to OnboardingProgress model."""
        completed_steps = row["completed_steps"]
        if isinstance(completed_steps, str):
            completed_steps = json.loads(completed_steps) if completed_steps else []

        return OnboardingProgress(
            id=row["id"],
            server_id=row["server_id"],
            user_id=row["user_id"],
            completed_steps=completed_steps or [],
            completed=bool(row["completed"]),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

