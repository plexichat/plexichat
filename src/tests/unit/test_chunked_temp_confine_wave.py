"""Session temp paths read back from the DB must stay inside the temp dir."""

import os

import pytest

from src.core.media.chunked import ChunkedUploadManager
from src.core.security.hardening import HardeningViolation


class _DB:
    def fetch_one(self, *args, **kwargs):
        return None

    def fetch_all(self, *args, **kwargs):
        return []

    def execute(self, *args, **kwargs):
        return None


@pytest.fixture
def manager():
    return ChunkedUploadManager(_DB())


def test_legit_temp_path_is_returned(manager):
    path = os.path.join(manager._temp_dir, "abc.tmp")
    assert manager._confine_temp_path(path) == path


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../../etc/passwd", "/tmp/other/evil.tmp"],
)
def test_escaping_temp_path_is_rejected(manager, path):
    with pytest.raises(HardeningViolation):
        manager._confine_temp_path(path)


def test_missing_temp_path_is_rejected(manager):
    with pytest.raises(RuntimeError):
        manager._confine_temp_path(None)
