"""
Auto-moderation data models.

Defines all data structures for rules, actions, violations, and audit entries.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any
from src.core.base import SnowflakeID


class RuleType(Enum):
    """Types of automod rules."""

    KEYWORD = "keyword"
    REGEX = "regex"
    MENTION_SPAM = "mention_spam"
    MESSAGE_SPAM = "message_spam"
    INVITE_LINKS = "invite_links"
    MASS_EMOJI = "mass_emoji"
    CAPS_PERCENTAGE = "caps_percentage"
    REPEATED_CHARS = "repeated_chars"
    EXTERNAL_LINKS = "external_links"
    AI_MODERATION = "ai_moderation"


class ActionType(Enum):
    """Types of actions that can be taken."""

    DELETE_MESSAGE = "delete_message"
    TIMEOUT_USER = "timeout_user"
    KICK_USER = "kick_user"
    BAN_USER = "ban_user"
    ALERT_MODERATORS = "alert_moderators"
    LOG_ONLY = "log_only"


class AIBackendType(Enum):
    """Supported AI moderation backends."""

    OPENAI = "openai"
    PERSPECTIVE = "perspective"
    CUSTOM = "custom"


class ViolationSeverity(Enum):
    """Severity levels for violations."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RuleAction:
    """Action to take when a rule is triggered."""

    action_type: ActionType
    duration_seconds: Optional[int] = None
    reason: Optional[str] = None
    notify_user: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Rule:
    """Automod rule definition."""

    id: SnowflakeID
    server_id: SnowflakeID
    name: str
    rule_type: RuleType
    enabled: bool
    config: Dict[str, Any]
    actions: List[RuleAction]
    applied_roles: List[SnowflakeID]
    exempt_roles: List[SnowflakeID]
    exempt_channels: List[SnowflakeID]
    priority: int
    created_at: int
    updated_at: int
    created_by: SnowflakeID
    check_all: bool = False


@dataclass
class RuleMatch:
    """Result of a rule check."""

    rule_id: SnowflakeID
    rule_type: RuleType
    matched: bool
    matched_content: Optional[str] = None
    match_details: Dict[str, Any] = field(default_factory=dict)
    severity: ViolationSeverity = ViolationSeverity.MEDIUM


@dataclass
class Violation:
    """Record of a rule violation."""

    id: SnowflakeID
    server_id: SnowflakeID
    channel_id: SnowflakeID
    user_id: SnowflakeID
    message_id: Optional[SnowflakeID]
    rule_id: SnowflakeID
    rule_type: RuleType
    matched_content: str
    actions_taken: List[ActionType]
    severity: ViolationSeverity
    created_at: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditEntry:
    """Audit log entry for automod actions."""

    id: SnowflakeID
    server_id: SnowflakeID
    action_type: ActionType
    target_user_id: SnowflakeID
    moderator_id: Optional[SnowflakeID]
    rule_id: Optional[SnowflakeID]
    reason: str
    metadata: Dict[str, Any]
    created_at: int


@dataclass
class UserReputation:
    """User reputation score for a server."""

    user_id: SnowflakeID
    server_id: SnowflakeID
    score: float
    violation_count: int
    last_violation_at: Optional[int]
    last_decay_at: int
    created_at: int
    updated_at: int


@dataclass
class Exemption:
    """Exemption from automod rules."""

    id: SnowflakeID
    server_id: SnowflakeID
    rule_id: Optional[SnowflakeID]
    target_type: str
    target_id: SnowflakeID
    created_at: int
    created_by: SnowflakeID


@dataclass
class CheckResult:
    """Result of checking a message against all rules."""

    passed: bool
    violations: List[RuleMatch]
    actions_to_take: List[RuleAction]
    should_delete: bool = False
    should_timeout: bool = False
    timeout_duration: Optional[int] = None


@dataclass
class AICheckResult:
    """Result from AI moderation backend."""

    flagged: bool
    categories: Dict[str, bool]
    scores: Dict[str, float]
    backend: AIBackendType
    raw_response: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BulkScanResult:
    """Result of bulk message scanning."""

    total_scanned: int
    violations_found: int
    messages_flagged: List[SnowflakeID]
    user_violations: Dict[SnowflakeID, int]
    scan_duration_ms: int
