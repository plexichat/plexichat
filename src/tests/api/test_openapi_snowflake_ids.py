"""OpenAPI ratchet: declared *response* id fields must serialize as strings.

A JS ``Number`` cannot hold a 64-bit snowflake exactly, so every response
schema's ``id``/``*_id``/``*_ids`` field must be a string (the ``SnowflakeID``
convention). This guards the OpenAPI-declared surface against a future route
that types a snowflake id as ``int``.

Only schemas reachable from an operation *response* are checked, so request
bodies (which legitimately accept integers) are not flagged. Ids that back an
INTEGER PRIMARY KEY rowid rather than a 64-bit snowflake are allowlisted below.
"""

from src.core.events.snowflakes import is_id_field

# Fields whose value is an INTEGER PRIMARY KEY rowid (or other non-snowflake
# counter), so a numeric JSON representation is correct. Keyed by
# (schema name, field name) with the justification.
_ALLOWED_NUMERIC_ID_FIELDS: dict[tuple[str, str], str] = {
    ("FeedbackResponse", "id"): "feedback row id is an INTEGER PRIMARY KEY",
    ("BannedUsernameResponse", "id"): "username_blacklist rowid",
    ("BotRequestResponse", "id"): "app_bot_requests rowid",
    ("AdminBotRequestEntry", "id"): "app_bot_requests rowid",
}


def _refs_in(node) -> set[str]:
    """Return every component-schema name referenced anywhere under ``node``."""
    refs: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            refs.add(ref.rsplit("/", 1)[-1])
        for value in node.values():
            refs |= _refs_in(value)
    elif isinstance(node, list):
        for value in node:
            refs |= _refs_in(value)
    return refs


def _response_schema_names(schema: dict) -> set[str]:
    """Transitively collect every schema reachable from an operation response."""
    schemas = schema.get("components", {}).get("schemas", {})
    roots: set[str] = set()
    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if isinstance(operation, dict):
                roots |= _refs_in(operation.get("responses", {}))

    names: set[str] = set()
    queue = list(roots)
    while queue:
        name = queue.pop()
        if name in names or name not in schemas:
            continue
        names.add(name)
        queue.extend(_refs_in(schemas[name]))
    return names


def _described_types(prop: dict) -> set[str]:
    """Collect the JSON schema ``type`` values for a property."""
    types: set[str] = set()
    if "type" in prop:
        types.add(prop["type"])
    for key in ("anyOf", "oneOf", "allOf"):
        for branch in prop.get(key, []) or []:
            types |= _described_types(branch)
    items = prop.get("items")
    if isinstance(items, dict):
        types |= _described_types(items)
    return types


def test_declared_response_id_fields_are_strings(test_client):
    schema = test_client.app.openapi()
    schemas = schema.get("components", {}).get("schemas", {})
    response_schemas = _response_schema_names(schema)
    offenders: list[str] = []

    for model_name in sorted(response_schemas):
        for prop_name, prop in (
            schemas.get(model_name, {}).get("properties", {}).items()
        ):
            if not is_id_field(prop_name):
                continue
            if (model_name, prop_name) in _ALLOWED_NUMERIC_ID_FIELDS:
                continue
            types = _described_types(prop)
            # ``SnowflakeID`` renders as ``anyOf: [integer, string]`` because
            # its validator accepts both; a pure ``int`` id renders without
            # ``string``. Only the latter is a leak.
            if "integer" in types and "string" not in types:
                offenders.append(f"{model_name}.{prop_name} -> {sorted(types)}")

    assert not offenders, (
        "Snowflake id response fields must serialize as strings; found "
        "numeric ones:\n" + "\n".join(offenders)
    )
