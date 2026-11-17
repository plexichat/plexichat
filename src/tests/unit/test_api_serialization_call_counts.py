"""Serialization call-count guards for the shared API response paths.

These tests pin the performance decisions behind the shared encoder config and
the "already validated" fast path: a hand-built JSON response pays exactly one
configured ``json.dumps`` call, model-backed responses keep FastAPI's Rust-core
fast path (zero ``json.dumps`` calls), and returning an already-validated model
through ``validated_json_response`` skips FastAPI's second validation/serialize
pass entirely — without changing the emitted bytes.
"""

import json as json_module

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.api.schemas.common import APIJSONResponse, validated_json_response


class _Payload(BaseModel):
    ok: bool
    count: int


def test_hand_built_json_response_uses_shared_encoder_once(monkeypatch):
    """A hand-built JSON response is encoded once via the shared config."""
    from src.api.schemas import common

    calls = []
    real = common.api_json_dumps

    def counting(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(common, "api_json_dumps", counting)

    response = APIJSONResponse(content={"ok": True, "count": 2})

    assert response.body == b'{"ok":true,"count":2}'
    assert len(calls) == 1
    # The encoder options come from the single preconfigured partial, not from
    # kwargs re-stated on every render call.
    assert calls[0][1] == {}


def test_validated_response_skips_fastapi_serialization(monkeypatch):
    """Returning a validated model skips FastAPI's serialize/validate pass."""
    import fastapi.routing as routing

    calls = []
    real = routing.serialize_response

    async def counting(**kwargs):
        calls.append(kwargs)
        return await real(**kwargs)

    monkeypatch.setattr(routing, "serialize_response", counting)

    app = FastAPI()

    @app.get("/fast", response_model=_Payload)
    async def fast():
        return validated_json_response(_Payload(ok=True, count=1))

    @app.get("/normal", response_model=_Payload)
    async def normal():
        return _Payload(ok=True, count=1)

    client = TestClient(app)

    fast_response = client.get("/fast")
    assert fast_response.status_code == 200
    assert fast_response.json() == {"ok": True, "count": 1}
    # The pre-serialized Response bypasses serialize_response altogether.
    assert len(calls) == 0

    normal_response = client.get("/normal")
    assert normal_response.status_code == 200
    # The ordinary path still validates + serializes exactly once, so the
    # optimization is opt-in and never disables validation globally.
    assert len(calls) == 1


def test_model_response_uses_pydantic_core_not_json_dumps(monkeypatch):
    """The default response class keeps FastAPI's dump_json fast path."""

    calls = []
    real_dumps = json_module.dumps

    def counting(*args, **kwargs):
        calls.append(1)
        return real_dumps(*args, **kwargs)

    app = FastAPI()

    @app.get("/m", response_model=_Payload)
    async def m():
        return _Payload(ok=True, count=3)

    client = TestClient(app)
    monkeypatch.setattr(json_module, "dumps", counting)

    response = client.get("/m")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "count": 3}
    # dump_json serializes straight to bytes; no json.dumps call is made.
    assert calls == []


def test_validated_response_bytes_match_fastapi_serialization():
    """Skipping re-validation does not change the response body."""
    model = _Payload(ok=True, count=7)

    direct = validated_json_response(model).body

    app = FastAPI()

    @app.get("/m", response_model=_Payload)
    async def m():
        return model

    framed = TestClient(app).get("/m").content

    assert direct == framed
