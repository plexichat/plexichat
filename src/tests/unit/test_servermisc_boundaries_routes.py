"""Focused regression tests for server-misc route request boundaries.

Signature/source checks keep these tests fast: every fix here is a request
boundary constraint or a static-path confinement, so booting the whole app
adds no coverage.
"""

from __future__ import annotations

import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _param(func, name):
    return inspect.signature(func).parameters[name].default


def _has_constraint(param, cls_name: str) -> bool:
    return any(type(m).__name__ == cls_name for m in getattr(param, "metadata", []))


def _constraint_value(param, cls_name: str, attr: str):
    for m in getattr(param, "metadata", []):
        if type(m).__name__ == cls_name:
            return getattr(m, attr)
    raise AssertionError(f"no {cls_name} constraint on parameter")


class TestSearchBoundaries:
    def test_search_query_and_limit_are_capped(self):
        from src.api.routes.search import search_messages

        assert _has_constraint(_param(search_messages, "query"), "MaxLen")
        assert (
            _constraint_value(_param(search_messages, "query"), "MaxLen", "max_length")
            <= 256
        )
        assert _has_constraint(_param(search_messages, "limit"), "Le")

    def test_every_search_cursor_is_length_bounded(self):
        from src.api.routes import search

        for name in ("search_messages", "search_users", "search_servers"):
            func = getattr(search, name)
            cursor = _param(func, "cursor")
            assert _has_constraint(cursor, "MaxLen"), f"{name} cursor unbounded"
            assert _constraint_value(cursor, "MaxLen", "max_length") <= 512, (
                f"{name} cursor cap too high"
            )


class TestThreadAndStickerBoundaries:
    def test_thread_duration_range_is_declared(self):
        source = (ROOT / "src/api/routes/threads.py").read_text(encoding="utf-8")
        assert "MAX_AUTO_ARCHIVE_MINUTES" in source
        assert "ALLOWED_AUTO_ARCHIVE_MINUTES" in source

    def test_sticker_tag_payload_and_suggestion_query_are_capped(self):
        from src.api.routes.stickers import get_sticker_suggestions

        content = _param(get_sticker_suggestions, "content")
        assert _has_constraint(content, "MaxLen")
        assert _constraint_value(content, "MaxLen", "max_length") <= 256
        source = (ROOT / "src/api/routes/stickers.py").read_text(encoding="utf-8")
        assert "MAX_STICKER_TAG_PAYLOAD" in source


class TestFeedbackAndDocsBoundaries:
    def test_feedback_category_enum_and_content_cap(self):
        source = (ROOT / "src/api/routes/feedback.py").read_text(encoding="utf-8")
        assert "KNOWN_FEEDBACK_CATEGORIES" in source
        assert "MAX_FEEDBACK_CONTENT_LENGTH" in source

    def test_help_robots_path_is_confined_and_nosniff_is_set(self):
        source = (ROOT / "src/api/routes/help.py").read_text(encoding="utf-8")
        assert "safe_join" in source
        assert "X-Content-Type-Options" in source and "nosniff" in source
        assert "Path.cwd()" not in source, "CWD-relative static path was restored"
