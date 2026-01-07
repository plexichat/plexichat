"""
Mention parser - Parse mentions from message content.

Supports:
- @user mentions: <@user_id>
- @role mentions: <@&role_id>
- @everyone: @everyone
- @here: @here
- #channel mentions: <#channel_id>
"""

import re
from typing import List

from .models import Mention, MentionType


USER_MENTION_PATTERN = re.compile(r"<@(\d+)>")
ROLE_MENTION_PATTERN = re.compile(r"<@&(\d+)>")
CHANNEL_MENTION_PATTERN = re.compile(r"<#(\d+)>")
EVERYONE_PATTERN = re.compile(r"@everyone\b")
HERE_PATTERN = re.compile(r"@here\b")


def parse_mentions(content: str) -> List[Mention]:
    """
    Parse all mentions from message content.

    Args:
        content: Message content to parse

    Returns:
        List of Mention objects found in content
    """
    if not content:
        return []

    mentions = []

    for match in USER_MENTION_PATTERN.finditer(content):
        mentions.append(
            Mention(
                mention_type=MentionType.USER,
                target_id=int(match.group(1)),
                raw_text=match.group(0),
                start_pos=match.start(),
                end_pos=match.end(),
            )
        )

    for match in ROLE_MENTION_PATTERN.finditer(content):
        mentions.append(
            Mention(
                mention_type=MentionType.ROLE,
                target_id=int(match.group(1)),
                raw_text=match.group(0),
                start_pos=match.start(),
                end_pos=match.end(),
            )
        )

    for match in CHANNEL_MENTION_PATTERN.finditer(content):
        mentions.append(
            Mention(
                mention_type=MentionType.CHANNEL,
                target_id=int(match.group(1)),
                raw_text=match.group(0),
                start_pos=match.start(),
                end_pos=match.end(),
            )
        )

    for match in EVERYONE_PATTERN.finditer(content):
        mentions.append(
            Mention(
                mention_type=MentionType.EVERYONE,
                target_id=None,
                raw_text=match.group(0),
                start_pos=match.start(),
                end_pos=match.end(),
            )
        )

    for match in HERE_PATTERN.finditer(content):
        mentions.append(
            Mention(
                mention_type=MentionType.HERE,
                target_id=None,
                raw_text=match.group(0),
                start_pos=match.start(),
                end_pos=match.end(),
            )
        )

    mentions.sort(key=lambda m: m.start_pos)

    return mentions


def format_user_mention(user_id: int) -> str:
    """Format a user ID as a mention string."""
    return f"<@{user_id}>"


def format_role_mention(role_id: int) -> str:
    """Format a role ID as a mention string."""
    return f"<@&{role_id}>"


def format_channel_mention(channel_id: int) -> str:
    """Format a channel ID as a mention string."""
    return f"<#{channel_id}>"
