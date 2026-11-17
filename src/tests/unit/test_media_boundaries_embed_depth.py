"""Regression tests for embed structural boundaries (depth, width, text).

A hostile embed payload is a small JSON tree that the sanitizer recurses into.
Without explicit structural caps it can exhaust the interpreter stack or the
heap before any per-field length limit applies. These tests pin the budget
constants and the fail-closed behaviour of both the message sanitizer and the
embed-data validator.
"""

from typing import Any

import pytest

from src.core.embeds.exceptions import EmbedValidationError
from src.core.embeds.validator import (
    MAX_EMBED_NESTING_DEPTH,
    MAX_EMBED_TOTAL_NODES,
    MAX_EMBED_TOTAL_TEXT_CHARS,
    EmbedBudget,
    enforce_embed_budget,
    sanitize_message_embeds,
    validate_embed_data,
)
from src.core.embeds.validator.bounds import (
    MAX_EMBED_KEYS_PER_OBJECT,
    MAX_EMBED_LIST_ITEMS,
    GENERIC_STRING_MAX_LENGTH,
)


def _nested(depth: int):
    payload = {"title": "leaf"}
    for _ in range(depth):
        payload = {"child": payload}
    return payload


class TestStructuralConstants:
    def test_budget_constants_are_finite_and_sane(self):
        assert 0 < MAX_EMBED_NESTING_DEPTH <= 32
        assert 0 < MAX_EMBED_TOTAL_NODES <= 100_000
        assert 0 < MAX_EMBED_TOTAL_TEXT_CHARS <= 1_000_000
        assert 0 < MAX_EMBED_KEYS_PER_OBJECT <= 256
        assert 0 < MAX_EMBED_LIST_ITEMS <= 1024
        assert 0 < GENERIC_STRING_MAX_LENGTH <= 16_384


class TestEmbedBudget:
    def test_charge_node_fails_closed(self):
        budget = EmbedBudget()
        for _ in range(MAX_EMBED_TOTAL_NODES):
            budget.charge_node("node")
        with pytest.raises(EmbedValidationError, match="node budget"):
            budget.charge_node("one too many")

    def test_charge_text_enforces_per_field_limit(self):
        budget = EmbedBudget()
        with pytest.raises(EmbedValidationError, match="maximum length"):
            budget.charge_text("x" * (GENERIC_STRING_MAX_LENGTH + 1), "blob")

    def test_charge_text_enforces_total_budget(self):
        budget = EmbedBudget()
        with pytest.raises(EmbedValidationError, match="text budget"):
            budget.charge_text("x" * (MAX_EMBED_TOTAL_TEXT_CHARS + 1), "blob")


class TestEnforceEmbedBudget:
    def test_ordinary_embed_passes(self):
        enforce_embed_budget(
            {
                "title": "hi",
                "description": "world",
                "fields": [{"name": "a", "value": "b"}],
            }
        )

    def test_deep_tree_rejected(self):
        with pytest.raises(EmbedValidationError, match="nesting depth"):
            enforce_embed_budget(_nested(MAX_EMBED_NESTING_DEPTH + 3))

    def test_wide_object_rejected(self):
        payload = {f"k{i}": i for i in range(MAX_EMBED_KEYS_PER_OBJECT + 1)}
        with pytest.raises(EmbedValidationError, match="too many keys"):
            enforce_embed_budget(payload)

    def test_wide_list_rejected(self):
        payload = {"blob": [1] * (MAX_EMBED_LIST_ITEMS + 1)}
        with pytest.raises(EmbedValidationError, match="too many items"):
            enforce_embed_budget(payload)

    def test_walker_does_not_recurse(self):
        # A very deep tree must be rejected by the budget, not by RecursionError.
        with pytest.raises(EmbedValidationError):
            enforce_embed_budget(_nested(5000))


class TestMessageSanitizerBudget:
    def test_deeply_nested_embed_rejected(self):
        payload = _nested(MAX_EMBED_NESTING_DEPTH + 3)
        with pytest.raises(EmbedValidationError, match="nesting depth"):
            sanitize_message_embeds([payload])

    def test_oversized_custom_string_rejected(self):
        with pytest.raises(EmbedValidationError, match="maximum length"):
            sanitize_message_embeds(
                [{"title": "x", "custom": "a" * (GENERIC_STRING_MAX_LENGTH + 1)}]
            )

    def test_many_keys_rejected(self):
        payload: dict[str, Any] = {"title": "x"}
        payload.update({f"k{i}": i for i in range(MAX_EMBED_KEYS_PER_OBJECT + 1)})
        with pytest.raises(EmbedValidationError, match="too many keys"):
            sanitize_message_embeds([payload])

    def test_normal_embed_still_sanitized(self):
        result = sanitize_message_embeds(
            [{"title": "ok", "fields": [{"name": "n", "value": "v"}]}]
        )
        assert result is not None
        assert result[0]["title"] == "ok"
        assert result[0]["fields"][0]["value"] == "v"


class TestEmbedDataValidatorBudget:
    def test_validate_embed_data_rejects_deep_tree(self):
        with pytest.raises(EmbedValidationError, match="nesting depth"):
            validate_embed_data(_nested(MAX_EMBED_NESTING_DEPTH + 3))
