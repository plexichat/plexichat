"""Regression: the keyword filter must see every byte the consumer delivers.

``KeywordRule.check`` used to match blocked keywords on the output of
``canonicalize_for_matching``, which truncates at ``MAX_CANONICAL_LENGTH``
(10_000). NFKC folds one compatibility codepoint (U+FDFA) into 18, so a few
hundred schema-legal characters (schema cap: 4000) can push the folded form
past the cap. A blocked keyword placed after the cut was reported as benign,
yet the send path stores/renders/copies the RAW message verbatim (message
create, ``_message_to_response``, notification previews). That is the same
filter-view-vs-consumer-view bypass class as the mass-mention one: the gate
sees a truncated canonical form while the action uses the raw content.

The fix matches on the FULL canonical form (no truncation), so obfuscated
keywords past the old cap are still caught while clean text is untouched.
"""

import pytest

from src.core.automod.canonicalization import MAX_CANONICAL_LENGTH
from src.core.automod.models import (
    ActionType,
    Rule,
    RuleAction,
    RuleType,
)
from src.core.automod.rules.keyword import KeywordRule


BLOCKED_WORD = "buycryptonow"


def _rule(config):
    return Rule(
        id=1,
        server_id=1,
        name="keyword truncation test",
        rule_type=RuleType.KEYWORD,
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


def _keyword_rule():
    return KeywordRule(
        _rule(
            {
                "keywords": [BLOCKED_WORD],
                "whole_word": True,
                "case_sensitive": False,
            }
        )
    )


# U+FDFA expands 1 -> 18 codepoints under NFKC. 600 of them expand to 10_800,
# comfortably past the 10_000 cap, with only 614 raw characters (well under
# the 4000-character message schema limit).
_EXPANDER = "\ufdfa" * 600
assert len(_EXPANDER) < 4000


def test_plain_keyword_is_matched_control():
    """Control: the filter still catches an ordinary blocked word."""
    match = _keyword_rule().check(f"please {BLOCKED_WORD.upper()} now", 1, 1)
    assert match.matched
    assert BLOCKED_WORD in match.match_details["keywords"]


def test_nfkc_expansion_past_canonical_cap_does_not_hide_keyword():
    """Pre-fix this returned no match: the word sat past the truncated view.

    The consumer (message render / notification preview) still delivered the
    raw message containing the blocked word, so the gate saw benign content
    while the prohibited content reached users.
    """
    payload = f"{_EXPANDER} {BLOCKED_WORD.upper()} "
    assert len(payload) < 4000  # schema-legal
    match = _keyword_rule().check(payload, 1, 1)
    assert match.matched
    assert BLOCKED_WORD in match.match_details["keywords"]


@pytest.mark.parametrize("separator", ["\u200b", "\u00ad", "\u2060", "\ufeff"])
def test_invisible_spliced_keyword_past_cap_is_matched(separator):
    """The full canonical view is required, not just raw + truncated.

    With a zero-width separator inside the word, neither the raw view (split
    token) nor the truncated canonical view (word beyond the cut) matches.
    Only a full-length canonical form folds it back to the blocked word.
    """
    spliced = separator.join(BLOCKED_WORD.upper())
    payload = f"{_EXPANDER} {spliced} "
    assert len(payload) < 4000
    match = _keyword_rule().check(payload, 1, 1)
    assert match.matched, repr(separator)
    assert BLOCKED_WORD in match.match_details["keywords"]


def test_clean_content_with_heavy_expansion_is_not_flagged():
    """Non-vacuity: removing the cap must not make clean text match."""
    assert not _keyword_rule().check(
        f"{_EXPANDER} all of this is legitimate prose ", 1, 1
    ).matched
    assert not _keyword_rule().check("hello world, nothing banned here", 1, 1).matched


def test_whole_word_boundary_still_enforced():
    """The fix must not weaken the whole-word policy."""
    # Substring of a larger word is not a whole-word match.
    assert not _keyword_rule().check(f"x{BLOCKED_WORD}x", 1, 1).matched
    # A standalone occurrence still is.
    assert _keyword_rule().check(f"x {BLOCKED_WORD} x", 1, 1).matched
