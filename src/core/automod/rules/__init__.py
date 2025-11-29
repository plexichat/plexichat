"""
AutoMod rules package - Rule type implementations.
"""

from .base import BaseRule, RuleMatch
from .keyword import KeywordRule
from .regex import RegexRule
from .spam import MessageSpamRule
from .mentions import MentionSpamRule
from .links import LinkFilterRule
from .caps import CapsPercentageRule
from .emoji import MassEmojiRule

__all__ = [
    "BaseRule",
    "RuleMatch",
    "KeywordRule",
    "RegexRule",
    "MessageSpamRule",
    "MentionSpamRule",
    "LinkFilterRule",
    "CapsPercentageRule",
    "MassEmojiRule",
]
