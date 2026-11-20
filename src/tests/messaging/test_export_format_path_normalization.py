"""Regression: a caller that reaches ``request_export`` without the HTTP
route's format allowlist must not steer the artifact path.

The content generator falls back to JSON for an unknown format, but the output
path used to be built from the *raw* format string. A value such as
``"../../evil"`` therefore wrote the JSON transcript outside the private
session directory (a path-traversal primitive). The normalised format used for
content must also drive the path and the persisted metadata.
"""

from typing import Any, cast

import os

import pytest

import utils.config as config_mod
from src.core.messaging.services.export import TranscriptExportService


class _StubMessages:
    def get_by_conversation(
        self, conversation_id, limit=10000, from_ts=None, to_ts=None, ascending=False
    ):
        return [
            {
                "id": "1",
                "author_id": "42",
                "content": "hello",
                "created_at": 1700000000000,
                "edited_at": None,
                "reply_to_id": None,
            }
        ][:limit]


class _StubParticipants:
    def exists(self, conversation_id, user_id):
        return True


@pytest.fixture
def export_env(tmp_path, monkeypatch):
    real_get = config_mod.get

    def fake_get(key, default=None):
        if key == "transcript_export":
            return {
                "storage_dir": str(tmp_path),
                "rate_limit": {"requests_per_hour": 1000},
                "temporary_storage_hours": 24,
                "max_messages_per_export": 10000,
            }
        try:
            return real_get(key, default)
        except Exception:
            return default

    monkeypatch.setattr(config_mod, "get", fake_get)
    TranscriptExportService._exports.clear()
    TranscriptExportService._hydrated = True
    yield tmp_path
    TranscriptExportService._exports.clear()
    TranscriptExportService._hydrated = False


def _make_svc():
    return TranscriptExportService(
        None, cast(Any, _StubMessages()), cast(Any, _StubParticipants())
    )


@pytest.mark.parametrize("bad_format", ["../../evil", "json/../../x", "PDF", "", None])
def test_unknown_format_cannot_escape_storage_dir(export_env, bad_format):
    result = _make_svc().request_export(cast(Any, "42"), cast(Any, "7"), bad_format)
    assert result["status"] == "ready"
    assert result["format"] == "json"

    fpath = result["file_path"]
    root = os.path.realpath(str(export_env))
    assert os.path.realpath(fpath).startswith(root + os.sep)
    assert fpath.endswith(".json")
    assert os.path.basename(fpath).count("..") == 0
    assert os.path.isfile(fpath)


def test_known_formats_still_honoured(export_env):
    for fmt in ("csv", "txt", "html"):
        result = _make_svc().request_export(cast(Any, "42"), cast(Any, "7"), fmt)
        assert result["status"] == "ready"
        assert result["format"] == fmt
        assert result["file_path"].endswith(f".{fmt}")
