"""Meta-test: request models may not accept free text without a rule.

This is the structural guarantee the hardening sweep was missing. Every commit
in that sweep added a validator to one model, and nothing could tell you which
model had been *missed* -- a new ``FooRequest`` with a ``name: str`` field was
invisible until someone thought to look.

A field counts as covered when one of these holds:

1. it is annotated with a registered policy (``policy_types.UIText`` and
   friends), which also records *which* policy;
2. it carries a ``field_validator``, which is how the sweep's call sites do it;
3. its name is in ``STRUCTURED_TEXT_FIELDS`` -- machine values (ids, tokens,
   URLs, MIME types) that are never rendered as free text;
4. its name is in ``UNRESTRICTED_TEXT_FIELDS`` -- chat bodies and similar,
   where rejecting ``<`` would break ordinary conversation.

Everything else is *debt* and lives in ``policy_coverage_baseline.json``. The
baseline is a ratchet: a new uncovered field fails this test immediately, while
the existing ones can be paid down one at a time. When you cover a baseline
entry, delete it -- the test fails if an entry no longer exists, so the list
cannot rot into fiction.
"""

import importlib
import inspect
import json
import os
import pkgutil
from pathlib import Path
from typing import Any, Union, get_args, get_origin

import pytest
from pydantic import BaseModel

import src.api.schemas as schemas_package

BASELINE_PATH = Path(__file__).with_name("policy_coverage_baseline.json")

REQUEST_SUFFIXES = ("Request", "Update", "Patch", "Create")

#: Machine-readable values. Never rendered as user-facing prose.
STRUCTURED_TEXT_FIELDS = frozenset(
    {
        "action",
        "allow_dms_from",
        "attachment_url",
        "avatar_url",
        "category",
        "challenge_token",
        "checksum",
        "code_verifier",
        "custom_emoji",
        "device_fingerprint",
        "device_name",
        "dob",
        "from_date",
        "hash_value",
        "ip_address",
        "license_feature",
        "pattern",
        "permission",
        "permissions",
        "phash_value",
        "rate_limit_tier",
        "state",
        "ticket",
        "tier",
        "to_date",
        "banner_url",
        "callback_url",
        "channel_type",
        "client_version",
        "code",
        "color",
        "content_type",
        "cursor",
        "email",
        "emoji",
        "file_name",
        "filename",
        "format",
        "hash",
        "icon_url",
        "id",
        "kind",
        "language",
        "locale",
        "method",
        "mime_type",
        "new_password",
        "nonce",
        "old_password",
        "password",
        "path",
        "perceptual_hash",
        "platform",
        "redirect_uri",
        "refresh_token",
        "salt",
        "scope",
        "scope_mode",
        "sha256",
        "signature",
        "slug",
        "status",
        "theme",
        "timezone",
        "token",
        "type",
        "url",
        "visibility",
        "webhook_url",
    }
)

#: Deliberately not restricted: rendered through an escaper, and rejecting these
#: characters would refuse ordinary messages ("use <b> for bold", "<3").
UNRESTRICTED_TEXT_FIELDS = frozenset(
    {
        "bio",
        "body",
        "comment",
        "content",
        "current_password",
        "description",
        "details",
        "direction",
        "location",
        "markdown",
        "message",
        "notes",
        "pronouns",
        "reason",
        "supported_versions",
        "text",
        "title",
    }
)

#: Suffix matching is not enough for nested ids (``user_id``, ``server_id``...).
_STRUCTURED_SUFFIXES = ("_id", "_ids", "_at", "_url", "_urls", "_hash", "_type")


def _iter_models():
    for module_info in pkgutil.walk_packages(
        schemas_package.__path__, prefix=f"{schemas_package.__name__}."
    ):
        module = importlib.import_module(module_info.name)
        for name, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and issubclass(obj, BaseModel)
                and obj is not BaseModel
                and obj.__module__ == module.__name__
            ):
                yield module_info.name, name, obj


def _policy_name(field: Any) -> str | None:
    """The policy a field's annotation declares, if any."""
    for metadata in field.metadata:
        candidate = getattr(getattr(metadata, "func", None), "__policy_name__", None)
        if candidate:
            return candidate
    return None


def _validator_covers(model: type[BaseModel], field_name: str) -> bool:
    decorators = model.__pydantic_decorators__
    for validator in decorators.field_validators.values():
        if field_name in validator.info.fields:
            return True
    return False


def _is_text(annotation: Any) -> bool:
    if annotation is str:
        return True
    origin = get_origin(annotation)
    if origin is Union:
        return any(_is_text(argument) for argument in get_args(annotation))
    if origin in (list, tuple):
        arguments = get_args(annotation)
        return bool(arguments) and _is_text(arguments[0])
    return False


def _covered_by_name(field_name: str) -> bool:
    if field_name in STRUCTURED_TEXT_FIELDS:
        return True
    if field_name in UNRESTRICTED_TEXT_FIELDS:
        return True
    return field_name.endswith(_STRUCTURED_SUFFIXES)


def offenders_now() -> list[str]:
    """Every request-model text field with no policy, validator or exemption."""
    offenders: list[str] = []
    for module_name, model_name, model in _iter_models():
        if not model_name.endswith(REQUEST_SUFFIXES):
            continue
        for field_name, field in model.model_fields.items():
            if not _is_text(field.annotation):
                continue
            if _policy_name(field) or _validator_covers(model, field_name):
                continue
            if _covered_by_name(field_name):
                continue
            offenders.append(f"{module_name}.{model_name}.{field_name}")
    return sorted(offenders)


def _baseline() -> list[str]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def test_no_new_unvalidated_text_fields():
    current = offenders_now()
    known = set(_baseline())
    growth = [entry for entry in current if entry not in known]
    assert not growth, (
        "New request-model text fields with no policy, validator or documented "
        "exemption:\n  "
        + "\n  ".join(growth)
        + "\n\nAnnotate the field with a policy type from "
        "src/api/schemas/policy_types.py, add a field_validator, or add the "
        "name to STRUCTURED_TEXT_FIELDS / UNRESTRICTED_TEXT_FIELDS with a "
        "reason."
    )


def test_the_baseline_has_no_stale_entries():
    # If a field was covered (or removed), its debt entry must go: otherwise the
    # baseline fills with fiction and stops meaning anything.
    current = set(offenders_now())
    stale = sorted(set(_baseline()) - current)
    assert not stale, (
        "These baseline entries are no longer uncovered -- delete them from "
        f"{BASELINE_PATH.name}:\n  " + "\n  ".join(stale)
    )


def test_regenerate_baseline_when_asked():
    # The baseline has to be written from inside the test session: importing the
    # schema tree outside pytest runs module-level ``config.get`` calls before
    # the test conftest has configured a config file.
    if not os.environ.get("PLEXICHAT_EMIT_POLICY_BASELINE"):
        pytest.skip("set PLEXICHAT_EMIT_POLICY_BASELINE=1 to regenerate")
    BASELINE_PATH.write_text(
        json.dumps(offenders_now(), indent=2) + "\n", encoding="utf-8"
    )


def test_discovery_finds_the_schema_tree():
    # A broken walk would make both tests above vacuously pass.
    models = list(_iter_models())
    assert len(models) > 50, f"only found {len(models)} schema models"


def test_the_registry_and_the_annotations_agree():
    from src.api.schemas.policy_types import POLICY_ANNOTATIONS
    from src.core.security.policies import TEXT_POLICIES

    assert set(POLICY_ANNOTATIONS) == set(TEXT_POLICIES)


def test_structured_and_unrestricted_exemptions_are_disjoint():
    assert not (STRUCTURED_TEXT_FIELDS & UNRESTRICTED_TEXT_FIELDS)
