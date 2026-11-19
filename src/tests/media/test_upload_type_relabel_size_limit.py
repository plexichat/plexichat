"""Regression: magic-byte re-detection must not let a client choose a looser
per-type size limit by mislabelling the upload.

``upload_file`` validated size against the *declared* type (e.g.
``text/plain`` -> 25 MB document bucket). ``_do_upload`` then detected a PNG
from the magic bytes and re-labelled the file ``image/png`` (10 MB bucket)
without re-running the size check, so an 11-20 MB image declared as plain
text bypassed the image limit. The streamed path had the same hole.
"""

import pytest

from src.core.media._config import DEFAULT_ALLOWED_TYPES, DEFAULT_SIZE_LIMITS
from src.core.media._upload import _UploadMixin
from src.core.media._validation import _ValidationMixin
from src.core.media.models import MediaType

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class _Stop(Exception):
    """Halts _do_upload right after the resolved-type size check."""


class _SizeCheckProbe(_UploadMixin, _ValidationMixin):
    def __init__(self):
        self._config = {
            "allowed_types": DEFAULT_ALLOWED_TYPES,
            "size_limits": DEFAULT_SIZE_LIMITS,
        }
        self.size_checks: list[tuple[int, MediaType | str]] = []

    def _validate_file_size(self, size: int, media_type):  # type: ignore[override]
        self.size_checks.append((size, media_type))
        if len(self.size_checks) >= 2:
            # Stop before storage/DB work; we only care about the checks.
            raise _Stop()
        return super()._validate_file_size(size, media_type)

    def _check_rate_limit(self, *args, **kwargs):
        return None

    def _reserve_storage_quota(self, *args, **kwargs):
        return None

    def _release_storage_quota(self, *args, **kwargs):
        return None


def test_relabelled_image_is_revalidated_against_image_size_limit():
    probe = _SizeCheckProbe()
    payload = PNG_MAGIC + b"\x00" * (11 * 1024 * 1024)  # >10 MB image, <25 MB document

    with pytest.raises(_Stop):
        probe.upload_file(1, payload, "big.txt", "text/plain")

    # The first check used the declared (document) bucket; the re-check must
    # use the type actually detected from the magic bytes (image).
    assert probe.size_checks[0][1] == MediaType.DOCUMENT
    assert probe.size_checks[1][1] == MediaType.IMAGE


def test_honest_type_only_validates_size_once():
    probe = _SizeCheckProbe()
    with pytest.raises(_Stop):
        probe.upload_file(1, PNG_MAGIC + b"\x00" * 64, "ok.png", "image/png")
    assert all(media_type == MediaType.IMAGE for _, media_type in probe.size_checks)
