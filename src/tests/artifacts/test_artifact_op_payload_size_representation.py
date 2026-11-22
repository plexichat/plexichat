"""Op payload size cap must measure the representation that is actually persisted.

``append_artifact_op`` guards the durable op log with a byte-size cap. A payload
of non-ASCII text is much larger once ``json.dumps`` escapes it to ``\\uXXXX``
(the default ``ensure_ascii=True`` used when writing the row) than when it is
measured with ``ensure_ascii=False``. Measuring the compact form lets a payload
pass the cap while persisting at nearly twice the allowed size.
"""

import json

from src.core.artifacts import repository
from src.core.artifacts.repository import append_artifact_op, get_artifact


def test_op_size_cap_measures_persisted_representation(db, artifact_builder):
    artifact = artifact_builder(db, author_id=1, artifact_type="upload")
    assert get_artifact(db, artifact.id) is not None

    cap = repository._MAX_ARTIFACT_OP_BYTES
    # A BMP non-ASCII char is 3 bytes as UTF-8 but 6 bytes once the default
    # ``json.dumps`` escapes it to ``\uXXXX``. Pick N so the compact
    # (ensure_ascii=False) measurement lands just under the cap while the form
    # that is actually stored (default json.dumps) is well over it.
    overhead = len('{"t": ""}')
    n = (cap - overhead) // 3
    data = {"t": "中" * n}

    measured = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    persisted = len(json.dumps(data).encode("utf-8"))
    assert measured <= cap < persisted, (measured, persisted, cap)

    # The oversized-in-storage payload must be rejected by the cap.
    assert append_artifact_op(db, artifact.id, "edit", 1, data) is None


def test_op_size_cap_still_accepts_legitimate_payload(db, artifact_builder):
    artifact = artifact_builder(db, author_id=1, artifact_type="upload")
    small = {"t": "中" * 64}
    seq = append_artifact_op(db, artifact.id, "edit", 1, small)
    assert seq is not None
