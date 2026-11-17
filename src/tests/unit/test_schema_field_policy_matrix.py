"""Meta-test: every request-model text, URL and id field declares a policy.

``test_schema_policy_coverage`` ratchets free text. This extends the same idea
to the two other attacker-controlled shapes that used to slip through: a ``url``
field with no structural check, and an ``id`` field typed as a bare ``str``.
Both are checked across every request model in the schemas *and* routes trees,
so a new field without a policy fails immediately instead of waiting for a
reviewer to notice.

Fields that are legitimately not absolute URLs (own-API relative paths) or not
snowflakes (WebAuthn challenges, spreadsheet operation ids, content-addressed
media object ids) are named explicitly. Route-owned debt is tracked in the
baseline and can only shrink; no schema-owned field may appear in it.
"""

import importlib
import inspect
import pkgutil
from typing import Union, get_args, get_origin

from pydantic import BaseModel

import src.api.routes as routes_package
import src.api.schemas as schemas_package
from src.tests.unit.test_schema_policy_coverage import (
    REQUEST_SUFFIXES,
    _policy_name,
    _validator_covers,
    _covered_by_name,
    _is_text,
)

SNOWFLAKE_TYPES = frozenset({"SnowflakeID", "SnowflakeIDParam"})

#: URL-looking names that are own-API relative paths, not absolute URLs.
RELATIVE_URL_FIELDS = frozenset({"avatar_url"})

#: Identifiers that are not snowflakes (challenge tokens, operation ids, hashes).
NON_SNOWFLAKE_ID_FIELDS = frozenset({"challenge_id", "operation_id", "media_object_id"})

#: Debt in route modules (not owned by the schema tree). New entries are
#: rejected by ``test_no_new_policyless_fields``; this list only shrinks.
POLICY_MATRIX_BASELINE = frozenset(
    {
        "src.api.routes.bots.ApproveBotRequest.application_id",
        "src.api.routes.bots.RequestBotRequest.application_id",
        "src.api.routes.features.bookmarks.BookmarkRequest.conversation_id",
        "src.api.routes.features.bookmarks.BookmarkRequest.message_id",
        "src.api.routes.features.forwarding.ForwardMessageRequest.message_id",
        "src.api.routes.features.forwarding.ForwardMessageRequest.target_conversation_id",
        "src.api.routes.features.last_chat.LastChatRequest.conversation_id",
        "src.api.routes.features.last_chat.LastChatRequest.last_message_id",
        "src.api.routes.features.onboarding.OnboardingPresetRequest.server_id",
        "src.api.routes.features.reports.EnhancedReportRequest.channel_id",
        "src.api.routes.features.reports.EnhancedReportRequest.server_id",
        "src.api.routes.features.scheduled_messages.ScheduledMessageRequest.conversation_id",
        "src.api.routes.features.voice.VoiceMessageRequest.conversation_id",
        "src.api.routes.features.profiles.ProfileUpdateRequest.banner_url",
        "src.api.routes.features.voice.VoiceMessageRequest.url",
    }
)


def _iter_request_models():
    seen = set()
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
                    and (module_info.name, name) not in seen
                ):
                    seen.add((module_info.name, name))
                    yield module_info.name, name, obj


def _is_snowflake(annotation) -> bool:
    if getattr(annotation, "__name__", None) in SNOWFLAKE_TYPES:
        return True
    origin = get_origin(annotation)
    if origin is Union:
        return any(
            _is_snowflake(arg) for arg in get_args(annotation) if arg is not type(None)
        )
    if origin in (list, tuple):
        args = get_args(annotation)
        return bool(args) and _is_snowflake(args[0])
    return False


def offenders() -> list[str]:
    """Every request-model text/url/id field with no declared policy."""
    found: list[str] = []
    for module_name, model_name, model in _iter_request_models():
        for field_name, field in model.model_fields.items():
            label = f"{module_name}.{model_name}.{field_name}"
            policy = _policy_name(field) or ""
            validator = _validator_covers(model, field_name)
            lower = field_name.lower()

            # URL and id names are checked *before* text: a bare ``str`` url or
            # id field would otherwise be treated as plain text and exempted by
            # the ``_url``/``_id`` name rule, hiding the missing policy.
            if "url" in lower or "uri" in lower:
                if field_name in RELATIVE_URL_FIELDS:
                    continue
                if not (policy.startswith("http_url") or validator):
                    found.append(label)
                continue

            is_id = field_name == "id" or field_name.endswith(("_id", "_ids"))
            if is_id:
                if field_name in NON_SNOWFLAKE_ID_FIELDS:
                    continue
                if not (_is_snowflake(field.annotation) or validator):
                    found.append(label)
                continue

            if _is_text(field.annotation):
                if not (
                    policy.startswith("ui_text")
                    or validator
                    or _covered_by_name(field_name)
                ):
                    found.append(label)
    return sorted(found)


def test_no_new_policyless_fields():
    new = [entry for entry in offenders() if entry not in POLICY_MATRIX_BASELINE]
    assert not new, (
        "Request-model text/url/id fields with no policy:\n  "
        + "\n  ".join(new)
        + "\n\nAnnotate the field with a UIText/HttpUrl policy type or a "
        "snowflake id type, or add a documented exemption."
    )


def test_baseline_has_no_stale_entries():
    current = set(offenders())
    stale = sorted(POLICY_MATRIX_BASELINE - current)
    assert not stale, (
        "Baseline entries were covered (baseline shrinks -- delete them):\n  "
        + "\n  ".join(stale)
    )


def test_no_schema_owned_field_is_in_the_baseline():
    # The baseline is route-owned debt only; the schema tree is fully covered.
    assert not [entry for entry in POLICY_MATRIX_BASELINE if ".schemas." in entry]


def test_the_shared_text_baseline_is_empty():
    from pathlib import Path

    baseline = Path(__file__).with_name("policy_coverage_baseline.json")
    import json

    assert json.loads(baseline.read_text(encoding="utf-8")) == []


def test_detector_is_not_vacuous(monkeypatch):
    """Feed the detector a synthetic unpoliced model and confirm it fires."""
    from pydantic import Field

    import src.tests.unit.test_schema_field_policy_matrix as matrix

    class Unpoliced(BaseModel):
        free_text: str = Field(...)
        webhook_url: str = Field(...)
        target_id: str = Field(...)

    monkeypatch.setattr(
        matrix,
        "_iter_request_models",
        lambda: iter([("synthetic", "UnpolicedRequest", Unpoliced)]),
    )
    found = set(matrix.offenders())
    assert "synthetic.UnpolicedRequest.free_text" in found
    assert "synthetic.UnpolicedRequest.webhook_url" in found
    assert "synthetic.UnpolicedRequest.target_id" in found
