"""
AutoMod models - Dataclasses for all automod-related entities.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


class RuleType(Enum):
    """Type of automod rule."""
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
    """Type of action to take on violation."""
    DELETE_MESSAGE = "delete_message"
    TIMEOUT_USER = "timeout_user"
    KICK_USER = "kick_user"
    BAN_USER = "ban_user"
    ALERT_MODERATORS = "alert_moderators"
    LOG_ONLY = "log_only"


class AIBackend(Enum):
    """AI moderation backend type."""
    OPENAI = "openai"
    PERSPECTIVE = "perspective"
    CUSTOM = "custom"


class ViolationSeverity(Enum):
    """Severity level of a violation."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RuleAction:
    """Action configuration for a rule."""
    action_type: ActionType
    duration_seconds: Optional[int] = None
    reason: Optional[str] = None
    notify_user: bool = True
    delete_message_history_hours: Optional[int] = None


@dataclass
class Rule:
    """Represents an automod rule."""
    id: int
    server_id: int
    rule_type: RuleType
    name: str
    enabled: bool = True
    priority: int = 0
    trigger_config: Dict[str, Any] = field(default_factory=dict)
    actions: List[RuleAction] = field(default_factory=list)
    exempt_roles: List[int] = field(default_factory=list)
    exempt_channels: List[int] = field(default_factory=list)
    cooldown_seconds: int = 0
    check_all_rules: bool = False
    created_at: int = 0
    updated_at: int = 0
    created_by: int = 0


@dataclass
class Violation:
    """Represents a rule violation."""
    id: int
    server_id: int
    channel_id: int
    user_id: int
    message_id: Optional[int]
    rule_id: int
    rule_type: RuleType
    severity: ViolationSeverity
    matched_content: Optional[str] = None
    trigger_details: Dict[str, Any] = field(default_factory=dict)
    actions_taken: List[ActionType] = field(default_factory=list)
    created_at: int = 0


@dataclass
class AuditEntry:
    """Represents an automod audit log entry."""
    id: int
    server_id: int
    action_type: ActionType
    user_id: int
    target_user_id: Optional[int]
    rule_id: Optional[int]
    violation_id: Optional[int]
    message_id: Optional[int]
    channel_id: Optional[int]
    reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: int = 0


@dataclass
class UserReputation:
    """Represents a user's reputation score in a server."""
    user_id: int
    server_id: int
    score: float = 100.0
    total_violations: int = 0
    last_violation_at: Optional[int] = None
    last_decay_at: Optional[int] = None
    created_at: int = 0
    updated_at: int = 0


@dataclass
class CheckResult:
    """Result of checking a message against rules."""
    passed: bool
    violations: List[Violation] = field(default_factory=list)
    actions_to_take: List[RuleAction] = field(default_factory=list)
    matched_rules: List[Rule] = field(default_factory=list)
    processing_time_ms: float = 0.0


@dataclass
class AICheckResult:
    """Result from AI moderation backend."""
    flagged: bool
    categories: Dict[str, bool] = field(default_factory=dict)
    scores: Dict[str, float] = field(default_factory=dict)
    raw_response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@dataclass
class ServerConfig:
    """Per-server automod configuration."""
    server_id: int
    enabled: bool = True
    log_channel_id: Optional[int] = None
    alert_channel_id: Optional[int] = None
    alert_webhook_url: Optional[str] = None
    default_timeout_duration: int = 300
    reputation_enabled: bool = True
    reputation_decay_rate: float = 1.0
    reputation_decay_interval_hours: int = 24
    ai_backend: Optional[AIBackend] = None
    ai_enabled: bool = False
    created_at: int = 0
    updated_at: int = 0
