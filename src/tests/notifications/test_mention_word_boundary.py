"""Word-boundary guards for @everyone / @here mention parsing.

Agent 43 F14: the mention parser used to match ``@everyone`` anywhere, so an
address such as ``mail@everyone.example`` was parsed as a broadcast mention
(spurious notifications and permission checks). ``@here`` already carried a
``(?<!\\w)`` guard; these tests pin that ``@everyone`` now behaves the same
while every legitimate standalone form still parses.
"""

from src.core.notifications import MentionType


def _everyone(notification_manager, content):
    return [
        m
        for m in notification_manager.parse_mentions(content)
        if m.mention_type == MentionType.EVERYONE
    ]


def _here(notification_manager, content):
    return [
        m
        for m in notification_manager.parse_mentions(content)
        if m.mention_type == MentionType.HERE
    ]


class TestEveryoneWordBoundary:
    def test_email_address_is_not_an_everyone_mention(self, notification_manager):
        assert _everyone(notification_manager, "ping mail@everyone.example now") == []

    def test_trailing_word_prefix_is_not_an_everyone_mention(
        self, notification_manager
    ):
        assert _everyone(notification_manager, "contact foo@everyone") == []

    def test_longer_identifier_is_not_an_everyone_mention(self, notification_manager):
        assert _everyone(notification_manager, "xeveryone@everyone") == []

    def test_standalone_everyone_still_matches(self, notification_manager):
        for content in (
            "@everyone",
            "hey @everyone",
            "(@everyone)",
            "hi,@everyone",
        ):
            assert _everyone(notification_manager, content), content

    def test_here_shares_the_same_boundary_behavior(self, notification_manager):
        # Guard against the two patterns drifting apart again.
        assert _here(notification_manager, "mail@here.example") == []
        assert _here(notification_manager, "hey @here")
