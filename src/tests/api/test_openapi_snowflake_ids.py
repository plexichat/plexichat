"""OpenAPI ratchet: declared id fields must serialize as strings.

A JS ``Number`` cannot hold a 64-bit snowflake exactly, so every schema's
``id``/``*_id``/``*_ids`` field must be a string (the ``SnowflakeID``
convention). This guards the OpenAPI-declared surface against a future route
that types a snowflake id as ``int``.

Three surfaces are checked:

* **responses** — the JSON body a client reads back must never hand it a
  lossy number;
* **request bodies** — a JS client building a JSON body from a snowflake id
  cannot do so without rounding it, so the schema must accept a string;
* **path/query params** — likewise, an ``integer`` param invites a client to
  ``Number()`` the id before interpolating it into the URL.

Ids that back an INTEGER PRIMARY KEY rowid rather than a 64-bit snowflake are
allowlisted below.
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

# Request-body id fields backed by an INTEGER PRIMARY KEY rowid; the body may
# legitimately carry a small numeric rowid.
_ALLOWED_NUMERIC_ID_REQUEST_FIELDS: dict[tuple[str, str], str] = {}

# Path/query params that are rowids rather than snowflakes. Keyed by
# (method, path, param name).
_ALLOWED_NUMERIC_ID_PARAMS: dict[tuple[str, str, str], str] = {}


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


def _schemas_reachable_from(schema: dict, root_nodes) -> set[str]:
    """Transitively collect every schema reachable from the given nodes."""
    schemas = schema.get("components", {}).get("schemas", {})
    roots: set[str] = set()
    for node in root_nodes:
        roots |= _refs_in(node)

    names: set[str] = set()
    queue = list(roots)
    while queue:
        name = queue.pop()
        if name in names or name not in schemas:
            continue
        names.add(name)
        queue.extend(_refs_in(schemas[name]))
    return names


def _operations(schema: dict):
    """Yield ``(method, path, operation)`` for every HTTP operation."""
    for path, path_item in schema.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if isinstance(operation, dict):
                yield method, path, operation


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


def _is_numeric_only(types: set[str]) -> bool:
    # ``SnowflakeID`` renders as ``anyOf: [integer, string]`` because its
    # validator accepts both; a pure ``int`` id renders without ``string``.
    # Only the latter is a precision-leak risk.
    return "integer" in types and "string" not in types


def _numeric_schema_id_fields(schema: dict, schema_names: set[str], allow) -> list[str]:
    schemas = schema.get("components", {}).get("schemas", {})
    offenders: list[str] = []
    for model_name in sorted(schema_names):
        for prop_name, prop in (
            schemas.get(model_name, {}).get("properties", {}).items()
        ):
            if not is_id_field(prop_name):
                continue
            if (model_name, prop_name) in allow:
                continue
            types = _described_types(prop)
            if _is_numeric_only(types):
                offenders.append(f"{model_name}.{prop_name} -> {sorted(types)}")
    return offenders


def test_declared_response_id_fields_are_strings(test_client):
    schema = test_client.app.openapi()
    response_schemas = _schemas_reachable_from(
        schema, [op.get("responses", {}) for _, _, op in _operations(schema)]
    )
    offenders = _numeric_schema_id_fields(
        schema, response_schemas, _ALLOWED_NUMERIC_ID_FIELDS
    )

    assert not offenders, (
        "Snowflake id response fields must serialize as strings; found "
        "numeric ones:\n" + "\n".join(offenders)
    )


def test_declared_request_body_id_fields_are_strings(test_client):
    schema = test_client.app.openapi()
    request_schemas = _schemas_reachable_from(
        schema,
        [op["requestBody"] for _, _, op in _operations(schema) if "requestBody" in op],
    )
    offenders = _numeric_schema_id_fields(
        schema, request_schemas, _ALLOWED_NUMERIC_ID_REQUEST_FIELDS
    )

    assert not offenders, (
        "Snowflake id request-body fields must accept a string so a browser "
        "client can send the id without rounding it; found numeric ones:\n"
        + "\n".join(offenders)
    )


def test_declared_param_id_fields_are_strings(test_client):
    schema = test_client.app.openapi()
    offenders: list[str] = []

    for method, path, operation in _operations(schema):
        for param in operation.get("parameters", []) or []:
            name = param.get("name", "")
            if not is_id_field(name):
                continue
            if (method, path, name) in _ALLOWED_NUMERIC_ID_PARAMS:
                continue
            types = _described_types(param.get("schema", {}))
            if _is_numeric_only(types):
                offenders.append(f"{method.upper()} {path} {name} -> {sorted(types)}")

    assert not offenders, (
        "Snowflake id path/query params must accept a string (so a browser "
        "client need not coerce the id through Number); found numeric ones:\n"
        + "\n".join(offenders)
    )
