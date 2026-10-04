"""Ratchet: every gateway payload builder emits string snowflake ids.

``src/core/events/snowflakes.py`` states the rule — gateway payloads carry
ids as decimal strings so a JS ``Number`` cannot round a 64-bit id — and says
the payload builders apply :func:`stringify_snowflake_ids` themselves, with the
websocket dispatcher as a second backstop.

The dispatcher backstop is covered elsewhere (``test_dispatch.py``); this test
guards the *builders* so a new ``create_*``/``emit_*`` helper that forgets to
stringify a scalar id is caught at the source instead of relying on the wire
backstop. A payload is compliant iff it is a fixed point of
``stringify_snowflake_ids`` (stringifying an already-string id is a no-op,
while a numeric id would change).
"""

import inspect
from typing import Any, Dict, get_args, get_origin

import pytest

from src.core.events import gateway_emit, payloads
from src.core.events.snowflakes import stringify_snowflake_ids

# Greater than 2**53: a JS ``Number`` cannot hold it exactly.
BIG = 1234567890123456789
BIG2 = 1234567890123456790

_STR_VALUE_NAMES = {
    "content",
    "reason",
    "action",
    "name",
    "icon",
    "status",
    "type",
    "format",
    "code",
    "emoji",
    "nick",
    "topic",
    "message",
    "resume_gateway_url",
}
_BOOL_VALUE_NAMES = {
    "pinned",
    "tts",
    "is_ended",
    "self_mute",
    "self_deaf",
    "mute",
    "deaf",
}
# ``payloads`` passes caller-owned containers through untouched, so feed them
# id-free values to avoid a false positive; the emitters stringify the
# containers they own, so feed those numeric ids to prove the conversion.
_PASS_THROUGH_CONTAINER_NAMES = {"user", "guilds", "emoji", "members", "author"}


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is None:
        return annotation
    args = [a for a in get_args(annotation) if a is not type(None)]
    if len(args) == 1:
        return args[0]
    return annotation


def _is_list(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return origin in (list, tuple) or annotation in (list, tuple)


def _is_dict(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return origin is dict or annotation is dict


def _value_for(name: str, annotation: Any, *, stringify_containers: bool) -> Any:
    if name.endswith("_ids"):
        return [BIG, BIG2]
    if name == "id" or name.endswith("_id"):
        # ``session_id`` is a string-shaped id (never a JSON number), so a
        # numeric value here would be a false positive.
        return "sess-1" if name == "session_id" else BIG
    if name in _BOOL_VALUE_NAMES:
        return False
    if name in _STR_VALUE_NAMES:
        return "x"

    unwrapped = _unwrap_optional(annotation)
    if _is_list(unwrapped):
        return [BIG] if stringify_containers else []
    if _is_dict(unwrapped):
        if not stringify_containers:
            return {}
        return {"id": BIG}
    if unwrapped is int:
        return BIG
    if unwrapped is bool:
        return False
    # Everything else (str/Any/...) gets an inert string so the builder runs.
    return "x"


def _call_params(func, *, required_only: bool, stringify_containers: bool):
    kwargs: Dict[str, Any] = {}
    for param in inspect.signature(func).parameters.values():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if required_only and param.default is not inspect.Parameter.empty:
            continue
        value = _value_for(
            param.name, param.annotation, stringify_containers=stringify_containers
        )
        if not stringify_containers and param.name in _PASS_THROUGH_CONTAINER_NAMES:
            # Re-assert the pass-through rule for explicitly listed names.
            value = [] if _is_list(_unwrap_optional(param.annotation)) else {}
        kwargs[param.name] = value
    return kwargs


def _payload_builders():
    for name, func in vars(payloads).items():
        if name.startswith("create_") and callable(func) and not name.startswith("_"):
            yield name, func


def _emitters():
    for name, func in vars(gateway_emit).items():
        if name.startswith("emit_") and callable(func):
            yield name, func


@pytest.mark.parametrize(
    "name,func", list(_payload_builders()), ids=lambda v: getattr(v, "__name__", v)
)
def test_event_payload_builders_emit_string_ids(name, func):
    kwargs = _call_params(func, required_only=True, stringify_containers=False)
    event = func(**kwargs)

    data = event.data
    assert stringify_snowflake_ids(data) == data, (
        f"{name} leaked a numeric snowflake id:\n"
        f"  before={data}\n  after={stringify_snowflake_ids(data)}"
    )


@pytest.mark.parametrize(
    "name,func", list(_emitters()), ids=lambda v: getattr(v, "__name__", v)
)
def test_gateway_emitters_emit_string_ids(name, func, monkeypatch):
    recorded: list = []

    def _record(event_type, data, **kwargs):
        recorded.append(data)

    monkeypatch.setattr(gateway_emit, "_emit", _record)

    kwargs = _call_params(func, required_only=False, stringify_containers=True)
    func(**kwargs)

    assert recorded, f"{name} did not emit (bad synthetic args?)"
    for data in recorded:
        assert stringify_snowflake_ids(data) == data, (
            f"{name} leaked a numeric snowflake id:\n"
            f"  before={data}\n  after={stringify_snowflake_ids(data)}"
        )
