"""Focused tests for batched admin log reads and the composite filter index.

Before: each admin log page re-opened, decompressed and re-parsed the entire
file, even for a different page of the same filter, and the line-format regex
was recompiled on every request. After: a bounded composite index keyed on
(path, file version, level, search) caches one filtered read per filter, a
batched reader amortises per-line overhead, and a single call can read several
files. These tests pin reuse, invalidation and unchanged filtering semantics.
"""

import os
import time

import pytest

from src.core.admin import logs as admin_logs


@pytest.fixture(autouse=True)
def _clean_index():
    admin_logs.clear_log_filter_index()
    yield
    admin_logs.clear_log_filter_index()


def _log_text(levels=("INFO", "WARNING", "ERROR"), count=3):
    lines = []
    for i in range(count):
        for level in levels:
            lines.append(
                f"2026-11-16 12:00:{i:02d},000 - {level} - message {level} {i}"
            )
    return "\n".join(lines) + "\n"


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    (tmp_path / "app.log").write_text(_log_text())
    (tmp_path / "other.log").write_text(_log_text(levels=("INFO",), count=2))
    monkeypatch.setattr(admin_logs, "get_log_dir", lambda: tmp_path)
    return tmp_path


def _count_reads(monkeypatch):
    calls = []
    original = admin_logs._read_filtered

    def counting(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(admin_logs, "_read_filtered", counting)
    return calls


def test_same_filter_different_pages_reuse_index(log_dir, monkeypatch):
    calls = _count_reads(monkeypatch)

    first = admin_logs.read_log_lines("app.log", limit=2, offset=0)
    second = admin_logs.read_log_lines("app.log", limit=2, offset=2)

    assert len(calls) == 1, "unchanged file + same filter must be parsed once"
    assert first["total_lines"] == second["total_lines"] == 9
    assert first["lines"][0]["message"] != second["lines"][0]["message"]


def test_mtime_change_invalidates_index(log_dir, monkeypatch):
    calls = _count_reads(monkeypatch)

    admin_logs.read_log_lines("app.log", limit=2)
    future = time.time() + 5
    os.utime(log_dir / "app.log", (future, future))
    admin_logs.read_log_lines("app.log", limit=2)

    assert len(calls) == 2, "edited file must be re-parsed"


def test_composite_key_separates_level_and_search(log_dir, monkeypatch):
    calls = _count_reads(monkeypatch)

    admin_logs.read_log_lines("app.log", limit=5, level_filter="error")
    admin_logs.read_log_lines("app.log", limit=5, level_filter="warning")
    admin_logs.read_log_lines("app.log", limit=5, search="message ERROR")
    admin_logs.read_log_lines("app.log", limit=5, search="message ERROR")

    assert len(calls) == 3, "distinct (level, search) filters get own entries"


def test_level_and_search_filtering_is_preserved(log_dir):
    errors = admin_logs.read_log_lines("app.log", limit=50, level_filter="error")
    assert errors["total_lines"] == 3
    assert all(line["level"] == "ERROR" for line in errors["lines"])

    searched = admin_logs.read_log_lines("app.log", limit=50, search="message ERROR 1")
    assert searched["total_lines"] == 1
    assert searched["lines"][0]["level"] == "ERROR"


def test_batch_reads_multiple_files(log_dir, monkeypatch):
    calls = _count_reads(monkeypatch)

    result = admin_logs.read_log_lines_batch(["app.log", "other.log"], limit=50)

    assert set(result) == {"app.log", "other.log"}
    assert result["app.log"]["total_lines"] == 9
    assert result["other.log"]["total_lines"] == 2
    assert len(calls) == 2, "each distinct file is parsed once"


def test_index_is_bounded(log_dir, monkeypatch):
    assert admin_logs.index_stats() == {
        "entries": 0,
        "max_entries": admin_logs._MAX_INDEXED_FILTERS,
    }
    monkeypatch.setattr(admin_logs, "_read_filtered", lambda *a, **k: (0, []))
    path = log_dir / "app.log"
    for i in range(admin_logs._MAX_INDEXED_FILTERS + 10):
        admin_logs._indexed_filtered(path, "app.log", (1,), None, f"term-{i}")
    assert admin_logs.index_stats()["entries"] == admin_logs._MAX_INDEXED_FILTERS
