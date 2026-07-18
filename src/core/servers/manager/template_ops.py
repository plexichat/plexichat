"""
Template manager - Handles server templates for cloning server structure.
"""

import time
import json
import secrets
import string
from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

from ..models import (
    ServerTemplate,
    TemplateData,
    Server,
    ChannelType,
    AuditLogAction,
)
from ..exceptions import (
    ServerNotFoundError,
    TemplateNotFoundError,
    TemplateError,
    PermissionDeniedError,
)


class TemplateManager:
    """Manages server templates."""

    def __init__(self, db, server_manager):
        """
        Initialize the template manager.

        Args:
            db: Database instance
            server_manager: ServerManager instance
        """
        self._db = db
        self._server_manager = server_manager
        self._config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load template configuration."""
        defaults = {
            "max_templates_per_user": 25,
            "template_code_length": 8,
            "max_channels_in_template": 100,
            "max_roles_in_template": 50,
        }
        template_config = config.get("servers.templates", {})
        return {**defaults, **template_config}

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        """Generate a new Snowflake ID."""
        return generate_snowflake_id()

    def _generate_template_code(self) -> str:
        """Generate a unique template code."""
        length = self._config.get("template_code_length", 8)
        chars = string.ascii_letters + string.digits
        while True:
            code = "".join(secrets.choice(chars) for _ in range(length))
            existing = self._db.fetch_one(
                "SELECT 1 FROM srv_templates WHERE code = ?", (code,)
            )
            if not existing:
                return code

    def create_template(
        self,
        user_id: int,
        server_id: int,
        name: str,
        description: Optional[str] = None,
    ) -> ServerTemplate:
        """Create a template from an existing server."""
        server = self._server_manager.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self._server_manager.require_permission(user_id, server_id, "templates.manage")

        name = name.strip()
        if not name:
            raise TemplateError("Template name cannot be empty")
        if len(name) > 100:
            raise TemplateError("Template name cannot exceed 100 characters")

        user_templates = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM srv_templates WHERE creator_id = ?",
            (user_id,),
        )
        max_templates = self._config.get("max_templates_per_user", 25)
        if user_templates and user_templates["count"] >= max_templates:
            raise TemplateError(f"Maximum templates ({max_templates}) reached")

        now = self._get_timestamp()
        template_id = self._generate_id()
        code = self._generate_template_code()

        self._db.execute(
            """INSERT INTO srv_templates 
               (id, name, description, creator_id, source_server_id, code, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (template_id, name, description, user_id, server_id, code, now, now),
        )

        self._snapshot_server(template_id, server_id, user_id)

        self._log_audit(
            server_id, user_id, AuditLogAction.TEMPLATE_CREATE, "template", template_id
        )
        logger.debug(f"Created template {template_id} from server {server_id}")

        result = self.get_template(code, user_id)
        assert result is not None  # Should exist since we just created it
        return result

    def _snapshot_server(self, template_id: int, server_id: int, user_id: int) -> None:
        """Snapshot server structure into template data."""
        channels_data = []
        categories_data = []
        roles_data = []

        cat_rows = self._db.fetch_all(
            "SELECT * FROM srv_categories WHERE server_id = ? ORDER BY position",
            (server_id,),
        )
        for row in cat_rows:
            categories_data.append(
                {
                    "name": row["name"],
                    "position": row["position"],
                }
            )

        max_channels = self._config.get("max_channels_in_template", 100)
        ch_rows = self._db.fetch_all(
            "SELECT * FROM srv_channels WHERE server_id = ? AND deleted = 0 ORDER BY position LIMIT ?",
            (server_id, max_channels),
        )
        for row in ch_rows:
            # Read topic from topic_encrypted column (unencrypted topic column dropped in migration 029)
            topic = None
            if row.get("topic_encrypted"):
                if self._server_manager._encrypt_descriptions:
                    from src.utils.encryption import decrypt_data

                    try:
                        topic = decrypt_data(row["topic_encrypted"])
                    except Exception:
                        logger.debug(f"Failed to decrypt topic for channel {row['id']}")
                else:
                    topic = row["topic_encrypted"]

            channels_data.append(
                {
                    "name": row["name"],
                    "channel_type": row["channel_type"],
                    "category_position": self._get_category_position(
                        row["category_id"], cat_rows
                    ),
                    "position": row["position"],
                    "topic": topic,
                    "nsfw": bool(row["nsfw"]),
                    "slowmode_seconds": row["slowmode_seconds"],
                }
            )

        max_roles = self._config.get("max_roles_in_template", 50)
        role_rows = self._db.fetch_all(
            "SELECT * FROM srv_roles WHERE server_id = ? AND deleted = 0 AND is_default = 0 ORDER BY position DESC LIMIT ?",
            (server_id, max_roles),
        )
        for row in role_rows:
            perms = row["permissions"]
            if isinstance(perms, str):
                perms = json.loads(perms) if perms else {}
            roles_data.append(
                {
                    "name": row["name"],
                    "permissions": perms,
                    "color": row["color"],
                    "hoist": bool(row["hoist"]),
                    "mentionable": bool(row["mentionable"]),
                    "position": row["position"],
                }
            )

        now = self._get_timestamp()
        data_id = self._generate_id()

        self._db.execute(
            """INSERT INTO srv_template_data (id, template_id, channels, categories, roles, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                data_id,
                template_id,
                json.dumps(channels_data),
                json.dumps(categories_data),
                json.dumps(roles_data),
                now,
            ),
        )

    def _get_category_position(
        self, category_id: Optional[int], cat_rows: List[Dict[str, Any]]
    ) -> Optional[int]:
        """Get category position by ID."""
        if not category_id:
            return None
        for row in cat_rows:
            if row["id"] == category_id:
                return row["position"]
        return None

    def get_template(
        self, code: str, user_id: Optional[int] = None
    ) -> Optional[ServerTemplate]:
        """Get a template by code."""
        logger.debug(f"Looking up template: {code}")
        row = self._db.fetch_one(
            "SELECT * FROM srv_templates WHERE code = ?",
            (code,),
        )
        if not row:
            return None

        if not row["is_public"] and user_id and row["creator_id"] != user_id:
            logger.debug(
                f"Template access denied: creator {row['creator_id']} != user {user_id}"
            )
            return None

        return self._row_to_template(row)

    def get_template_by_id(
        self, template_id: int, user_id: int
    ) -> Optional[ServerTemplate]:
        """Get a template by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM srv_templates WHERE id = ?",
            (template_id,),
        )
        if not row:
            return None

        if not row["is_public"] and row["creator_id"] != user_id:
            return None

        return self._row_to_template(row)

    def get_user_templates(self, user_id: int, limit: int = 50) -> List[ServerTemplate]:
        """Get templates created by a user."""
        rows = self._db.fetch_all(
            "SELECT * FROM srv_templates WHERE creator_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, min(limit, 100)),
        )
        return [self._row_to_template(row) for row in rows]

    def get_public_templates(self, limit: int = 50) -> List[ServerTemplate]:
        """Get public templates."""
        rows = self._db.fetch_all(
            "SELECT * FROM srv_templates WHERE is_public = 1 ORDER BY usage_count DESC LIMIT ?",
            (min(limit, 100),),
        )
        return [self._row_to_template(row) for row in rows]

    def preview_template(self, code: str) -> Optional[TemplateData]:
        """Preview template data without applying."""
        template = self.get_template(code)
        if not template:
            raise TemplateNotFoundError("Template not found")

        row = self._db.fetch_one(
            "SELECT * FROM srv_template_data WHERE template_id = ?",
            (template.id,),
        )
        if not row:
            return None

        return self._row_to_template_data(row)

    def apply_template(
        self,
        user_id: int,
        code: str,
        server_name: str,
        server_description: Optional[str] = None,
    ) -> Optional[Server]:
        """Apply a template to create a new server."""
        template = self.get_template(code, user_id)
        if not template:
            raise TemplateNotFoundError("Template not found")

        data_row = self._db.fetch_one(
            "SELECT * FROM srv_template_data WHERE template_id = ?",
            (template.id,),
        )
        if not data_row:
            raise TemplateError("Template data not found")

        server = self._server_manager.create_server(
            owner_id=user_id,
            name=server_name,
            description=server_description,
        )

        categories = (
            json.loads(data_row["categories"]) if data_row["categories"] else []
        )
        channels = json.loads(data_row["channels"]) if data_row["channels"] else []
        roles = json.loads(data_row["roles"]) if data_row["roles"] else []

        category_map: Dict[int, int] = {}
        for cat_data in sorted(categories, key=lambda x: x.get("position", 0)):
            cat = self._server_manager.create_category(
                user_id=user_id,
                server_id=server.id,
                name=cat_data["name"],
            )
            category_map[cat_data["position"]] = cat.id

        for ch_data in sorted(channels, key=lambda x: x.get("position", 0)):
            cat_pos = ch_data.get("category_position")
            category_id = category_map.get(cat_pos) if cat_pos is not None else None

            self._server_manager.create_channel(
                user_id=user_id,
                server_id=server.id,
                name=ch_data["name"],
                channel_type=ChannelType(ch_data.get("channel_type", "text")),
                category_id=category_id,
                topic=ch_data.get("topic"),
                nsfw=ch_data.get("nsfw", False),
                slowmode_seconds=ch_data.get("slowmode_seconds", 0),
            )

        for role_data in sorted(
            roles, key=lambda x: x.get("position", 0), reverse=True
        ):
            self._server_manager.create_role(
                user_id=user_id,
                server_id=server.id,
                name=role_data["name"],
                permissions=role_data.get("permissions"),
                color=role_data.get("color"),
                hoist=role_data.get("hoist", False),
                mentionable=role_data.get("mentionable", False),
            )

        self._db.execute(
            "UPDATE srv_templates SET usage_count = usage_count + 1 WHERE id = ?",
            (template.id,),
        )

        logger.debug(f"Applied template {template.id} to create server {server.id}")
        return self._server_manager.get_server(server.id, user_id)

    def delete_template(self, user_id: int, code: str) -> bool:
        """Delete a template."""
        template = self.get_template(code, user_id)
        if not template:
            raise TemplateNotFoundError("Template not found")

        if template.creator_id != user_id:
            raise PermissionDeniedError(
                "Only the creator can delete this template", "templates.manage"
            )

        self._db.execute(
            "DELETE FROM srv_template_data WHERE template_id = ?", (template.id,)
        )
        self._db.execute("DELETE FROM srv_templates WHERE id = ?", (template.id,))

        if template.source_server_id:
            self._log_audit(
                template.source_server_id,
                user_id,
                AuditLogAction.TEMPLATE_DELETE,
                "template",
                template.id,
            )

        return True

    def update_template(
        self,
        user_id: int,
        code: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        is_public: Optional[bool] = None,
    ) -> ServerTemplate:
        """Update template metadata."""
        template = self.get_template(code, user_id)
        if not template:
            raise TemplateNotFoundError("Template not found")

        if template.creator_id != user_id:
            raise PermissionDeniedError(
                "Only the creator can update this template", "templates.manage"
            )

        updates = []
        params = []

        if name is not None:
            name = name.strip()
            if not name:
                raise TemplateError("Template name cannot be empty")
            updates.append("name = ?")
            params.append(name)

        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if is_public is not None:
            updates.append("is_public = ?")
            params.append(1 if is_public else 0)

        if updates:
            updates.append("updated_at = ?")
            params.append(self._get_timestamp())
            params.append(template.id)

            # Whitelist of allowed column names for UPDATE
            allowed_columns = {
                "name",
                "description",
                "server_id",
                "category_id",
                "enabled",
            }
            # Validate all column names in updates
            for update in updates:
                col_name = update.split(" = ")[0]
                if col_name not in allowed_columns:
                    raise ValueError(f"Invalid column name: {col_name}")

            # Avoid dynamic UPDATE to satisfy bandit - use if-else for each possible column
            now = self._get_timestamp()
            for update in updates:
                if "name = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_templates SET name = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, template.id),
                    )
                elif "description = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_templates SET description = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, template.id),
                    )
                elif "server_id = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_templates SET server_id = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, template.id),
                    )
                elif "category_id = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_templates SET category_id = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, template.id),
                    )
                elif "enabled = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_templates SET enabled = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, template.id),
                    )
                elif "is_public = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_templates SET is_public = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, template.id),
                    )

        result = self.get_template(code, user_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def _log_audit(
        self,
        server_id: int,
        user_id: int,
        action: AuditLogAction,
        target_type: Optional[str] = None,
        target_id: Optional[int] = None,
    ) -> None:
        """Log an audit entry."""
        entry_id = self._generate_id()
        now = self._get_timestamp()

        self._db.execute(
            """INSERT INTO srv_audit_log 
               (id, server_id, user_id, action, target_type, target_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (entry_id, server_id, user_id, action.value, target_type, target_id, now),
        )

    def _row_to_template(self, row: Dict[str, Any]) -> ServerTemplate:
        """Convert database row to ServerTemplate model."""
        return ServerTemplate(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            creator_id=row["creator_id"],
            source_server_id=row["source_server_id"],
            code=row["code"],
            usage_count=row["usage_count"],
            is_public=bool(row["is_public"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_template_data(self, row: Dict[str, Any]) -> TemplateData:
        """Convert database row to TemplateData model."""
        channels = row["channels"]
        categories = row["categories"]
        roles = row["roles"]

        if isinstance(channels, str):
            channels = json.loads(channels) if channels else []
        if isinstance(categories, str):
            categories = json.loads(categories) if categories else []
        if isinstance(roles, str):
            roles = json.loads(roles) if roles else []

        return TemplateData(
            id=row["id"],
            template_id=row["template_id"],
            channels=channels or [],
            categories=categories or [],
            roles=roles or [],
            created_at=row["created_at"],
        )
