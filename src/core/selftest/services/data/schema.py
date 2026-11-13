import random
import re
import secrets
from typing import Any, Dict, Optional

from .base import DataGeneratorBase


class SchemaBodyMixin(DataGeneratorBase):
    """Convert OpenAPI schemas into example request bodies."""

    def generate_from_schema(
        self, schema: Dict[str, Any], prop_name: Optional[str] = None
    ) -> Any:
        if "$ref" in schema:
            ref_path = schema["$ref"].split("/")
            ref_schema = self.ctx.openapi_spec
            for part in ref_path[1:]:
                ref_schema = ref_schema.get(part, {})
            return self.generate_from_schema(ref_schema, prop_name)

        if "allOf" in schema:
            merged = {}
            for sub in schema["allOf"]:
                res = self.generate_from_schema(sub, prop_name)
                if isinstance(res, dict):
                    merged.update(res)
            return merged

        for key in ("anyOf", "oneOf"):
            if key in schema:
                return self.generate_from_schema(schema[key][0], prop_name)

        type_ = schema.get("type")

        if not type_ and "properties" in schema:
            type_ = "object"

        if type_ == "object":
            obj = {}
            required = schema.get("required", [])
            properties = schema.get("properties", {})

            for p_name in required:
                if p_name in properties:
                    obj[p_name] = self.generate_from_schema(properties[p_name], p_name)

            for p_name, prop_schema in properties.items():
                if p_name not in obj:
                    if any(
                        x in p_name.lower()
                        for x in (
                            "id",
                            "name",
                            "type",
                            "content",
                            "username",
                            "email",
                            "password",
                            "status",
                            "category",
                        )
                    ):
                        obj[p_name] = self.generate_from_schema(prop_schema, p_name)

            if not obj and properties:
                p_name = next(iter(properties))
                obj[p_name] = self.generate_from_schema(properties[p_name], p_name)

            return obj

        elif type_ == "array":
            items = schema.get("items", {})
            return [self.generate_from_schema(items, prop_name)]

        elif type_ == "string":
            if "enum" in schema:
                return schema["enum"][0]

            if "pattern" in schema and prop_name:
                pattern = schema["pattern"]
                if pattern.startswith("^") and pattern.endswith("$"):
                    inner = pattern[1:-1]
                    alt_match = re.match(
                        r"^\(([a-zA-Z0-9_.-]+(?:\|[a-zA-Z0-9_.-]+)*)\)$", inner
                    )
                    if alt_match:
                        return alt_match.group(1).split("|")[0]
                if pattern == "^[a-zA-Z0-9_]+$":
                    return "test_value"
                if pattern == "^[a-fA-F0-9]{6}$":
                    return "ff0000"
                if pattern == "^[a-zA-Z]+$":
                    return "test"
                if pattern.startswith("^#"):
                    return "#ff0000"

            if prop_name:
                pn = prop_name.lower()
                if pn == "username":
                    return f"user_{random.randint(1000, 9999)}"
                if pn == "email":
                    return f"test_{random.randint(1000, 9999)}@example.com"
                if pn == "password":
                    return self.ctx._test_password
                if pn == "current_password":
                    return self.ctx._test_password
                if "id" in pn:
                    if "server" in pn:
                        return str(self.ctx.test_server_id or self.generate_snowflake())
                    if "channel" in pn:
                        return str(
                            self.ctx.test_channel_id or self.generate_snowflake()
                        )
                    if "message" in pn:
                        return str(
                            self.ctx.test_message_id or self.generate_snowflake()
                        )
                    if "user" in pn:
                        return str(self.ctx.test_user_id or self.generate_snowflake())
                    return str(self.generate_snowflake())
                if "content" in pn:
                    return "Test content " + secrets.token_hex(4)
                if "reason" in pn:
                    return "Self-test reason"
                if "topic" in pn:
                    return "Self-test topic"
                if "description" in pn:
                    return "Self-test description"
                if "name" in pn:
                    return "test_name_" + secrets.token_hex(4)
                if "reported_user_id" in pn:
                    return str(self.ctx.test_user_id)
                if "recipient_id" in pn:
                    return str(self.ctx.test_user_id)
                if "status" in pn:
                    return "online"
                if "category" in pn:
                    return "other"
                if "code" in pn:
                    return "123456"
                if "hash" in pn:
                    return "a" * 64
                if "version" in pn:
                    return "1.0.0a1"
                if "method" in pn:
                    return "GET"
                if "question" in pn:
                    return "Test poll question?"
                if "rule_type" in pn:
                    return "keyword"
                if "trigger_type" in pn:
                    return "keyword"
                if "action_type" in pn:
                    return "delete_message"
                if "scope_type" in pn:
                    return "ip"
                if "emoji" in pn and "id" not in pn:
                    return "smile"
                if "fingerprint" in pn:
                    # Device fingerprints require min_length=8 server-side.
                    return "selftest-fp-" + secrets.token_hex(4)

            fmt = schema.get("format")
            if fmt == "email":
                return f"test_{random.randint(1000, 9999)}@example.com"
            if fmt == "password":
                return self.ctx._test_password
            if fmt == "date-time":
                return "2024-01-01T00:00:00Z"

            title = schema.get("title", "").lower()
            if "id" in title or "snowflake" in title:
                return str(self.generate_snowflake())

            value = "test"
            min_len = schema.get("minLength") or 0
            try:
                min_len = int(min_len)
            except (TypeError, ValueError):
                min_len = 0
            if min_len and len(value) < min_len:
                # Pad to the schema minimum without breaking emails.
                pad = "a" * (min_len - len(value))
                if "@" in value:
                    local, _, domain = value.partition("@")
                    value = f"{local}{pad}@{domain}"
                else:
                    value = f"{value}{pad}"
            max_len = schema.get("maxLength")
            try:
                max_len = int(max_len) if max_len is not None else None
            except (TypeError, ValueError):
                max_len = None
            if max_len and len(value) > max_len:
                value = value[:max_len]
            return value

        elif type_ in ("integer", "number"):
            if prop_name:
                pn = prop_name.lower()
                if "status" in pn and "code" in pn:
                    return 200
                if "id" in pn:
                    if "server" in pn:
                        return self.ctx.test_server_id or 1
                    if "channel" in pn:
                        return self.ctx.test_channel_id or 1
                    if "message" in pn:
                        return self.ctx.test_message_id or 1
                    if "user" in pn:
                        return self.ctx.test_user_id or 1
                    if "poll" in pn:
                        return self.ctx.test_poll_id or 1
                    if "rule" in pn:
                        return self.ctx.test_automod_rule_id or 1
                    if "thread" in pn:
                        return self.ctx.test_thread_id or 1
            # Honor schema bounds for generated numbers (e.g. tuning ranges
            # like glide_request_timeout_ms [10, 600000]); the old constant
            # ``1`` tripped every minimum above 1.
            minimum = schema.get("minimum")
            maximum = schema.get("maximum")
            try:
                minimum = None if minimum is None else int(minimum)
            except (TypeError, ValueError):
                minimum = None
            try:
                maximum = None if maximum is None else int(maximum)
            except (TypeError, ValueError):
                maximum = None
            if minimum is not None or maximum is not None:
                if minimum is not None and maximum is not None:
                    return min(max(1, minimum), maximum)
                if minimum is not None:
                    return max(1, minimum)
                assert maximum is not None  # narrowed: not None by the branch guard
                return min(1, maximum)
            return 1

        elif type_ == "boolean":
            return False

        return None
