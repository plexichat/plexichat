"""The artifact conversion size cap must come from a bounded config value."""

import pytest

from src.core.artifacts.manager import ArtifactManager
from src.core.security.hardening import HardeningViolation


class _DB:
    pass


@pytest.mark.parametrize("value", [0, -1, 10241, 10**9, "abc"])
def test_rejects_bad_size_cap(value):
    mgr = ArtifactManager(_DB(), {"max_artifact_size_mb": value})
    with pytest.raises((HardeningViolation, ValueError)):
        mgr.convert_upload_to_artifact(
            {"attachment_id": 1, "size": 10}, conversation_id=1, author_id=1
        )


def test_rejects_negative_declared_size():
    mgr = ArtifactManager(_DB(), {})
    with pytest.raises(ValueError, match="invalid size"):
        mgr.convert_upload_to_artifact(
            {"attachment_id": 1, "size": -1}, conversation_id=1, author_id=1
        )
