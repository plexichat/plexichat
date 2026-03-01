"""
Command registry - Command registration and management.
"""

import time
import json
from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

from ..models import Command, CommandType
from ..exceptions import (
    CommandNotFoundError,
    CommandLimitError,
    CommandValidationError,
    ApplicationNotFoundError,
)
from .validation import validate_command, validate_command_update
from .options import options_from_dict


class CommandRegistry:
    """Handles command registration and management."""

    def __init__(self, db, config: Dict[str, Any]):
        """
        Initialize command registry.

        Args:
            db: Database instance
            config: Command configuration
        """
        self._db = db
        self._config = config

    def _current_time(self) -> int:
        """Get current Unix timestamp."""
        return int(time.time() * 1000)

    def register_command(
        self,
        application_id: int,
        name: str,
        description: str,
        command_type: CommandType = CommandType.CHAT_INPUT,
        server_id: Optional[int] = None,
        options: Optional[List[Dict[str, Any]]] = None,
        default_member_permissions: Optional[str] = None,
        dm_permission: bool = True,
        nsfw: bool = False,
    ) -> Command:
        """
        Register a new command.

        Args:
            application_id: Application ID
            name: Command name
            description: Command description
            command_type: Type of command
            server_id: Server ID for guild commands (None for global)
            options: Command options
            default_member_permissions: Default permissions required
            dm_permission: Whether command works in DMs
            nsfw: Whether command is NSFW

        Returns:
            Registered Command

        Raises:
            ApplicationNotFoundError: Application not found
            CommandLimitError: Command limit reached
            CommandValidationError: Invalid command data
        """
        app = self._db.fetch_one(
            "SELECT id FROM app_applications WHERE id = ?", (application_id,)
        )
        if not app:
            raise ApplicationNotFoundError("Application not found")

        command_data = {
            "name": name,
            "description": description,
            "command_type": command_type,
            "options": options or [],
            "default_member_permissions": default_member_permissions,
        }

        valid, issues = validate_command(command_data)
        if not valid:
            raise CommandValidationError("Command validation failed", issues)

        max_commands = self._config.get("max_commands_per_app", 100)
        if server_id:
            count = self._db.fetch_one(
                """SELECT COUNT(*) as count FROM app_commands
                   WHERE application_id = ? AND server_id = ?""",
                (application_id, server_id),
            )
        else:
            count = self._db.fetch_one(
                """SELECT COUNT(*) as count FROM app_commands
                   WHERE application_id = ? AND server_id IS NULL""",
                (application_id,),
            )

        current = count["count"] if count else 0
        if current >= max_commands:
            raise CommandLimitError(
                f"Maximum of {max_commands} commands per application",
                max_commands,
                current,
            )

        existing = self._db.fetch_one(
            """SELECT id FROM app_commands
               WHERE application_id = ? AND name = ? AND server_id IS ?""",
            (application_id, name.lower(), server_id),
        )
        if existing:
            raise CommandValidationError(
                "Command with this name already exists", ["Duplicate command name"]
            )

        command_id = generate_snowflake_id()
        now = self._current_time()

        options_json = json.dumps(options or [])

        self._db.execute(
            """INSERT INTO app_commands
               (id, application_id, name, description, command_type, server_id,
                options, default_member_permissions, dm_permission, nsfw,
                version, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                command_id,
                application_id,
                name.lower(),
                description,
                command_type.value
                if isinstance(command_type, CommandType)
                else command_type,
                server_id,
                options_json,
                default_member_permissions,
                1 if dm_permission else 0,
                1 if nsfw else 0,
                1,
                now,
                now,
            ),
        )

        logger.info(f"Command registered: {name} for app {application_id}")

        result = self.get_command(command_id)
        assert result is not None  # Should exist since we just created it
        return result

    def get_command(self, command_id: int) -> Optional[Command]:
        """
        Get a command by ID.

        Args:
            command_id: Command ID

        Returns:
            Command or None
        """
        row = self._db.fetch_one(
            """SELECT id, application_id, name, description, command_type,
                      server_id, options, default_member_permissions,
                      dm_permission, nsfw, version, created_at, updated_at
               FROM app_commands WHERE id = ?""",
            (command_id,),
        )

        if not row:
            return None

        return self._row_to_command(row)

    def get_commands(
        self,
        application_id: int,
        server_id: Optional[int] = None,
        include_global: bool = True,
    ) -> List[Command]:
        """
        Get commands for an application.

        Args:
            application_id: Application ID
            server_id: Server ID to filter by
            include_global: Include global commands when server_id is set

        Returns:
            List of Commands
        """
        if server_id is None:
            rows = self._db.fetch_all(
                """SELECT id, application_id, name, description, command_type,
                          server_id, options, default_member_permissions,
                          dm_permission, nsfw, version, created_at, updated_at
                   FROM app_commands
                   WHERE application_id = ? AND server_id IS NULL
                   ORDER BY name""",
                (application_id,),
            )
        elif include_global:
            rows = self._db.fetch_all(
                """SELECT id, application_id, name, description, command_type,
                          server_id, options, default_member_permissions,
                          dm_permission, nsfw, version, created_at, updated_at
                   FROM app_commands
                   WHERE application_id = ? AND (server_id IS NULL OR server_id = ?)
                   ORDER BY server_id NULLS FIRST, name""",
                (application_id, server_id),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT id, application_id, name, description, command_type,
                          server_id, options, default_member_permissions,
                          dm_permission, nsfw, version, created_at, updated_at
                   FROM app_commands
                   WHERE application_id = ? AND server_id = ?
                   ORDER BY name""",
                (application_id, server_id),
            )

        return [self._row_to_command(row) for row in rows]

    def update_command(
        self,
        command_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        options: Optional[List[Dict[str, Any]]] = None,
        default_member_permissions: Optional[str] = None,
        dm_permission: Optional[bool] = None,
        nsfw: Optional[bool] = None,
    ) -> Command:
        """
        Update a command.

        Args:
            command_id: Command ID
            name: New name
            description: New description
            options: New options
            default_member_permissions: New permissions
            dm_permission: New DM permission
            nsfw: New NSFW flag

        Returns:
            Updated Command

        Raises:
            CommandNotFoundError: Command not found
            CommandValidationError: Invalid update data
        """
        command = self.get_command(command_id)
        if not command:
            raise CommandNotFoundError("Command not found")

        update_data = {}
        if name is not None:
            update_data["name"] = name
        if description is not None:
            update_data["description"] = description
        if options is not None:
            update_data["options"] = options
        if default_member_permissions is not None:
            update_data["default_member_permissions"] = default_member_permissions

        if update_data:
            update_data["command_type"] = command.command_type
            valid, issues = validate_command_update(update_data)
            if not valid:
                raise CommandValidationError("Command validation failed", issues)

        if name is not None and name.lower() != command.name:
            existing = self._db.fetch_one(
                """SELECT id FROM app_commands
                   WHERE application_id = ? AND name = ? AND server_id IS ? AND id != ?""",
                (command.application_id, name.lower(), command.server_id, command_id),
            )
            if existing:
                raise CommandValidationError(
                    "Command with this name already exists", ["Duplicate command name"]
                )

        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name.lower())

        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if options is not None:
            updates.append("options = ?")
            params.append(json.dumps(options))

        if default_member_permissions is not None:
            updates.append("default_member_permissions = ?")
            params.append(default_member_permissions)

        if dm_permission is not None:
            updates.append("dm_permission = ?")
            params.append(1 if dm_permission else 0)

        if nsfw is not None:
            updates.append("nsfw = ?")
            params.append(1 if nsfw else 0)

        if updates:
            updates.append("version = version + 1")
            updates.append("updated_at = ?")
            params.append(self._current_time())
            params.append(command_id)

            self._db.execute(
                f"UPDATE app_commands SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )

            logger.debug(f"Command updated: {command_id}")

        result = self.get_command(command_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def delete_command(self, command_id: int) -> bool:
        """
        Delete a command.

        Args:
            command_id: Command ID

        Returns:
            True if deleted

        Raises:
            CommandNotFoundError: Command not found
        """
        command = self.get_command(command_id)
        if not command:
            raise CommandNotFoundError("Command not found")

        self._db.execute("DELETE FROM app_commands WHERE id = ?", (command_id,))

        logger.info(f"Command deleted: {command.name} ({command_id})")
        return True

    def bulk_overwrite_commands(
        self,
        application_id: int,
        commands: List[Dict[str, Any]],
        server_id: Optional[int] = None,
    ) -> List[Command]:
        """
        Bulk overwrite commands for an application.

        Args:
            application_id: Application ID
            commands: List of command data
            server_id: Server ID for guild commands

        Returns:
            List of registered Commands
        """
        for cmd_data in commands:
            valid, issues = validate_command(cmd_data)
            if not valid:
                raise CommandValidationError(
                    f"Command '{cmd_data.get('name', 'unknown')}' validation failed",
                    issues,
                )

        if server_id:
            self._db.execute(
                "DELETE FROM app_commands WHERE application_id = ? AND server_id = ?",
                (application_id, server_id),
            )
        else:
            self._db.execute(
                "DELETE FROM app_commands WHERE application_id = ? AND server_id IS NULL",
                (application_id,),
            )

        result = []
        for cmd_data in commands:
            command = self.register_command(
                application_id=application_id,
                name=cmd_data["name"],
                description=cmd_data.get("description", ""),
                command_type=cmd_data.get("command_type", CommandType.CHAT_INPUT),
                server_id=server_id,
                options=cmd_data.get("options"),
                default_member_permissions=cmd_data.get("default_member_permissions"),
                dm_permission=cmd_data.get("dm_permission", True),
                nsfw=cmd_data.get("nsfw", False),
            )
            result.append(command)

        logger.info(f"Bulk overwrote {len(result)} commands for app {application_id}")
        return result

    def _row_to_command(self, row) -> Command:
        """Convert database row to Command."""
        options_data = json.loads(row["options"]) if row["options"] else []
        options = options_from_dict(options_data)

        return Command(
            id=row["id"],
            application_id=row["application_id"],
            name=row["name"],
            description=row["description"],
            command_type=CommandType(row["command_type"]),
            server_id=row["server_id"],
            options=options,
            default_member_permissions=row["default_member_permissions"],
            dm_permission=bool(row["dm_permission"]),
            nsfw=bool(row["nsfw"]),
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )



