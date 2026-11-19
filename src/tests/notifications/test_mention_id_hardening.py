"""Regression tests for canonical, bounded mention-id parsing.

The core mention parser is also called directly (internal callers, the
notification manager, and tests) rather than only through an API schema, so it
must reject malformed ids itself. Previously it used ``\\d+`` (which also
matches Unicode decimal digits) and called ``int()`` with no range or length
bound, allowing:

* a parser differential where ``<@٤٢>`` notified the real user 42 while the UI
  rendered it as plain text;
* unbounded, out-of-snowflake-range ids reaching lookups and arithmetic;
* an unhandled ``ValueError`` (and therefore a notification-path crash) for a
  token longer than Python's 4300-digit ``int()`` limit.
"""

import pytest

from src.core.notifications.models import MentionType
from src.core.notifications.parser import parse_mentions
from src.core.security.hardening import SNOWFLAKE_MAX


@pytest.mark.notifications
class TestMentionIdHardening:
    """Mention ids must be canonical ASCII snowflakes in range."""

    @pytest.mark.parametrize(
        "content",
        [
            "<@٤٢>",  # Arabic-Indic digits for "42"
            "<@４２>",  # fullwidth digits for "42"
            "<@&٤٢>",  # role mention
            "<#٤٢>",  # channel mention
        ],
    )
    def test_unicode_digit_homoglyphs_are_not_mentions(self, content):
        assert parse_mentions(content) == []

    @pytest.mark.parametrize(
        "content",
        [
            "<@0>",  # zero is not a snowflake
            "<@-1>",  # signs are not parsed at all
            f"<@{SNOWFLAKE_MAX + 1}>",  # just past snowflake max
            "<@999999999999999999999999999999>",  # far out of range
            f"<@&{SNOWFLAKE_MAX + 1}>",  # role
            f"<#{SNOWFLAKE_MAX + 1}>",  # channel
        ],
    )
    def test_out_of_range_ids_are_not_mentions(self, content):
        assert parse_mentions(content) == []

    def test_leading_zero_ids_are_not_mentions(self):
        assert parse_mentions("<@00042>") == []

    def test_pathological_length_does_not_crash_or_parse(self):
        # Python's int() refuses strings over 4300 digits; the old parser let
        # that ValueError escape and crash notification creation.
        token = "<@" + "9" * 5000 + ">"
        assert parse_mentions(token) == []

    def test_boundary_snowflake_is_accepted(self):
        mentions = parse_mentions(f"<@{SNOWFLAKE_MAX}>")
        assert len(mentions) == 1
        assert mentions[0].mention_type == MentionType.USER
        assert mentions[0].target_id == SNOWFLAKE_MAX

    def test_valid_ascii_mentions_still_parse(self):
        mentions = parse_mentions("<@123> <@&456> <#789> @everyone")
        assert [m.mention_type for m in mentions] == [
            MentionType.USER,
            MentionType.ROLE,
            MentionType.CHANNEL,
            MentionType.EVERYONE,
        ]
        assert [m.target_id for m in mentions[:3]] == [123, 456, 789]

    def test_homoglyph_among_valid_mentions_is_skipped(self):
        mentions = parse_mentions("<@1> <@٢> <@3> <@٤>")
        assert [m.target_id for m in mentions] == [1, 3]
