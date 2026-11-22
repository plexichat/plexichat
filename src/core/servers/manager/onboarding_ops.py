"""
Onboarding manager - Handles welcome screens and onboarding flows.
"""

import time
import json
from typing import Optional, List, Dict, Any

import utils.config as config
from src.utils.encryption import generate_snowflake_id

from ..models import (
    WelcomeScreen,
    OnboardingStep,
    OnboardingProgress,
    OnboardingStepType,
    AuditLogAction,
)
from ..exceptions import (
    ServerNotFoundError,
    OnboardingStepNotFoundError,
    OnboardingError,
    ChannelNotFoundError,
    RoleNotFoundError,
    PermissionDeniedError,
)

#: Bound every role-id IN list.  SQLite builds with the legacy 999-variable
#: ceiling still need room for the extra ``server_id`` predicate, and a
#: whitelist must not be able to turn one set-based lookup into an oversized
#: statement.  Chunking also keeps the query plan a set of index probes.
_ROLE_LOOKUP_CHUNK_SIZE = 500


def _resolve_whitelist_roles(db, role_ids, server_id):
    """Resolve many role ids with bounded, set-based queries.

    Returns a ``{role_id: row}`` mapping for non-deleted roles belonging to
    ``server_id``.  Ids are de-duplicated (a whitelist may repeat a role) and
    bound in chunks of at most ``_ROLE_LOOKUP_CHUNK_SIZE``.

    This helper deliberately resolves *rows only*: authorization and rank
    decisions stay with the caller so batched resolution cannot weaken the
    per-role hierarchy gate (see ``_validate_step_options``).
    """
    unique_ids = []
    seen = set()
    for rid in role_ids:
        try:
            rid = int(rid)
        except (TypeError, ValueError):
            # A malformed option resolves to no role, matching the old
            # ``get_role`` lookup missing the row.
            continue
        if rid not in seen:
            seen.add(rid)
            unique_ids.append(rid)
    resolved = {}
    for start in range(0, len(unique_ids), _ROLE_LOOKUP_CHUNK_SIZE):
        chunk = unique_ids[start : start + _ROLE_LOOKUP_CHUNK_SIZE]
        placeholders = ",".join("?" for _ in chunk)
        rows = db.fetch_all(
            f"SELECT * FROM srv_roles WHERE id IN ({placeholders}) "
            "AND server_id = ? AND deleted = 0",
            (*chunk, server_id),
        )
        for row in rows or []:
            resolved[int(row["id"])] = row
    return resolved


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
        if self._server_manager.get_member(server_id, user_id) is None:
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
        *,
        _out: Optional[Dict[int, Any]] = None,
    ):
        """Validate step options based on step type.

        ``_out`` optionally captures the exact stored permission payload of
        every whitelisted role *from the same read that authorized it*. The
        completion path binds its role-assignment INSERT to these snapshots so
        a role widened after the grantability check cannot be handed out with
        scopes the whitelist author never held (compare-and-swap, N745/N642).
        """
        if step_type == OnboardingStepType.SELECT_ROLES:
            role_ids = options.get("role_ids", [])
            # Bulk self-service grant: the whitelist is the only authorization
            # on the completion path. Record who authored it so completion can
            # re-validate rank + grantability against that principal's CURRENT
            # state. A role's permissions may be edited after the whitelist is
            # stored, which otherwise lets a member self-assign grants the
            # author never held -- a grantability bypass via this bulk path.
            options["_whitelist_author"] = user_id
            max_options = self._config.get("max_step_options", 25)
            if len(role_ids) > max_options:
                raise OnboardingError(f"Maximum {max_options} role options allowed")

            # A SELECT_ROLES step is a bulk grant: every member who completes it
            # may assign itself every role on this whitelist without ever going
            # through members.manage_roles. Whoever defines the whitelist must
            # therefore be able to manage each offered role. Without this a
            # low-ranked moderator holding onboarding.manage could whitelist a
            # role above their own rank and self-assign it through onboarding,
            # bypassing the role hierarchy entirely.
            #
            # Rank alone is not enough. ``assign_roles_from_whitelist`` is the
            # only role-grant path that skips ``check_permissions_grantable``:
            # the whitelist *is* the authorization. A moderator who holds
            # onboarding.manage but not roles.manage could otherwise whitelist a
            # role carrying roles.manage (positioned below their own, so the rank
            # gate passes), complete the step themselves and self-assign it --
            # gaining a permission they cannot grant by any other route. Require
            # the step author to possess every permission the whitelisted roles
            # carry, mirroring the grantability rule the normal assign/create/
            # update paths already enforce.
            from ..permission_utils import can_manage_role, check_permissions_grantable

            server = self._server_manager.get_server(server_id, user_id)
            is_owner = server is not None and server.owner_id == user_id
            user_roles = self._server_manager._get_member_role_rows(server_id, user_id)
            creator_permissions = self._server_manager.get_permissions(
                user_id, server_id
            )

            # Whitelist validation used to call ``get_role`` once per option;
            # each call issued a role read plus a membership read, so a full
            # step with ``max_step_options`` roles cost 2N queries. Resolve the
            # whole whitelist with bounded IN batches instead. The membership
            # precondition of ``get_role`` is checked once here, and every
            # role still goes through the same rank check below.
            if self._server_manager._is_member(server_id, user_id):
                roles_by_id = _resolve_whitelist_roles(self._db, role_ids, server_id)
            else:
                roles_by_id = {}

            for role_id in role_ids:
                try:
                    role = roles_by_id.get(int(role_id))
                except (TypeError, ValueError):
                    role = None
                if not role or int(role["server_id"]) != int(server_id):
                    raise RoleNotFoundError(f"Role {role_id} not found")
                if not can_manage_role(
                    user_roles, {"position": role["position"]}, is_owner
                ):
                    raise OnboardingError(
                        f"Cannot offer role {role_id} at or above your own rank"
                    )
                # The self-service assign path hands out whatever the role
                # carries, so a whitelist author must hold those grants. This
                # closes the gap where a moderator with only onboarding.manage
                # could self-assign a lower-ranked role that grants powers they
                # do not have (roles.manage, members.manage_roles, ...).
                role_permissions = role["permissions"]
                if isinstance(role_permissions, str):
                    try:
                        role_permissions = (
                            json.loads(role_permissions) if role_permissions else {}
                        )
                    except (TypeError, ValueError):
                        raise OnboardingError(f"Role {role_id} has invalid permissions")
                check_permissions_grantable(creator_permissions, role_permissions)
                if _out is not None:
                    # Capture the raw (pre-parse) payload from the SAME row the
                    # grantability decision above used. Only a str is a usable
                    # CAS value; anything else fails closed at completion.
                    raw_snapshot = role["permissions"]
                    _out[int(role_id)] = (
                        raw_snapshot if isinstance(raw_snapshot, str) else None
                    )

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
        if self._server_manager.get_member(row["server_id"], user_id) is None:
            return None

        return self._row_to_step(row)

    def get_onboarding_steps(
        self, user_id: int, server_id: int
    ) -> List[OnboardingStep]:
        """Get all onboarding steps for a server."""
        if self._server_manager.get_member(server_id, user_id) is None:
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
            # Only these columns are ever appended to ``updates`` above, so
            # the allow-list stays in sync with the builder by construction.
            allowed_columns = {
                "title",
                "description",
                "required",
                "options",
                "position",
            }
            # Single atomic UPDATE: one statement assigns every changed
            # column plus updated_at, so a mid-update failure cannot leave a
            # half-written row and the write costs one round trip instead of
            # one per column. Column names come only from the allow-list
            # (values stay parameterized) and the SET clause is joined, so
            # the static SQL-guard audit keeps passing.
            set_columns = []
            set_params = []
            for update, value in zip(updates, params):
                col_name = update.split(" = ")[0]
                if col_name not in allowed_columns:
                    raise ValueError(f"Invalid column name: {col_name}")
                set_columns.append(col_name)
                set_params.append(value)
            now = self._get_timestamp()
            set_columns.append("updated_at")
            set_params.append(now)
            set_clause = ", ".join(f"{col} = ?" for col in set_columns)
            query = "UPDATE srv_onboarding_steps SET " + set_clause + " WHERE id = ?"
            self._db.execute(query, (*set_params, step_id))

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
        if self._server_manager.get_member(server_id, user_id) is None:
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
            step_options = step.options or {}
            allowed_roles = step_options.get("role_ids", [])
            if allowed_roles:
                # Re-run the author's rank + grantability validation against
                # CURRENT roles. Without this, an admin could raise a
                # whitelisted role's permissions after the step was saved and
                # any member completing it would inherit grants the author
                # never held -- a grantability bypass on this bulk path.
                author_id = step_options.get("_whitelist_author")
                if author_id is None:
                    raise OnboardingError(
                        "Onboarding role whitelist has no recorded author"
                    )
                # Capture the exact permission payload the grantability
                # re-check read for each whitelisted role; bind the writes below
                # to those snapshots so a role widened in the check->write gap
                # cannot escalate the completing member (N745/N642 CAS).
                _perm_snapshots: Dict[int, Any] = {}
                try:
                    self._validate_step_options(
                        int(author_id),
                        server_id,
                        step.step_type,
                        step_options,
                        _out=_perm_snapshots,
                    )
                except OnboardingError:
                    raise
                except Exception as exc:
                    raise OnboardingError(
                        "Onboarding role whitelist is no longer authorized"
                    ) from exc
            # Self-service assignment restricted to the admin-defined role
            # whitelist captured in the step's options. This intentionally
            # does not require "members.manage_roles" - the step creator's
            # whitelist is the authorization - but refuses administrator
            # roles to prevent privilege escalation.
            try:
                self._server_manager.role_handler.assign_roles_from_whitelist(
                    user_id,
                    server_id,
                    selected_roles,
                    allowed_roles,
                    permission_snapshots=_perm_snapshots,
                )
            except PermissionDeniedError as exc:
                # The write failed its compare-and-swap: a whitelisted role's
                # payload changed after the grantability decision. Fail closed.
                raise OnboardingError(
                    "Onboarding role whitelist is no longer authorized"
                ) from exc

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
