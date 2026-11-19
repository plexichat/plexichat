"""Regression: Stash media-object id parsing must be ASCII-only.

``str.isdigit()`` is true for non-ASCII digits (e.g. the superscript ``²`` and
Arabic-Indic ``١٢٣``) while ``int()`` only accepts Unicode *decimal* digits and
maps homoglyphs onto the ASCII value. ``_resolve_media_object`` and
``_media_bytes`` used ``isdigit()`` to decide whether a reference was a numeric
media id: a superscript raised an uncaught ``ValueError`` (500), and a homoglyph
was read as numeric by one hop but passed verbatim to the filename/access
lookups by another -- a parser differential. Only plain ASCII digits may be
treated as a numeric media id.
"""

import pytest

from src.core.suite.stash import StashManager
from src.core.suite.stash.manager import StashValidationError


class _FilenameMissMedia:
    """Minimal media boundary where no numeric/filename lookup resolves."""

    def get_file(self, file_id):
        raise AssertionError("non-ASCII digits must not be read as a numeric id")

    def get_file_by_filename(self, value):
        return None

    def check_file_access(self, value, user_id):
        return False


def _manager() -> StashManager:
    return StashManager(db=None, servers_module=None, media=_FilenameMissMedia())


@pytest.mark.parametrize("bad", ["²", "١٢٣", "１２３"])
def test_resolve_media_object_rejects_non_ascii_digits(bad: str) -> None:
    # A non-ASCII "digit" is neither a numeric id nor a resolvable filename:
    # the caller must get the StashValidationError 400 mapping, never an
    # uncaught ValueError from int(bad) or a resolved numeric id.
    with pytest.raises(StashValidationError):
        _manager()._resolve_media_object(bad, owner_id=42)


@pytest.mark.parametrize("bad", ["²", "١٢٣", "１２３"])
def test_media_bytes_does_not_crash_on_non_ascii_digits(bad: str) -> None:
    assert _manager()._media_bytes(bad) is None
