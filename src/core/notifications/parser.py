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
#
# One combined pattern scans the content ONCE. This is the notification
# fan-out hot path (every message), and five separate ``finditer`` passes used
# to scan the same text. The alternation is behaviour-preserving: the five
# token shapes are mutually exclusive at any given position (``<@``+digits,
# ``<@&``+digits, ``<#``+digits, ``@everyone``, ``@here``), and ``finditer``
# still yields non-overlapping matches left-to-right. Named groups recover the
# exact id/type from the single match with no second scan.
#
# The branches deliberately share their literal prefixes (``<`` for the three
# angle-bracket forms, ``@`` for everyone/here). That gives ``re`` a fast
# literal-search anchor in each branch and keeps the scan linear even on the
# common mention-free message -- a flat five-way alternation was measurably
# SLOWER than the old five passes on sparse text. No nested quantifiers are
# introduced, so the ReDoS-safe shape is unchanged.
#
# Kept as a standalone public pattern for the message-serialization caller
# (``src.api.routes.messages.helpers``) which uses ``findall`` to mirror client
# mention ids into REST payloads. ``parse_mentions`` itself no longer uses it.
USER_MENTION_PATTERN = re.compile(r"<@([0-9]+)>")

COMBINED_MENTION_PATTERN = re.compile(
    r"<(?:(?P<role>@&)|(?P<user>@)|(?P<channel>#))(?P<id>[0-9]+)>"
    r"|(?<!\w)@(?P<everyone>everyone|here)\b"
)

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

    for match in COMBINED_MENTION_PATTERN.finditer(content):
        raw = match.group(0)
        if match.group("role") is not None:
            mention_type = MentionType.ROLE
        elif match.group("user") is not None:
            mention_type = MentionType.USER
        elif match.group("channel") is not None:
            mention_type = MentionType.CHANNEL
        elif match.group("everyone") == "everyone":
            mention_type = MentionType.EVERYONE
        else:  # "@here"
            mention_type = MentionType.HERE

        if mention_type in (
            MentionType.USER,
            MentionType.ROLE,
            MentionType.CHANNEL,
        ):
            target_id = _parse_target_id(match.group("id"))
            if target_id is None:
                # Malformed/out-of-range id: ignore it so it fails closed,
                # exactly as the old per-type loops did.
                continue
        else:
            target_id = None

        mentions.append(
            Mention(
                mention_type=mention_type,
                target_id=target_id,
                raw_text=raw,
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
