"""SEC3 invariant: HTTP request models reject hostile input at the boundary.

FastAPI turns a Pydantic ``ValidationError`` into a 422 response before any
handler runs, so a request model is the cheapest and most reliable place to
reject oversized bodies, control characters (log/header injection) and HTML
metacharacters (stored XSS). This suite sweeps every request model in the
schema and route trees and verifies:

* every bounded text field actually rejects a value one character over its
  declared ``max_length``;
* fields that declare a text policy reject control characters and HTML
  metacharacters;
* the models that use per-field validators reject control characters; and
* Pydantic rejection is surfaced as a 4xx by FastAPI, not a 500.
"""

import importlib
import inspect
import pkgutil
from typing import Annotated, Union, get_args, get_origin

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, TypeAdapter, ValidationError

import src.api.routes as routes_package
import src.api.schemas as schemas_package

REQUEST_SUFFIXES = ("Request", "Update", "Patch", "Create", "Body", "Confirm")


def _iter_models():
    """Every Pydantic model declared in the schema and route trees."""
    for package in (schemas_package, routes_package):
        for module_info in pkgutil.walk_packages(
            package.__path__, prefix=f"{package.__name__}."
        ):
            module = importlib.import_module(module_info.name)
            for name, obj in vars(module).items():
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, BaseModel)
                    and obj is not BaseModel
                    and obj.__module__ == module.__name__
                    and name.endswith(REQUEST_SUFFIXES)
                ):
                    yield f"{module_info.name}.{name}", obj


MODELS = dict(_iter_models())


def _is_text(annotation) -> bool:
    if annotation is str:
        return True
    origin = get_origin(annotation)
    if origin is Union:
        return any(_is_text(arg) for arg in get_args(annotation))
    if origin in (list, tuple):
        arguments = get_args(annotation)
        return bool(arguments) and _is_text(arguments[0])
    return False


def _max_length(field):
    for metadata in field.metadata:
        if type(metadata).__name__ == "MaxLen":
            return metadata.max_length
    return None


def _adapter(field) -> TypeAdapter:
    """A TypeAdapter for the field, preserving its declared constraints."""
    if field.metadata:
        return TypeAdapter(Annotated[field.annotation, *field.metadata])
    return TypeAdapter(field.annotation)


def _policy_names(value, found):
    """Collect the names of any registered policies attached to a field."""
    if isinstance(value, (list, tuple)):
        for item in value:
            _policy_names(item, found)
        return found
    name = getattr(value, "__policy_name__", None)
    if name:
        found.add(name)
    func = getattr(value, "func", None)
    if func is not None:
        _policy_names(func, found)
    for arg in get_args(value):
        _policy_names(arg, found)
    return found


CONTROL = "x\x00y"
HTML = '<img src=x onerror="alert(1)">'


def test_discovery_finds_the_request_model_tree():
    assert len(MODELS) > 60, f"only found {len(MODELS)} request models"


def test_every_bounded_text_field_rejects_oversized_input():
    checked = 0
    for model_name, model in MODELS.items():
        for field_name, field in model.model_fields.items():
            if not _is_text(field.annotation):
                continue
            limit = _max_length(field)
            if limit is None:
                continue
            with pytest.raises(ValidationError):
                _adapter(field).validate_python("a" * (limit + 1))
            checked += 1
    assert checked > 80, f"expected a broad length-cap sweep, only checked {checked}"


def test_policy_fields_reject_control_characters_and_html():
    checked = 0
    for model_name, model in MODELS.items():
        for field_name, field in model.model_fields.items():
            policies = _policy_names(field.annotation, set()) | _policy_names(field.metadata, set())
            if not policies:
                continue
            adapter = _adapter(field)
            for hostile in (CONTROL, HTML):
                with pytest.raises(ValidationError):
                    adapter.validate_python(hostile)
            checked += 1
    assert checked > 20, f"expected a broad policy sweep, only checked {checked}"


@pytest.mark.parametrize(
    "model_name, payload",
    [
        ("src.api.schemas.channels.ChannelCreateRequest", {"name": "ok"}),
        ("src.api.schemas.servers.ServerCreateRequest", {"name": "ok server"}),
        (
            "src.api.schemas.messages.AttachmentRequest",
            {"filename": "x.txt", "content_type": "text/plain", "url": "https://e.test/x", "size": 1},
        ),
    ],
)
def test_field_validator_models_reject_control_characters(model_name, payload):
    model = MODELS[model_name]
    # The baseline payload is itself valid: only the hostile mutation should
    # be rejected, not the surrounding required fields.
    model.model_validate(payload)
    field_name = next(iter(payload))
    with pytest.raises(ValidationError):
        model.model_validate({**payload, field_name: CONTROL})


def test_a_pydantic_rejection_is_a_4xx_at_the_http_boundary():
    model = MODELS["src.api.schemas.channels.ChannelCreateRequest"]
    app = FastAPI()

    @app.post("/create")
    def create(payload: model):  # noqa: ANN001 - annotation is the model under test  # pyright: ignore[reportInvalidTypeForm]
        return {"ok": True}

    client = TestClient(app)
    assert client.post("/create", json={"name": "a" * 101}).status_code == 422
    assert client.post("/create", json={"name": CONTROL}).status_code == 422
