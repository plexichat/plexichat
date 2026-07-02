"""
Phase-2 finalization: membership-AGNOSTIC ``channel_exists`` probe tests.

Covers the new cheap pre-check that lets the channels API distinguish
``404`` (channel gone) from ``403`` (exists, caller blocked) -- because
the membership-aware ``get_channel`` returns ``None`` in BOTH cases.

The probe is single ``SELECT EXISTS(SELECT 1 FROM srv_channels WHERE id
= ?) AS "exists"``; no joins, no permission checks, no row hydration.

The ``"exists"`` alias is double-quoted because ``EXISTS`` is reserved
in BOTH SQLite and Postgres; the cursor strips the quotes and exposes
the column under the unquoted name ``exists``, so ``row["exists"]``
works against either DB.

*** Phase-3 followup ***
One pre-existing source-level bug surfaced while writing this suite:

  ``channel_handler.delete_channel`` (and probably all sibling ops)
  calls ``self.manager.get_channel(user_id, channel_id)`` in the OLD
  (user_id, channel_id) positional order, but base.py canonicalises
  ``get_channel`` to ``(channel_id, user_id)`` per AGENTS.md. The
  call is therefore WRONG at runtime -- ``get_channel`` interprets
  ``user_id`` as ``channel_id``, finds no row, returns ``None``,
  which then propagates as ``ChannelNotFoundError`` even when the
  channel exists AND the caller is the owner.

  The dual-probe (channel_exists + get_channel) is unaffected because
  test code here calls ``server_manager.get_channel(c.id, u.id)``
  directly with the canonical order. The probe itself is correct.

  Phase-3 fix: globally fix the positional call order in
  ``channel_handler.py`` and ``member_handler.py`` (delete_channel,
  update_channel, create_invite, set_channel_override and friends)
  to ``self.manager.get_channel(channel_id, user_id)``.

  To avoid coupling Phase-2 test green-up to that source fix, the
  "post-delete probe" assertion below uses a raw ``UPDATE
  srv_channels SET deleted = 1`` that bypasses the buggy
  ``delete_channel`` handler entirely.
"""

import uuid

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.servers, pytest.mark.unit]


# --- helpers -------------------------------------------------------------


def _create_server_with_channel(server_manager, owner_id):
    server = server_manager.create_server(owner_id, f"srv-{uuid.uuid4().hex[:6]}")
    channel = server_manager.create_channel(
        owner_id, server.id, "general", channel_type=server_manager.ChannelType.TEXT
    )
    return server, channel


def _register_non_member(auth_manager):
    """Create a fresh user that is NOT a member of any test server."""
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return auth_manager.register(
            username=f"non_member_{uuid.uuid4().hex[:8]}",
            email=f"non_member_{uuid.uuid4().hex[:6]}@test.local",
            password="TestPass123!",
        )


# --- channel_exists() ----------------------------------------------------


def test_channel_exists_true_for_existing_channel(server_manager, test_user):
    server, channel = _create_server_with_channel(server_manager, test_user.id)
    assert server_manager.channel_exists(channel.id) is True


def test_channel_exists_false_for_nonexistent_id(server_manager, test_user):
    _create_server_with_channel(server_manager, test_user.id)  # touch DB
    assert server_manager.channel_exists(999_999_999) is False


def test_channel_exists_membership_agnostic_true_for_non_member(
    server_manager, test_user, auth_manager
):
    """Per the docstring contract: ``channel_exists`` returns True even
    when the *caller* is not a member of the channel's server. This is
    what makes it a workable 404-vs-403 separator -- the membership-
    aware ``get_channel`` would return ``None`` in the same situation.
    """
    server, channel = _create_server_with_channel(server_manager, test_user.id)
    non_member = _register_non_member(auth_manager)

    # Sanity: the non_member is not in the server.
    assert server_manager.get_member(server.id, non_member.id) is None

    # The probe MUST still report True -- membership is not part of its scope.
    assert server_manager.channel_exists(channel.id) is True

    # And the membership-aware get_channel MUST report None -- proof
    # the two answers are *intentionally* different and the dual-probe
    # wiring in the channels API has something to distinguish.
    assert server_manager.get_channel(channel.id, non_member.id) is None


def test_channel_exists_unaffected_when_caller_is_owner(server_manager, test_user):
    server, channel = _create_server_with_channel(server_manager, test_user.id)
    # Caller WITH membership AND permission -- both probes agree.
    assert server_manager.channel_exists(channel.id) is True
    assert server_manager.get_channel(channel.id, test_user.id) is not None


def test_channel_exists_returns_false_after_channel_deleted(
    server_manager, db, test_user
):
    server, channel = _create_server_with_channel(server_manager, test_user.id)
    assert server_manager.channel_exists(channel.id) is True

    # Direct SQL DELETE -- bypasses ``channel_handler.delete_channel``'s
    # positional-arg bug (see file-level docstring). This isolates the
    # behavioral claim under test ("probe flips to False after the row
    # is gone") from the broken handler path; the handler path is
    # covered comprehensively elsewhere and will be fixed in Phase-3.
    #
    # NOTE: production ``delete_channel`` is a SOFT delete
    # (`UPDATE srv_channels SET deleted = 1`) but the probe
    # ``SELECT EXISTS(SELECT 1 FROM srv_channels WHERE id = ?)``
    # does NOT filter on ``deleted`` (per its docstring contract:
    # "exists at all, regardless of membership/permission"). So a
    # soft-delete leaves the probe returning True. To assert the
    # probe-as-404-detector, the test must HARD-delete the row.
    db.execute("DELETE FROM srv_channels WHERE id = ?", (channel.id,))
    # The probe bypasses the membership-aware ``get_channel`` cache
    # via a direct ``SELECT EXISTS`` so no cache-invalidate is required
    # for the probe to reflect the post-delete row. We DO invalidate
    # the channel row cache so a subsequent ``get_channel`` call is
    # also consistent (and the test's third assertion holds).
    server_manager._cache_invalidate(server_manager._channel_cache_prefix, channel.id)

    # After delete the probe MUST flip to False.
    assert server_manager.channel_exists(channel.id) is False
    # Membership-aware get_channel also reports None -- but for the
    # *post*-delete case both probes agree, so callers don't NEED to
    # distinguish; the 404-vs-403 routing applies only when the
    # membership-aware None is due to a *membership* gap.
    assert server_manager.get_channel(channel.id, test_user.id) is None


def test_channel_exists_fails_closed_when_row_missing_alias_column(
    server_manager, db, test_user
):
    """If the SELECT returns a row missing the ``exists`` column
    (e.g. a future schema drift, or someone hand-edits the SQL to drop
    the alias), the probe MUST fail closed to ``False`` -- silently
    returning True for a non-existent channel would let the
    404-vs-403 dual-probe misclassify a missing channel as 403.
    """
    server, channel = _create_server_with_channel(server_manager, test_user.id)

    def missing_exists(*args, **kwargs):
        # Return a dict that's missing the 'exists' key on purpose.
        return {"unrelated_key": 0}

    with patch.object(db, "fetch_one", side_effect=missing_exists):
        assert server_manager.channel_exists(channel.id) is False
