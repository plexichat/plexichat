"""Interaction payload must be bounded before it is serialized/persisted.

``InteractionHandler.create_interaction`` accepts an attacker-influenced
``data`` object (command ``options``, ``resolved`` bodies, ``custom_id``, ...)
and forwards it verbatim to ``json.dumps`` -> ``encrypt_data`` -> the
``app_interactions`` row.  Before the fix there was no ceiling at all on the
option count, option value length, nesting depth or total serialized size, so a
single crafted payload could force an unbounded allocation during
serialization/encryption and persist/dispatch an arbitrarily large blob.

These tests drive the real handler with a stubbed crypto backend and a fake DB
and pin the fail-closed bounds while confirming a normal payload still passes.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.core.applications.exceptions import InteractionValidationError
from src.core.applications.interactions.handler import InteractionHandler
from src.core.applications.models import InteractionType


def _handler():
    db = MagicMock()
    handler = InteractionHandler(db, {})
    return handler, db


def _create(handler, data):
    return handler.create_interaction(
        application_id=1,
        interaction_type=InteractionType.APPLICATION_COMMAND,
        user_id=100,
        data=data,
    )


@pytest.fixture(autouse=True)
def _fast_crypto():
    """Keep the pre-fix demonstration cheap (no real AEAD on huge blobs)."""
    with patch(
        "src.core.applications.interactions.handler.encrypt_data",
        side_effect=lambda text, **kwargs: "enc:" + str(len(text)),
    ):
        yield


def test_unbounded_option_count_is_rejected():
    """Thousands of options must not be serialized/persisted."""
    handler, db = _handler()
    data = {
        "name": "flood",
        "type": 1,
        "options": [{"name": str(i), "value": "1"} for i in range(5000)],
    }

    with pytest.raises(InteractionValidationError):
        _create(handler, data)
    db.execute.assert_not_called()


def test_oversized_option_value_is_rejected():
    """A single enormous option value must not reach serialization."""
    handler, db = _handler()
    data = {"name": "big", "options": [{"name": "a", "value": "A" * 100_000}]}

    with pytest.raises(InteractionValidationError):
        _create(handler, data)
    db.execute.assert_not_called()


def test_deeply_nested_options_are_rejected():
    """Deeply nested sub-options must not be walked/serialized."""
    handler, db = _handler()
    root = {"name": "nested"}
    node = root
    for _ in range(60):
        child = {"name": "n", "value": "1"}
        node["options"] = [child]
        node = child

    with pytest.raises(InteractionValidationError):
        _create(handler, root)
    db.execute.assert_not_called()


def test_options_of_wrong_type_is_rejected():
    """``options`` must be a list, not an opaque scalar (type confusion)."""
    handler, db = _handler()
    with pytest.raises(InteractionValidationError):
        _create(handler, {"name": "x", "options": "not-a-list"})
    db.execute.assert_not_called()


def test_aggregate_size_is_rejected_even_when_each_value_fits():
    """Per-value limits alone are insufficient; total size is bounded."""
    handler, db = _handler()
    # Each value is below a typical per-option cap but the aggregate is large.
    data = {"name": "many", "options": [{"name": str(i), "value": "x" * 3000} for i in range(25)]}

    with pytest.raises(InteractionValidationError):
        _create(handler, data)
    db.execute.assert_not_called()


def test_legitimate_interaction_data_still_accepted():
    """A normal command payload is stored and parsed unchanged."""
    handler, db = _handler()
    data = {
        "id": 123,
        "name": "roll",
        "type": 1,
        "options": [{"name": "sides", "type": 4, "value": 6}],
    }

    interaction = _create(handler, data)

    db.execute.assert_called_once()
    assert interaction.data is not None
    assert interaction.data.name == "roll"
    assert interaction.data.options == data["options"]


def test_persisted_serialization_size_is_bounded_not_compact_form():
    """Validation must measure the exact string that is persisted/encrypted.

    ``create_interaction`` stores ``json.dumps(data)`` with its default,
    space-padded separators.  A 2048-node payload can stay within every
    per-field limit while its *compact* form is just under the cap yet the
    default form actually persisted is well over it.  Validation uses the same
    serialization as persistence so the cap binds the stored blob.
    """
    handler, db = _handler()
    data = {"k" + str(i): ("x" * 21) for i in range(2047)}
    for i in range(560):
        data["k" + str(i)] = "x" * 22

    assert len(json.dumps(data, separators=(",", ":"))) <= 64 * 1024
    assert len(json.dumps(data)) > 64 * 1024

    with pytest.raises(InteractionValidationError):
        _create(handler, data)
    db.execute.assert_not_called()
