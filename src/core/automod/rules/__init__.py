"""
Automod rules package.

Exports all rule implementations.
"""

from .base import BaseRule
from .keyword import KeywordRule
from .regex import RegexRule
from .spam import MessageSpamRule
from .mentions import MentionSpamRule
from .links import InviteLinkRule, ExternalLinkRule
from .caps import CapsPercentageRule
from .emoji import MassEmojiRule
from .repeated import RepeatedCharsRule

__all__ = [
    "BaseRule",
    "KeywordRule",
    "RegexRule",
    "MessageSpamRule",
    "MentionSpamRule",
    "InviteLinkRule",
    "ExternalLinkRule",
    "CapsPercentageRule",
    "MassEmojiRule",
    "RepeatedCharsRule",
]
