import zipfile

import zstandard

from src.core.admin import logs as admin_logs


def _write_zstd_archive(path, text):
    with open(path, "wb") as raw:
        with zstandard.ZstdCompressor().stream_writer(raw, closefd=False) as writer:
            writer.write(text.encode())


def test_read_zstd_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_logs, "get_log_dir", lambda: tmp_path)
    archive = tmp_path / "old.log.zst"
    _write_zstd_archive(archive, "2026-11-08 12:00:00,000 - INFO - archived line\n")

    result = admin_logs.read_log_lines("old.log.zst", limit=10, offset=0)

    assert result["total_lines"] == 1
    assert result["lines"][0]["message"] == "archived line"
    assert admin_logs.list_logs()[0]["is_zipped"] is True


def test_read_legacy_zip_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_logs, "get_log_dir", lambda: tmp_path)
    archive = tmp_path / "legacy.log.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr(
            "legacy.log",
            "2026-11-08 12:00:00,000 - INFO - legacy archived line\n",
        )

    result = admin_logs.read_log_lines("legacy.log.zip", limit=10, offset=0)

    assert result["total_lines"] == 1
    assert result["lines"][0]["message"] == "legacy archived line"
