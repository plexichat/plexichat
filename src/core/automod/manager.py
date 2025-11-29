"""
AutoMod manager - Core business logic for auto-moderation.

Handles rule evaluation, action execution, and integration with other modules.
"""

import time
import json
from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

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
    BulkScanResult,
)
from .exceptions import (
    RuleNotFoundError,
    RuleValidationError,
    RuleDisabledError,
    ActionExecutionError,
    ExemptionError,
    ConfigurationError,
    PermissionDeniedError,
)
from .schema import create_tables
from .rules import (
    BaseRule,
    KeywordRule,
    RegexRule,
    MessageSpamRule,
    MentionSpamRule,
    InviteLinkRule,
    ExternalLinkRule,
    CapsPercentageRule,
    MassEmojiRule,
    RepeatedCharsRule,
)
from .actions import (
    BaseAction,
    DeleteMessageAction,
    TimeoutUserAction,
    KickUserAction,
    BanUserAction,
    AlertModeratorsAction,
)
from .ai import OpenAIAdapter, PerspectiveAdapter, CustomAdapter


class AutoModManager:
    """Core auto-moderation manager."""
    
    RULE_CLASSES = {
        RuleType.KEYWORD: KeywordRule,
        RuleType.REGEX: RegexRule,
        RuleType.MESSAGE_SPAM: MessageSpamRule,
        RuleType.MENTION_SPAM: MentionSpamRule,
        RuleType.INVITE_LINKS: InviteLinkRule,
        RuleType.EXTERNAL_LINKS: ExternalLinkRule,
        RuleType.CAPS_PERCENTAGE: CapsPercentageRule,
        RuleType.MASS_EMOJI: MassEmojiRule,
        RuleType.REPEATED_CHARS: RepeatedCharsRule,
    }
    
    ACTION_CLASSES = {
        ActionType.DELETE_MESSAGE: DeleteMessageAction,
        ActionType.TIMEOUT_USER: TimeoutUserAction,
        ActionType.KICK_USER: KickUserAction,
        ActionType.BAN_USER: BanUserAction,
        ActionType.ALERT_MODERATORS: AlertModeratorsAction,
    }
    
    def __init__(
        self,
        db,
        servers_module=None,
        messaging_module=None,
        notifications_module=None
    ):
        """
        Initialize the AutoMod manager.
        
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
        self._config = self._load_config()
        self._ai_adapters = {}
        
        create_tables(db)
        self._init_ai_adapters()
        
        logger.info("AutoMod module initialized")
    
    def _load_config(self) -> Dict[str, Any]:
        """Load automod configuration."""
        defaults = {
            "enabled": True,
            "default_actions": ["delete_message", "alert_moderators"],
            "rate_limit_window": 60,
            "reputation_decay_rate": 1.0,
            "reputation_decay_interval": 86400,
            "max_violations_before_action": 3,
        }
        
        automod_config = config.get("automod", {})
        return {**defaults, **automod_config}
    
    def _init_ai_adapters(self):
        """Initialize configured AI adapters."""
        ai_config = self._config.get("ai", {})
        
        if ai_config.get("openai", {}).get("api_key"):
            self._ai_adapters["openai"] = OpenAIAdapter(ai_config["openai"])
        
        if ai_config.get("perspective", {}).get("api_key"):
            self._ai_adapters["perspective"] = PerspectiveAdapter(ai_config["perspective"])
        
        if ai_config.get("custom", {}).get("endpoint_url"):
            self._ai_adapters["custom"] = CustomAdapter(ai_config["custom"])
    
    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)
    
    def _generate_id(self) -> int:
        """Generate a new Snowflake ID."""
        return generate_snowflake_id()
