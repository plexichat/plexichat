"""Regression for the hardened request schemas owned by this hardening pass.

Covers three weakness classes fixed in ``src/api/schemas``:

* unbounded invite lifetime/use fields on ``ChannelInviteCreateRequest``;
* missing cross-field validators on polls (duplicate options and ballots);
* C1 controls and Unicode bidi/invisible overrides slipping past the legacy
  C0/DEL-only text check on text echoed back to clients (polls, reports,
  channels).
"""

import pytest
from pydantic import ValidationError

from src.api.schemas.channels import (
    MAX_INVITE_AGE_SECONDS,
    MAX_INVITE_USES,
    ChannelCreateRequest,
    ChannelInviteCreateRequest,
)
from src.api.schemas.polls import PollInlineCreateRequest, PollVoteRequest
from src.api.schemas.reports import MessageReportRequest, UserReportRequest

# Drift guard: the schema ceilings must mirror the route's runtime guard, or a
# client could still smuggle an oversized value through whichever layer is
# looser.
from src.api.routes.channels.helpers import (  # noqa: E402
    MAX_INVITE_AGE_SECONDS as ROUTE_MAX_INVITE_AGE_SECONDS,
    MAX_INVITE_USES as ROUTE_MAX_INVITE_USES,
)


# ---------------------------------------------------------------------------
# 1. Unbounded invite limits
# ---------------------------------------------------------------------------


def test_schema_invite_caps_match_route_guard():
    assert MAX_INVITE_AGE_SECONDS == ROUTE_MAX_INVITE_AGE_SECONDS
    assert MAX_INVITE_USES == ROUTE_MAX_INVITE_USES


def test_invite_limits_bound_above_and_below():
    ChannelInviteCreateRequest(max_age=0, max_uses=0)
    ChannelInviteCreateRequest(max_age=MAX_INVITE_AGE_SECONDS, max_uses=MAX_INVITE_USES)

    with pytest.raises(ValidationError, match="max_age"):
        ChannelInviteCreateRequest(max_age=MAX_INVITE_AGE_SECONDS + 1)
    with pytest.raises(ValidationError, match="max_uses"):
        ChannelInviteCreateRequest(max_uses=MAX_INVITE_USES + 1)
    # The oversized integer that used to reach expires_at arithmetic.
    with pytest.raises(ValidationError, match="max_age"):
        ChannelInviteCreateRequest(max_age=2**70)
    with pytest.raises(ValidationError):
        ChannelInviteCreateRequest(max_age=-1)
    with pytest.raises(ValidationError):
        ChannelInviteCreateRequest(max_uses=-1)


# ---------------------------------------------------------------------------
# 2. Cross-field validators on polls
# ---------------------------------------------------------------------------


def _poll(**overrides):
    payload = {"question": "Best day?", "options": ["Mon", "Tue"]}
    payload.update(overrides)
    return payload


def test_poll_question_and_options_still_accept_normal_input():
    PollInlineCreateRequest(**_poll())
    PollInlineCreateRequest(**_poll(options=["Yes", "No", "Abstain"]))


@pytest.mark.parametrize(
    "options",
    [
        ["Maybe", "maybe"],  # case-only duplicate
        [" Maybe ", "Maybe"],  # padding-only duplicate
        ["Yes", "Yes"],  # exact duplicate
    ],
)
def test_poll_rejects_duplicate_options(options):
    with pytest.raises(ValidationError, match="duplicates an earlier option"):
        PollInlineCreateRequest(**_poll(options=options))


def test_vote_rejects_duplicate_option_ids():
    PollVoteRequest(option_ids=["123", "456"])
    with pytest.raises(ValidationError, match="duplicates"):
        PollVoteRequest(option_ids=["123", "123"])
    # SnowflakeID normalizes "00123" to "123" before the cross-field check.
    with pytest.raises(ValidationError, match="duplicates"):
        PollVoteRequest(option_ids=["123", "00123"])


# ---------------------------------------------------------------------------
# 3. Control characters / bidi overrides in echoed text
# ---------------------------------------------------------------------------

UNICODE_ATTACKS = (
    "ok\u202eexe",  # RIGHT-TO-LEFT OVERRIDE
    "ok\u200fname",  # RIGHT-TO-LEFT MARK
    "ok\u2066x",  # LEFT-TO-RIGHT ISOLATE
    "ok\x81name",  # C1 control (missed by the legacy C0/DEL check)
)


@pytest.mark.parametrize("value", UNICODE_ATTACKS)
def test_poll_question_rejects_unsafe_unicode(value):
    with pytest.raises(ValidationError):
        PollInlineCreateRequest(**_poll(question=value))


@pytest.mark.parametrize("value", UNICODE_ATTACKS)
def test_poll_options_reject_unsafe_unicode(value):
    with pytest.raises(ValidationError):
        PollInlineCreateRequest(**_poll(options=["ok", value]))


@pytest.mark.parametrize(
    "model,kwargs",
    [
        (
            MessageReportRequest,
            {"message_id": "1", "channel_id": "2", "reason": "spam"},
        ),
        (UserReportRequest, {"user_id": "1", "reason": "spam"}),
    ],
)
@pytest.mark.parametrize("field", ["reason", "details"])
@pytest.mark.parametrize("value", UNICODE_ATTACKS)
def test_report_text_rejects_unsafe_unicode(model, kwargs, field, value):
    model(**kwargs)  # baseline proves the surrounding payload is valid
    with pytest.raises(ValidationError):
        model(**{**kwargs, field: value})


@pytest.mark.parametrize("field", ["name", "topic"])
@pytest.mark.parametrize("value", UNICODE_ATTACKS)
def test_channel_text_rejects_unsafe_unicode(field, value):
    with pytest.raises(ValidationError):
        ChannelCreateRequest(**{"name": "general", field: value})


def test_ordinary_unicode_still_accepted():
    PollInlineCreateRequest(question="Café 👨‍👩‍👧?", options=["Oui", "Non"])
    ChannelCreateRequest(name="café", topic="Everything 👍")
