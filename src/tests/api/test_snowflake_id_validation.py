"""Direct checks for the ``SnowflakeID`` / ``SnowflakeIDParam`` validators.

The OpenAPI ratchet (``test_openapi_snowflake_ids``) asserts the declared
schema of the whole app; this pins the two types directly. In particular it
guards the ``json_schema_input_schema`` on the plain validator: without it the
types emit no schema at all and the param no longer advertises ``string``.

It also pins the raw-value checks the plain validator exists for. The previous
``after``-validator ran *after* the ``int`` branch coerced the value, so a
``bool`` reached it as ``1`` and slipped through.
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from src.api.schemas.common import SnowflakeID, SnowflakeIDParam


def _declared_types(schema: dict) -> set:
    types: set = set()
    if "type" in schema:
        types.add(schema["type"])
    for key in ("anyOf", "oneOf", "allOf"):
        for branch in schema.get(key, []) or []:
            types |= _declared_types(branch)
    return types


def test_snowflake_id_declares_integer_or_string():
    types = _declared_types(TypeAdapter(SnowflakeID).json_schema())
    assert types == {"integer", "string"}


def test_snowflake_id_param_declares_integer_or_string():
    types = _declared_types(TypeAdapter(SnowflakeIDParam).json_schema())
    assert types == {"integer", "string"}


@pytest.mark.parametrize("value", [True, False])
def test_snowflake_id_rejects_bool(value):
    with pytest.raises(ValidationError):
        TypeAdapter(SnowflakeID).validate_python(value)


@pytest.mark.parametrize("value", [-1, "-5"])
def test_snowflake_id_rejects_negative(value):
    with pytest.raises(ValidationError):
        TypeAdapter(SnowflakeID).validate_python(value)


@pytest.mark.parametrize("value", [-1, "-5"])
def test_snowflake_id_param_rejects_negative(value):
    with pytest.raises(ValidationError):
        TypeAdapter(SnowflakeIDParam).validate_python(value)


@pytest.mark.parametrize(
    "value,expected",
    [
        (123, "123"),
        ("123", "123"),
        ("  123  ", "123"),
        ("00123", "123"),
        ("  00123  ", "123"),
        ("+123", "123"),
        ("00", "0"),
        ("-0", "0"),
    ],
)
def test_snowflake_id_normalises_to_string(value, expected):
    assert TypeAdapter(SnowflakeID).validate_python(value) == expected


@pytest.mark.parametrize("value,expected", [(123, 123), ("123", 123)])
def test_snowflake_id_param_normalises_to_int(value, expected):
    assert TypeAdapter(SnowflakeIDParam).validate_python(value) == expected
