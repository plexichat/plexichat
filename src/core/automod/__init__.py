"""
AutoMod module - Zero-friction API for auto-moderation.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import automod
    automod.setup(db, servers, messaging, notifications)

    # In any other file (use directly)
    from src.core import automod
    result = automod.check_message(server_id, channel_id, user_id, content)
"""

from typing import Optional, List, Dict, Any

from .models import (
    Rule,
    RuleType,
    RuleAction,
    RuleMatch,
    ActionType,
    Violation,
    ViolationSeverity,
    AuditEntry,
    UserReputation,
    Exemption,
    CheckResult,
    AICheckResult,
    AIBackendType,
    BulkScanResult,
)
from .exceptions import (
    AutoModError,
    RuleNotFoundError,
    RuleValidationError,
    RuleDisabledError,
    ActionExecutionError,
    ExemptionError,
    ViolationNotFoundError,
    ReputationError,
    AIBackendError,
    AIBackendUnavailableError,
    AIBackendTimeoutError,
    RateLimitExceededError,
    ConfigurationError,
    ServerNotFoundError,
    ChannelNotFoundError,
    UserNotFoundError,
    PermissionDeniedError,
)

__all__ = [
    # Models
    "Rule",
    "RuleType",
    "RuleAction",
    "RuleMatch",
    "ActionType",
    "Violation",
    "ViolationSeverity",
    "AuditEntry",
    "UserReputation",
    "Exemption",
    "CheckResult",
    "AICheckResult",
    "AIBackendType",
    "BulkScanResult",
    # Exceptions
    "AutoModError",
    "RuleNotFoundError",
    "RuleValidationError",
    "RuleDisabledError",
    "ActionExecutionError",
    "ExemptionError",
    "ViolationNotFoundError",
    "ReputationError",
    "AIBackendError",
    "AIBackendUnavailableError",
    "AIBackendTimeoutError",
    "RateLimitExceededError",
    "ConfigurationError",
    "ServerNotFoundError",
    "ChannelNotFoundError",
    "UserNotFoundError",
    "PermissionDeniedError",
    # Setup
    "setup",
    # Core operations
    "check_message",
    "check_user",
    "process_violation",
    # Rule management
    "create_rule",
    "get_rule",
    "update_rule",
    "delete_rule",
    "get_server_rules",
    "set_rule_enabled",
    # Exemptions
    "add_exemption",
    "remove_exemption",
    # Violations and reputation
    "get_violations",
    "get_user_reputation",
    "decay_reputation",
    # Audit
    "get_audit_log",
    # Actions
    "trigger_action",
    # Bulk operations
    "scan_messages_bulk",
    # AI moderation
    "check_ai",
]

_manager = None
_setup_complete = False


def setup(db, servers_module=None, messaging_module=None, notifications_module=None):
    """
    Initialize the automod module.

    Args:
        db: Database instance (must be connected)
        servers_module: Servers module for kicks/bans
        messaging_module: Messaging module for message operations
        notifications_module: Notifications module for alerts
    """
    global _manager, _setup_complete

    from .manager import AutoModManager

    _manager = AutoModManager(db, servers_module, messaging_module, notifications_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "AutoMod module not initialized. Call automod.setup(db) first."
        )
    return _manager


def check_message(
    server_id: int,
    channel_id: int,
    user_id: int,
    content: str,
    message_id: Optional[int] = None,
    context: Optional[Dict[str, Any]] = None
) -> CheckResult:
    """Check a message against all enabled rules."""
    return _get_manager().check_message(
        server_id, channel_id, user_id, content, message_id, context
    )


def check_user(user_id: int, server_id: int) -> Dict[str, Any]:
    """Get user's automod status for a server."""
    return _get_manager().check_user(user_id, server_id)


def process_violation(
    server_id: int,
    channel_id: int,
    user_id: int,
    message_id: Optional[int],
    match: RuleMatch,
    actions: List[RuleAction],
    context: Optional[Dict[str, Any]] = None
) -> Violation:
    """Process a violation and execute actions."""
    return _get_manager().process_violation(
        server_id, channel_id, user_id, message_id, match, actions, context
    )


def create_rule(
    user_id: int,
    server_id: int,
    name: str,
    rule_type: RuleType,
    rule_config: Dict[str, Any],
    actions: List[Dict[str, Any]],
    exempt_roles: Optional[List[int]] = None,
    exempt_channels: Optional[List[int]] = None,
    priority: int = 0,
    check_all: bool = False
) -> Rule:
    """Create a new automod rule."""
    return _get_manager().create_rule(
        user_id, server_id, name, rule_type, rule_config, actions,
        exempt_roles, exempt_channels, priority, check_all
    )


def get_rule(rule_id: int) -> Optional[Rule]:
    """Get a rule by ID."""
    return _get_manager().get_rule(rule_id)


def update_rule(
    user_id: int,
    rule_id: int,
    name: Optional[str] = None,
    rule_config: Optional[Dict[str, Any]] = None,
    actions: Optional[List[Dict[str, Any]]] = None,
    exempt_roles: Optional[List[int]] = None,
    exempt_channels: Optional[List[int]] = None,
    priority: Optional[int] = None,
    check_all: Optional[bool] = None
) -> Rule:
    """Update an existing rule."""
    return _get_manager().update_rule(
        user_id, rule_id, name, rule_config, actions,
        exempt_roles, exempt_channels, priority, check_all
    )


def delete_rule(user_id: int, rule_id: int) -> bool:
    """Delete a rule."""
    return _get_manager().delete_rule(user_id, rule_id)


def get_server_rules(server_id: int) -> List[Rule]:
    """Get all rules for a server."""
    return _get_manager().get_server_rules(server_id)


def set_rule_enabled(user_id: int, rule_id: int, enabled: bool) -> Rule:
    """Enable or disable a rule."""
    return _get_manager().set_rule_enabled(user_id, rule_id, enabled)


def add_exemption(
    user_id: int,
    server_id: int,
    target_type: str,
    target_id: int,
    rule_id: Optional[int] = None
) -> Exemption:
    """Add an exemption from automod rules."""
    return _get_manager().add_exemption(user_id, server_id, target_type, target_id, rule_id)


def remove_exemption(user_id: int, exemption_id: int) -> bool:
    """Remove an exemption."""
    return _get_manager().remove_exemption(user_id, exemption_id)


def get_violations(
    server_id: int,
    user_id: Optional[int] = None,
    limit: int = 50,
    before_id: Optional[int] = None
) -> List[Violation]:
    """Get violations for a server."""
    return _get_manager().get_violations(server_id, user_id, limit, before_id)


def get_user_reputation(user_id: int, server_id: int) -> UserReputation:
    """Get user's reputation score for a server."""
    return _get_manager().get_user_reputation(user_id, server_id)


def decay_reputation(server_id: Optional[int] = None) -> int:
    """Apply reputation decay to restore scores over time."""
    return _get_manager().decay_reputation(server_id)


def get_audit_log(
    server_id: int,
    limit: int = 50,
    before_id: Optional[int] = None,
    action_type: Optional[ActionType] = None
) -> List[AuditEntry]:
    """Get automod audit log entries."""
    return _get_manager().get_audit_log(server_id, limit, before_id, action_type)


def trigger_action(
    user_id: int,
    server_id: int,
    target_user_id: int,
    action_type: ActionType,
    reason: str,
    duration_seconds: Optional[int] = None,
    context: Optional[Dict[str, Any]] = None
) -> bool:
    """Manually trigger an automod action."""
    return _get_manager().trigger_action(
        user_id, server_id, target_user_id, action_type, reason, duration_seconds, context
    )


def scan_messages_bulk(
    server_id: int,
    channel_id: int,
    message_ids: List[int],
    context: Optional[Dict[str, Any]] = None
) -> BulkScanResult:
    """Scan multiple messages for violations."""
    return _get_manager().scan_messages_bulk(server_id, channel_id, message_ids, context)


def check_ai(
    content: str,
    backend: str = "openai",
    context: Optional[Dict[str, Any]] = None
) -> AICheckResult:
    """Check content using AI moderation backend."""
    return _get_manager().check_ai(content, backend, context)
