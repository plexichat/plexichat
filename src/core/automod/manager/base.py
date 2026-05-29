from typing import Dict, Any

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager
from ..models import (
    RuleType,
    ActionType,
)
from ..rules import (
    KeywordRule,
    RegexRule,
    MessageSpamRule,
    MentionSpamRule,
    InviteLinkRule,
    ExternalLinkRule,
    CapsPercentageRule,
    MassEmojiRule,
    RepeatedCharsRule,
    AIModerationRule,
)
from ..actions import (
    DeleteMessageAction,
    TimeoutUserAction,
    KickUserAction,
    BanUserAction,
    AlertModeratorsAction,
)
from ..ai import OpenAIAdapter, PerspectiveAdapter, CustomAdapter
from .rules import RuleOpsMixin
from .evaluation import EvaluationMixin
from .actions import ActionMixin
from .exemptions import ExemptionMixin
from .reputation import ReputationMixin
from .audit import AuditMixin
from .tracking import TrackingMixin


class AutoModManager(
    RuleOpsMixin,
    EvaluationMixin,
    ActionMixin,
    ExemptionMixin,
    ReputationMixin,
    AuditMixin,
    TrackingMixin,
    BaseManager,
):
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
        RuleType.AI_MODERATION: AIModerationRule,
    }

    ACTION_CLASSES = {
        ActionType.DELETE_MESSAGE: DeleteMessageAction,
        ActionType.TIMEOUT_USER: TimeoutUserAction,
        ActionType.KICK_USER: KickUserAction,
        ActionType.BAN_USER: BanUserAction,
        ActionType.ALERT_MODERATORS: AlertModeratorsAction,
    }

    def __init__(
        self, db, servers_module=None, messaging_module=None, notifications_module=None
    ):
        super().__init__(db)
        self._servers = servers_module
        self._messaging = messaging_module
        self._notifications = notifications_module
        self._config = self._load_config()
        self._ai_adapters = {}

        self._init_ai_adapters()

        logger.info("AutoMod module initialized")

    def _load_config(self) -> Dict[str, Any]:
        defaults = {
            "enabled": True,
            "default_actions": ["delete_message", "alert_moderators"],
            "rate_limit_window": 60,
            "reputation_decay_rate": 1.0,
            "reputation_decay_interval": 86400,
            "max_violations_before_action": 1,
        }

        automod_config = config.get("automod", {})
        return {**defaults, **automod_config}

    def _init_ai_adapters(self):
        ai_config = self._config.get("ai", {})

        if ai_config.get("openai", {}).get("api_key"):
            self._ai_adapters["openai"] = OpenAIAdapter(ai_config["openai"])

        if ai_config.get("perspective", {}).get("api_key"):
            self._ai_adapters["perspective"] = PerspectiveAdapter(
                ai_config["perspective"]
            )

        if ai_config.get("custom", {}).get("endpoint_url"):
            self._ai_adapters["custom"] = CustomAdapter(ai_config["custom"])

    def reload_config(self) -> None:
        self._config = self._load_config()
        self._ai_adapters = {}
        self._init_ai_adapters()
