"""
Base action class for automod actions.

All action implementations inherit from BaseAction.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from ..models import ACTION_TYPE_VALUES, ActionType, RuleAction, Violation

import utils.logger as logger


class BaseAction(ABC):
    """Abstract base class for all automod actions."""

    action_type: Optional[ActionType] = None

    def __init__(
        self, db, servers_module=None, messaging_module=None, notifications_module=None
    ):
        """
        Initialize the action executor.

        Args:
            db: Database instance
            servers_module: Servers module for kicks/bans
            messaging_module: Messaging module for message operations
            notifications_module: Notifications module for alerts
        """
        self._db = db
        self._servers = servers_module
        self._messaging = messaging_module
        self._notifications = notifications_module

    def _invalidate_member_authz(self, server_id, user_id) -> None:
        """Replay the cache invalidation a raw-SQL member removal bypasses.

        The system-context ban/kick executors write ``srv_members`` and
        ``srv_member_roles`` directly (there is no permission-checked manager
        call for a system actor), so they skip the servers layer's
        ``_invalidate_member_caches``. Without this replay every authorization
        tier -- the ``perms:``/``srv_permission:`` snapshots, the ``has_perm:*``
        decorator tier, the bounded ``channel_access_cache`` L1, the
        member-role-row cache, ``is_member`` and the roster pages -- keeps
        answering with the removed member's old GRANT until its TTL expires.

        ``self._servers`` is the servers module in production and a manager
        instance in unit tests; resolve whichever exposes the invalidator.
        A failure here must surface (the caller's handler logs it and refuses
        to report success), so no stale grant is silently left in place.
        """
        servers = self._servers
        if servers is not None:
            manager = getattr(servers, "_get_manager", None)
            manager = manager() if callable(manager) else servers
            invalidator = getattr(manager, "_invalidate_member_caches", None)
            if callable(invalidator):
                logger.debug(
                    "Invalidating cached authorization state for removed member "
                    "%s in server %s",
                    user_id,
                    server_id,
                )
                invalidator(server_id, user_id)

        # The permission-checked kick/ban/leave paths also call
        # ``remove_participant_from_conversations``. The raw-SQL system
        # fallback skipped it, so the ``msg_participants`` rows seeded on join
        # stayed live and the artifact export/download gate -- which grants a
        # server-scoped artifact to any participant of a channel conversation
        # bound to that server -- kept serving the server's files to a member
        # who had just been kicked or banned. Replay the same cleanup through
        # the messaging layer so its ``is_participant`` verdict cache is
        # invalidated as well.
        messaging = self._messaging
        remover = getattr(messaging, "remove_participant_from_conversations", None)
        if not callable(remover):
            return
        rows = self._db.fetch_all(
            "SELECT conversation_id FROM srv_channels "
            "WHERE server_id = ? AND deleted = 0",
            (server_id,),
        )
        conversation_ids = []
        for row in rows or []:
            conversation_id = (
                row["conversation_id"] if isinstance(row, dict) else row[0]
            )
            if conversation_id:
                conversation_ids.append(conversation_id)
        if conversation_ids:
            remover(user_id, conversation_ids)

    @abstractmethod
    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Execute the action.

        Args:
            action: Action configuration
            violation: Violation that triggered this action
            context: Additional context

        Returns:
            True if action was executed successfully
        """
        pass

    def can_execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """
        Check if action can be executed.

        Args:
            action: Action configuration
            violation: Violation that triggered this action
            context: Additional context

        Returns:
            Tuple of (can_execute: bool, reason: str or None)
        """
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        # Last line of defence before an executor runs: a RuleAction can be
        # constructed directly by a caller, so re-check its action type
        # against the allowlist here instead of trusting the rule loader.
        if (
            getattr(getattr(action, "action_type", None), "value", None)
            not in ACTION_TYPE_VALUES
        ):
            return False, "unknown action type"
        return True, None
