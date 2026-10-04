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

For responses and request bodies the walk also covers two shapes the named
component-schema check cannot see: **inline** objects (a literal
``properties`` block) and **opaque** objects (a property-less
``Dict[str, Any]``), the latter via an explicit allowlist so a new one must be
justified.

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

# Numeric id fields declared *inline* in a request body (never reached by the
# named-schema walk above). Keyed by ("METHOD /path", field) with the
# justification. Mirrors ``_ALLOWED_NUMERIC_ID_REQUEST_FIELDS`` for the
# inline surface.
_ALLOWED_NUMERIC_ID_INLINE_REQUEST_FIELDS: dict[tuple[str, str], str] = {}

# Path/query params that are rowids rather than snowflakes. Keyed by
# (method, path, param name).
_ALLOWED_NUMERIC_ID_PARAMS: dict[tuple[str, str, str], str] = {}


# 2xx JSON responses that declare no properties at all (``Dict[str, Any]`` or
# a bare ``{"type": "object"}``), so the schema walk above cannot see the
# ids they carry. Each entry must be justified as carrying no un-stringified
# snowflake id (rowid-only, boolean/scalar maps, or ids already serialized
# with ``str(...)`` / ``SnowflakeID``). A *new* free-form response fails the
# gate until it is either given a response model or allowlisted here — this is
# what stops a future route from hiding a numeric snowflake behind an opaque
# dict. Keyed by (method, path) with the justification.
# Request bodies that declare no properties at all (``Dict[str, Any]`` / a
# bare ``{"type": "object"}``). Like the response counterpart, a new opaque
# request body fails the gate until it declares a model or is allowlisted
# with a justification — otherwise it could accept a numeric snowflake the
# schema walk can never see. Keyed by (method, path).
_ALLOWED_FREEFORM_REQUEST_BODIES: dict[tuple[str, str], str] = {
    (
        "put",
        "/api/v1/users/@me/settings/bulk",
    ): "settings key/value bag scoped to the session user; no request ids",
    (
        "post",
        "/api/v1/admin/artifacts/features/{feature}",
    ): "feature settings map; server_id accepted as int|str (int()-coerced)",
}

_ALLOWED_FREEFORM_RESPONSES: dict[tuple[str, str], str] = {
    ("get", "/api/v1/ready"): "readiness probe: booleans/status only",
    ("get", "/api/v1/capabilities/artifacts"): "capability map: no ids",
    (
        "get",
        "/api/v1/servers/{server_id}/permissions",
    ): "boolean permission map",
    ("delete", "/api/v1/channels/{channel_id}"): "success boolean only",
    ("delete", "/api/v1/channels/invites/{invite_code}"): "success boolean only",
    (
        "get",
        "/api/v1/channels/{channel_id}/ratchet",
    ): "channel_id/interval ids already stringified by _stringify_ids",
    (
        "get",
        "/api/v1/channels/{channel_id}/ratchet/intervals",
    ): "channel_id/interval ids already stringified by _stringify_ids",
    (
        "post",
        "/api/v1/applications/{application_id}/regenerate-secret",
    ): "new client secret only (no ids)",
    ("get", "/api/v1/admin/migrations/status"): "migration metadata: no ids",
    (
        "post",
        "/api/v1/admin/migrations/{version}/rollback",
    ): "migration status booleans: no ids",
    ("get", "/api/v1/admin/audit/logs/export"): "export rows stringify admin_id",
    ("get", "/api/v1/admin/capabilities"): "capability map: no ids",
    (
        "get",
        "/api/v1/admin/artifacts",
    ): "items are ArtifactResponse (SnowflakeID)",
    ("delete", "/api/v1/admin/artifacts/{artifact_id}"): "success + str(id)",
    (
        "get",
        "/api/v1/admin/artifacts/features/{feature}",
    ): "server_id emitted as str; settings only",
    (
        "post",
        "/api/v1/admin/artifacts/features/{feature}",
    ): "server_id emitted as str; settings only",
    (
        "get",
        "/api/v1/artifacts/{artifact_id}/ops",
    ): "artifact_id emitted as str; ops carry no snowflakes",
    ("get", "/api/v1/public/license/challenge"): "license challenge: no ids",
    ("get", "/api/v1/public/license/export"): "license document: no snowflakes",
    ("get", "/api/v1/public/license/info"): "license info: no snowflakes",
    ("get", "/api/v1/suite/apps"): "manifest catalog: no device/user ids",
    ("get", "/api/v1/suite/home/recent"): "item ids stringified in projections",
    ("get", "/api/v1/suite/home/pinned"): "item ids stringified in projections",
    ("get", "/api/v1/suite/home/shared"): "item ids stringified in projections",
    ("get", "/api/v1/suite/home/activity"): "row ids emitted as str(...)",
    ("get", "/api/v1/suite/search"): "item ids stringified in projections",
    ("get", "/public/license/challenge"): "license challenge: no ids",
    ("get", "/public/license/export"): "license document: no snowflakes",
    ("get", "/public/license/info"): "license info: no snowflakes",
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


def _json_request_body_schemas(schema: dict):
    """Yield ``(method, path, schema_node)`` for every JSON request body."""
    for method, path, operation in _operations(schema):
        request_body = operation.get("requestBody")
        if not request_body:
            continue
        js = (request_body.get("content", {}) or {}).get("application/json")
        if js is None:
            continue
        node = js.get("schema")
        if node is not None:
            yield method, path, node


def _json_2xx_schemas(schema: dict):
    """Yield ``(method, path, schema_node)`` for every 2xx JSON response."""
    for method, path, operation in _operations(schema):
        for status, resp in (operation.get("responses", {}) or {}).items():
            if not str(status).startswith("2"):
                continue
            js = (resp or {}).get("content", {}).get("application/json")
            if js is None:
                continue
            node = js.get("schema")
            if node is not None:
                yield method, path, node


def _is_opaque_freeform(node) -> bool:
    """True when a response node is a property-less object with unconstrained
    values (``Dict[str, Any]`` renders as ``additionalProperties: true``; a
    bare ``{"type": "object"}`` leaves it unspecified) — the shape that can
    smuggle a numeric id past the named-schema walk.

    A *typed* map (``additionalProperties: {"type": "boolean"}``) is not
    opaque: it cannot carry ids, and a typed value schema is walked by
    ``_inline_id_offenders`` below.
    """
    if not isinstance(node, dict) or node.get("type") != "object":
        return False
    if node.get("properties") or node.get("$ref"):
        return False
    additional = node.get("additionalProperties")
    return additional is True or additional is None


def _inline_id_offenders(
    node, allow, path: str = "", root: str | None = None
) -> list[str]:
    """Recursively flag numeric id fields in an *inline* schema node.

    ``test_declared_*_id_fields_are_strings`` only inspect named component
    schemas reachable via ``$ref``; an inline ``properties`` block or a typed
    ``additionalProperties`` value schema never reaches them. This walk closes
    that gap, stopping at ``$ref`` because the named-schema tests own those.

    ``allow`` is keyed by ``(root, field)`` where ``root`` identifies the
    operation (``"GET /path"``) — the ``path`` argument is the human-readable
    location used in the failure message, and ``root`` stays constant through
    recursion so an entry allowlists the field wherever it appears in the tree.
    """
    if not isinstance(node, dict):
        return []
    if root is None:
        root = path
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        return []
    offenders: list[str] = []
    for prop_name, prop in (node.get("properties") or {}).items():
        where = f"{path}.{prop_name}" if path else prop_name
        types = _described_types(prop)
        if (root, prop_name) in allow or (where, prop_name) in allow:
            pass
        elif is_id_field(prop_name) and _is_numeric_only(types):
            offenders.append(f"{where} -> {sorted(types)}")
        offenders += _inline_id_offenders(prop, allow, where, root)
    items = node.get("items")
    if isinstance(items, dict):
        offenders += _inline_id_offenders(items, allow, f"{path}[]", root)
    additional = node.get("additionalProperties")
    if isinstance(additional, dict):
        offenders += _inline_id_offenders(additional, allow, f"{path}.*", root)
    for key in ("anyOf", "oneOf", "allOf"):
        for branch in node.get(key, []) or []:
            offenders += _inline_id_offenders(branch, allow, path, root)
    return offenders


def test_inline_response_id_fields_are_strings(test_client):
    """Numeric id fields must also be caught outside named schemas."""
    schema = test_client.app.openapi()
    offenders: list[str] = []
    for method, path, node in _json_2xx_schemas(schema):
        offenders += _inline_id_offenders(
            node, _ALLOWED_NUMERIC_ID_FIELDS, f"{method.upper()} {path}"
        )
    assert not offenders, (
        "Inline/returned id fields must serialize as strings; found numeric "
        "ones:\n" + "\n".join(offenders)
    )


def test_freeform_response_operations_are_allowlisted(test_client):
    """Opaque ``Dict[str, Any]`` responses must be explicitly allowlisted.

    A property-less response schema gives the id walk nothing to inspect, so
    each one has to be justified as id-free (see the
    ``_ALLOWED_FREEFORM_RESPONSES`` table). Without this, a new route
    returning a bare dict could serve
    a numeric snowflake and every other check here would stay green.
    """
    schema = test_client.app.openapi()
    offenders: list[str] = []
    for method, path, node in _json_2xx_schemas(schema):
        if not _is_opaque_freeform(node):
            continue
        if (method, path) in _ALLOWED_FREEFORM_RESPONSES:
            continue
        offenders.append(f"{method.upper()} {path} -> {node}")
    assert not offenders, (
        "Free-form (property-less) JSON responses bypass the snowflake-id "
        "schema walk; give them a response model or allowlist them with a "
        "justification:\n" + "\n".join(offenders)
    )


def test_freeform_request_body_operations_are_allowlisted(test_client):
    """Opaque ``Dict[str, Any]`` request bodies must be explicitly allowlisted.

    A property-less request-body schema gives the id walk nothing to inspect,
    so each one has to be justified as id-free (see the
    ``_ALLOWED_FREEFORM_REQUEST_BODIES`` table). Without this, a new route
    accepting a bare dict could take a numeric snowflake (already rounded by
    the browser before it was sent) and every other check here would stay
    green.
    """
    schema = test_client.app.openapi()
    offenders: list[str] = []
    for method, path, node in _json_request_body_schemas(schema):
        if not _is_opaque_freeform(node):
            continue
        if (method, path) in _ALLOWED_FREEFORM_REQUEST_BODIES:
            continue
        offenders.append(f"{method.upper()} {path} -> {node}")
    assert not offenders, (
        "Free-form (property-less) JSON request bodies bypass the snowflake-id "
        "schema walk; give them a request model or allowlist them with a "
        "justification:\n" + "\n".join(offenders)
    )


def test_inline_request_body_id_fields_are_strings(test_client):
    """Numeric id fields declared inline in a request body must be strings.

    ``test_declared_request_body_id_fields_are_strings`` only sees named
    component schemas reachable via ``$ref``; an inline ``properties`` block
    (or a typed ``additionalProperties`` value schema) never reaches it. This
    closes the request-side gap, mirroring the response-side inline walk.
    """
    schema = test_client.app.openapi()
    offenders: list[str] = []
    for method, path, node in _json_request_body_schemas(schema):
        key = f"{method.upper()} {path}"
        offenders += _inline_id_offenders(
            node, _ALLOWED_NUMERIC_ID_INLINE_REQUEST_FIELDS, key, key
        )
    assert not offenders, (
        "Inline request-body id fields must accept a string so a browser "
        "client can send the id without rounding it; found numeric ones:\n"
        + "\n".join(offenders)
    )


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
    """Every id path/query param must be able to carry the id as a string.

    A URL segment or query value is always a string, so the param's OpenAPI
    type must include ``string`` — otherwise the wire format would tell a
    browser client to coerce a 64-bit id through a JS ``number``. Both
    ``SnowflakeIDParam`` (``integer|string``) and a plain ``str`` id param
    satisfy this; a numeric-only or untyped param does not.
    """
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
            if "string" not in types:
                offenders.append(f"{method.upper()} {path} {name} -> {sorted(types)}")

    assert not offenders, (
        "Snowflake id path/query params must advertise a string type (so a "
        "browser client need not coerce the id through Number); found:\n"
        + "\n".join(offenders)
    )
