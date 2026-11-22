"""Invite-rule candidate de-duplication must not scale quadratically.

``InviteLinkRule.check`` scans every message (automod evaluation is a hot
path). It collects URL-safe tokens from the message via ``_add()``, which
used to reject duplicates with ``code not in potential_codes`` -- a linear
list scan performed for every candidate. A message with ``n`` distinct tokens
therefore did O(n**2) work before detection even started; a handful of links
in a long post was enough to push moderation latency into the tens of
milliseconds.

The fix keeps the insertion-ordered list (output/report order is observable)
and adds a companion ``set`` for the membership test. This test pins both
properties: identical results, and near-linear work growth. It fails on the
old list-only implementation because 4x the candidates grows runtime far
faster than 4x. Timings use process CPU time and interleaved rounds so a busy
CI box cannot turn a linear path into a false positive.
"""

from __future__ import annotations

import statistics
import time

import pytest

from src.core.automod.models import ActionType, Rule, RuleAction, RuleType
from src.core.automod.rules.links import InviteLinkRule

pytestmark = [pytest.mark.unit, pytest.mark.automod]


def _rule(config=None):
    return Rule(
        id=1,
        server_id=1,
        name="invite dedup scaling test",
        rule_type=RuleType.INVITE_LINKS,
        enabled=True,
        config=config or {"block_all": True},
        actions=[RuleAction(action_type=ActionType.DELETE_MESSAGE)],
        applied_roles=[],
        exempt_roles=[],
        exempt_channels=[],
        priority=0,
        created_at=0,
        updated_at=0,
        created_by=0,
    )


def _tokens(n: int) -> str:
    """``n`` distinct 8-char alphanumeric candidates, no known invite codes."""
    return " ".join(f"{i:08x}" for i in range(n))


def _cpu_seconds(rule, content: str) -> float:
    rule.check(content, 1, 1, {})  # warm up regex caches
    start = time.process_time()
    rule.check(content, 1, 1, {})
    return time.process_time() - start


def test_invite_candidate_dedup_scales_near_linearly():
    rule = InviteLinkRule(_rule())
    small = _tokens(200)
    large = _tokens(800)  # 4x the candidates

    ratios = []
    for round_no in range(7):
        # Alternate measurement order so a transient load spike cannot always
        # land on the large input.
        if round_no % 2:
            t_large = _cpu_seconds(rule, large)
            t_small = _cpu_seconds(rule, small)
        else:
            t_small = _cpu_seconds(rule, small)
            t_large = _cpu_seconds(rule, large)
        ratios.append(t_large / max(t_small, 1e-9))
    ratio = statistics.median(ratios)

    # Linear work => ratio near 4. The old list-scan dedup grew ~O(n**2)
    # (measured ~10x here). 6.5 leaves ample room for scheduler noise while
    # still catching the quadratic path.
    assert ratio < 6.5, (
        f"invite candidate dedup scales superlinearly: "
        f"median 800/200-token CPU-time ratio {ratio:.2f}x "
        f"(rounds: {', '.join(f'{r:.1f}' for r in ratios)})"
    )


def test_invite_candidate_dedup_preserves_order_and_result():
    """De-duplication must keep the first-occurrence order of candidates."""
    rule = InviteLinkRule(_rule({"block_all": True, "allowed_codes": []}))
    # A known invite code appears twice; duplicate handling must not change
    # which code is reported or the order in which codes are emitted.
    known = {"Abc12345", "Zyx98765"}
    content = "hello Abc12345 middle Zyx98765 tail Abc12345"
    result = rule.check(content, 1, 1, {"known_invite_codes": list(known)})
    assert result.matched is True
    assert "Abc12345" in result.matched_content
    assert "Zyx98765" in result.matched_content
    assert result.matched_content.index("Abc12345") < result.matched_content.index(
        "Zyx98765"
    )
