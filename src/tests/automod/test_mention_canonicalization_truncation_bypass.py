"""Regression: NFKC expansion must not push real mentions past the canonical cap.

``MentionSpamRule`` counts mentions on ``fold_invisible_for_matching(content)``,
which NFKC-folds the message and then truncates the result at
``MAX_CANONICAL_LENGTH`` (10,000). NFKC can expand ONE codepoint into many:
U+FDFA (ARABIC LIGATURE SALLALLAHOU ALAYHE WASALLAM) becomes 18 characters.
A short, schema-legal message (well under the 4,000-character content cap)
can therefore expand past 10,000 characters, so every ``<@id>`` placed after
the expansion point is cut out of the *counted* view.

The notification fan-out, however, parses mentions from the RAW content, so
those cut mentions still ping their targets. Before the fix this let a user
with no mention-spam allowance mass-ping an arbitrary number of users while
AutoMod reported zero mentions and let the message through -- a real
mass-mention count bypass, not a cosmetic parser mismatch.

The fix counts mentions on both the canonical (invisible-stripped) view and
the raw view and takes the maximum per bucket. The canonical view still catches
invisible-splice evasion (the earlier hardening); the raw view catches
canonicalization-truncation evasion. Both views are linear and already bounded
(message length / canonical cap), so no unbounded work is introduced.
"""

import pytest

from src.core.automod.canonicalization import (
    MAX_CANONICAL_LENGTH,
    fold_invisible_for_matching,
)
from src.core.automod.models import (
    ActionType,
    Rule,
    RuleAction,
    RuleType,
)
from src.core.automod.rules.mentions import MentionSpamRule
from src.core.notifications.parser import parse_mentions

# U+FDFA expands to 18 characters under NFKC -- the cheapest way to blow past
# the 10,000-character canonical cap from a short, schema-legal message.
_EXPANDER = "\ufdfa"
_PADDING_LEN = 600  # 600 * 18 = 10,800 > MAX_CANONICAL_LENGTH
_MENTION_COUNT = 20  # well over max_user_mentions=5 / max_total_mentions=10
_FIRST_ID = 100_000_000_000_000_000


def _payload():
    mentions = " ".join(f"<@{_FIRST_ID + i}>" for i in range(_MENTION_COUNT))
    return _EXPANDER * _PADDING_LEN + " " + mentions


def _rule(config):
    return Rule(
        id=1,
        server_id=1,
        name="mention spam",
        rule_type=RuleType.MENTION_SPAM,
        enabled=True,
        config=config,
        actions=[RuleAction(action_type=ActionType.DELETE_MESSAGE)],
        applied_roles=[],
        exempt_roles=[],
        exempt_channels=[],
        priority=0,
        created_at=0,
        updated_at=0,
        created_by=0,
    )


def _check(payload, **config):
    return MentionSpamRule(_rule(config)).check(payload, 1, 1)


@pytest.mark.automod
def test_payload_is_schema_legal_and_carries_real_mentions():
    """The bypass needs no oversized message and no bogus mention ids."""
    payload = _payload()
    assert len(payload) <= 4000, "message content max_length is 4000"
    assert len(parse_mentions(payload)) == _MENTION_COUNT


@pytest.mark.automod
def test_canonical_view_alone_drops_the_tail_mentions():
    """Document the exact mechanism: NFKC expansion truncates the mentions."""
    payload = _payload()
    folded = fold_invisible_for_matching(payload)
    assert len(folded) == MAX_CANONICAL_LENGTH
    assert parse_mentions(folded) == []


@pytest.mark.automod
def test_expansion_truncation_does_not_bypass_mass_mention_limit():
    """Pre-fix this message passed AutoMod while pinging 20 users."""
    match = _check(_payload(), max_user_mentions=5, max_total_mentions=10)
    assert match.matched, "20 real pings must not evade the mention count limit"
    assert match.match_details["user_mentions"] >= _MENTION_COUNT


@pytest.mark.automod
def test_invisible_splice_evasion_still_caught():
    """Counting the raw view must not regress the invisible-character fix."""
    spliced = " ".join(
        "<@" + "\u200b".join("123456789012345678") + ">" for _ in range(6)
    )
    match = _check(spliced, max_user_mentions=2, max_total_mentions=3)
    assert match.matched
    assert match.match_details["user_mentions"] == 6
