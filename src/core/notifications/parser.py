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
from typing import List, Optional

from .models import Mention, MentionType
from src.core.security.hardening import HardeningViolation, parse_id

# SECURITY: use an explicit ASCII digit class instead of ``\d``. Python's
# ``\d`` also matches Unicode decimal digits (Arabic-Indic ``٤``, fullwidth
# ``４``, ...), and ``int()`` maps them onto the same value as their ASCII
# counterparts. Clients highlight/render mentions with ASCII-only patterns, so
# accepting a homoglyph id here is a parser differential: ``<@٤٢>`` would
# notify the real user 42 on the backend while displaying as plain text in the
# UI. Only canonical ASCII digits may form a mention id.
USER_MENTION_PATTERN = re.compile(r"<@([0-9]+)>")
ROLE_MENTION_PATTERN = re.compile(r"<@&([0-9]+)>")
CHANNEL_MENTION_PATTERN = re.compile(r"<#([0-9]+)>")
EVERYONE_PATTERN = re.compile(r"(?<!\w)@everyone\b")
HERE_PATTERN = re.compile(r"(?<!\w)@here\b")

# ``2**63 - 1`` (the largest snowflake) has 19 decimal digits. Bounding the
# token length keeps an out-of-range id from ever reaching int() (Python raises
# ValueError above 4300 digits, which previously crashed notification
# creation) and rejects leading-zero-padded, non-canonical ids that clients do
# not render as mentions.
_MAX_ID_DIGITS = 19


def _parse_target_id(raw: str) -> Optional[int]:
    """Return a canonical snowflake id, or ``None`` for an unusable token.

    ``parse_id`` enforces the ASCII grammar and the ``[1, 2**63 - 1]`` snowflake
    range. A token that cannot name a real user/role/channel is ignored so it
    fails closed instead of being treated as a mention of a bogus id.
    """
    if not raw or len(raw) > _MAX_ID_DIGITS:
        return None
    if len(raw) > 1 and raw[0] == "0":
        return None
    try:
        return parse_id(raw, "mention id")
    except (HardeningViolation, ValueError):
        # Out-of-range/zero ids and any unexpected conversion failure are all
        # unusable mention targets. Never fail open on a malformed token.
        return None


def parse_mentions(content: str) -> List[Mention]:
    """
    Parse all mentions from message content.

    Args:
        content: Message content to parse

    Returns:
        List of Mention objects found in content. Tokens whose id is not a
        canonical, in-range snowflake are ignored (they cannot reference a
        real object and would create a client/server parser differential).
    """
    if not content:
        return []

    mentions = []

    for match in USER_MENTION_PATTERN.finditer(content):
        target_id = _parse_target_id(match.group(1))
        if target_id is None:
            continue
        mentions.append(
            Mention(
                mention_type=MentionType.USER,
                target_id=target_id,
                raw_text=match.group(0),
                start_pos=match.start(),
                end_pos=match.end(),
            )
        )

    for match in ROLE_MENTION_PATTERN.finditer(content):
        target_id = _parse_target_id(match.group(1))
        if target_id is None:
            continue
        mentions.append(
            Mention(
                mention_type=MentionType.ROLE,
                target_id=target_id,
                raw_text=match.group(0),
                start_pos=match.start(),
                end_pos=match.end(),
            )
        )

    for match in CHANNEL_MENTION_PATTERN.finditer(content):
        target_id = _parse_target_id(match.group(1))
        if target_id is None:
            continue
        mentions.append(
            Mention(
                mention_type=MentionType.CHANNEL,
                target_id=target_id,
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
