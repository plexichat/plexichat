"""Regression: mention parsing must scan the message content only once.

``parse_mentions`` sits on the notification fan-out hot path and runs for every
message.  It used to issue five independent ``finditer`` passes over the same
text (user / role / channel / everyone / here).  This test instruments every
compiled pattern in the parser module, asserts the content is scanned exactly
once, and pins the exact mention output so a combined pass cannot silently
change behaviour.
"""

import re

import pytest

from src.core.notifications import parser as mention_parser
from src.core.notifications.models import MentionType

CONTENT = (
    "hi <@123> and <@&456> and <#789> @everyone @here <@999> <@&888> <#777> @everyone"
)


def _full_result(content):
    """Exact behavioural snapshot: (type, id, raw, start, end) per mention."""
    return [
        (m.mention_type, m.target_id, m.raw_text, m.start_pos, m.end_pos)
        for m in mention_parser.parse_mentions(content)
    ]


class _SpyPattern:
    """Wrap a compiled pattern and count ``finditer`` scans of the text."""

    def __init__(self, real, counter):
        self._real = real
        self._counter = counter

    def finditer(self, text):
        self._counter.append(text)
        return self._real.finditer(text)

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.mark.notifications
class TestMentionParserSinglePass:
    def test_content_is_scanned_exactly_once(self, monkeypatch):
        # Snapshot behaviour with the real (unpatched) patterns first.
        expected = _full_result(CONTENT)
        assert [m[0] for m in expected] == [
            MentionType.USER,
            MentionType.ROLE,
            MentionType.CHANNEL,
            MentionType.EVERYONE,
            MentionType.HERE,
            MentionType.USER,
            MentionType.ROLE,
            MentionType.CHANNEL,
            MentionType.EVERYONE,
        ]

        counter = []
        patched = 0
        for name, value in list(vars(mention_parser).items()):
            if isinstance(value, re.Pattern):
                monkeypatch.setattr(mention_parser, name, _SpyPattern(value, counter))
                patched += 1
        assert patched, "no compiled regex found in the mention parser"

        mentions = mention_parser.parse_mentions(CONTENT)

        assert len(counter) == 1, f"content scanned {len(counter)} times: {counter!r}"
        actual = [
            (m.mention_type, m.target_id, m.raw_text, m.start_pos, m.end_pos)
            for m in mentions
        ]
        assert actual == expected
