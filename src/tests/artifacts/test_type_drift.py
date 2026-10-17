"""Regression tests for artifact-type drift handling (L18).

Unknown persisted ``artifact_type`` values used to collapse silently to
``FUTURE``: the divergence was masked, and the row became unfilterable
under its real value. Unknown types must now raise at the model layer, while
row reads of legacy junk stay resilient and log the drift.
"""

import pytest

from src.core.artifacts.models import ArtifactType
from src.core.artifacts.repository import get_artifact


def test_missing_value_maps_to_future():
    """A NULL / absent type is the documented 'not yet classified' case."""
    assert ArtifactType(None) is ArtifactType.FUTURE


def test_unknown_string_raises_instead_of_silently_collapsing():
    """A real unknown string must surface, not masquerade as FUTURE."""
    with pytest.raises(ValueError, match="Unknown artifact type"):
        ArtifactType("totally-not-a-real-artifact-type")


def test_read_of_drifted_row_is_resilient(db, artifact_builder):
    """Reading a drifted row yields FUTURE instead of crashing the caller."""
    artifact = artifact_builder(db, author_id=1, artifact_type="upload")
    db.execute(
        "UPDATE artifacts SET artifact_type = ? WHERE id = ?",
        ("mystery-drift", artifact.id),
    )

    loaded = get_artifact(db, artifact.id)
    assert loaded is not None
    assert loaded.artifact_type is ArtifactType.FUTURE


def test_known_types_still_parse(db, artifact_builder):
    for raw in ("upload", "whiteboard", "transcript", "sum", "future"):
        artifact = artifact_builder(db, author_id=1, artifact_type=raw)
        loaded = get_artifact(db, artifact.id)
        assert loaded is not None
        assert loaded.artifact_type == ArtifactType(raw)
