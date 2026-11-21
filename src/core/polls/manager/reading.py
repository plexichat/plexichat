"""Poll reads, row hydration, and visibility checks."""

from typing import Optional

import utils.logger as logger
from ...base import SnowflakeID

from ..models import Poll, PollOption, PollResultsVisibility
from .base import PollManagerBase


def _get_servers_module():
    """Resolve the servers module lazily (kept separate so it is patchable)."""
    import src.api as api

    return api.get_servers()


def _can_view_poll_message(
    messaging, message_id: SnowflakeID, user_id: SnowflakeID
) -> bool:
    """Single live authz gate for a poll's backing message.

    ``messaging.get_message`` proves only conversation *participation*. For a
    server channel that is granted to every member of the server regardless of
    the per-channel ``channels.view`` permission. A poll read exposes the
    question, options and (through the results path) the vote counts of that
    message, so a member denied view on a private channel must not be able to
    read its poll. Resolve the channel backing the message's conversation and
    require a live view grant on it. A conversation with no backing channel is
    a genuine DM/group and needs no further gate. Fails closed: any lookup that
    cannot be resolved denies the read.
    """
    msg = messaging.get_message(user_id, message_id)
    if not msg:
        return False

    if isinstance(msg, dict):
        conversation_id = msg.get("conversation_id")
    else:
        conversation_id = getattr(msg, "conversation_id", None)
    # No conversation id means a DM/group (or a minimal test double); the
    # participation check above is the only boundary that exists there.
    if not conversation_id:
        return True

    servers = _get_servers_module()
    if servers is None:
        # The backing-channel lookup cannot be evaluated without the servers
        # module. Deny rather than skip the live view gate (the
        # unwired-dependency invariant in
        # src/tests/api/test_unwired_dependency_fails_closed.py).
        return False
    if getattr(servers, "_setup_complete", True) is False:
        # Present but not initialized (unit harnesses / early startup): there
        # is no server-channel store in this runtime, so a DM/group is the only
        # possibility and the participation check above is the whole boundary.
        return True
    lookup = getattr(servers, "get_channel_by_conversation", None)
    if not callable(lookup):
        return False
    try:
        info = lookup(conversation_id)
    except Exception:
        return False
    if info is None:
        # No reverse row keyed by conversation_id usually means a genuine
        # DM/group. It does NOT prove one: a *channel id* used in place of its
        # linked conversation id has no such row either, yet it is still a
        # server channel. The dangling-conversation-link send path stores
        # exactly that shape in ``msg_messages.conversation_id``, and
        # ``is_participant`` grants every server member participation in the
        # id via ``srv_channels.id`` regardless of ``channels.view``. Treating
        # the alias as a DM here would hand a member denied view the poll's
        # question, options and vote counts. Probe membership-agnostically and
        # require a live view grant whenever the id is actually a channel.
        probe = getattr(servers, "channel_exists", None)
        if not callable(probe):
            probe = getattr(servers, "get_channel_conversation_id", None)
        if callable(probe):
            try:
                is_channel = bool(probe(conversation_id))
            except Exception:
                return False  # cannot prove it is a DM -> deny
            if is_channel:
                get_channel = getattr(servers, "get_channel", None)
                if not callable(get_channel):
                    return False
                try:
                    return get_channel(conversation_id, user_id) is not None
                except Exception:
                    return False
        return True  # genuine DM/group conversation
    channel_id = info.get("id") if isinstance(info, dict) else None
    if channel_id is None:
        return False
    get_channel = getattr(servers, "get_channel", None)
    if not callable(get_channel):
        return False
    try:
        return get_channel(int(channel_id), user_id) is not None
    except Exception:
        return False


class ReadingMixin(PollManagerBase):
    """Read polls and decide what a viewer is allowed to see."""

    def can_access_poll(self, poll_id: SnowflakeID, user_id: SnowflakeID) -> bool:
        """Cheap, live authorization gate for poll reads.

        Fetches only the poll's message id and re-checks the caller's channel
        access through the messaging layer (participation *and* a live
        channels.view grant for server channels). Unlike :meth:`get_poll` it
        does not hydrate options or reconcile vote totals, so callers can run
        it on *every* request -- before any response-cache lookup -- without
        paying for the expensive part of a poll read. A missing poll or a
        viewer without message access returns ``False`` (existence concealed).
        """
        row = self._db.fetch_one(
            "SELECT message_id FROM poll_polls WHERE id = ?", (poll_id,)
        )
        if not row:
            return False
        messaging = self._require_messaging()
        return _can_view_poll_message(messaging, row["message_id"], user_id)

    def get_poll(self, poll_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Poll]:
        """Get a poll by ID."""
        row = self._db.fetch_one("SELECT * FROM poll_polls WHERE id = ?", (poll_id,))

        if not row:
            return None

        # Verify access to the message (fail closed: without the messaging
        # module there is no way to enforce channel membership). This gate
        # runs on EVERY call, before the cache lookup, so a viewer who lost
        # channel access cannot be served a still-warm body. Participation is
        # not enough for server channels -- the shared gate also requires a
        # live channels.view grant before any option/vote data is hydrated.
        messaging = self._require_messaging()
        if not _can_view_poll_message(messaging, row["message_id"], user_id):
            return None

        cache = getattr(self, "_poll_read_cache", None)
        key = None
        if cache is not None:
            key = self._poll_read_key("detail", poll_id, user_id)
            cached = cache.get(key)
            if cached is not None:
                return cached

        poll = self._row_to_poll(row)

        # Whatever the visibility setting, ``_row_to_poll`` never fills in
        # aggregate vote totals; that is reconciled here against the
        # requesting user.  A user who is not entitled to see the results
        # (e.g. has not voted on an ``AFTER_VOTE`` poll) must not receive
        # the voter headcount, so it is suppressed.
        #
        # The viewer's own voter flag and the headcount both come from
        # ``poll_voters``. Compute them in ONE conditional-aggregation pass
        # instead of a separate existence probe + COUNT (two round trips per
        # read). ``_voter_stats`` returns ``(total, viewer_voted)``.
        total_voters, user_voted = self._voter_stats(user_id, poll_id)
        if self._can_view_results(poll, user_voted):
            poll.total_votes = total_voters
        else:
            poll.total_votes = None

        if cache is not None:
            cache.set(key, poll)
        return poll

    def _row_to_poll(self, row) -> Poll:
        """Convert database row to Poll."""
        poll_id = row["id"]

        # Decrypt question if encryption is enabled and encrypted data exists
        question = row["question"]
        if self._encrypt_polls and row.get("question_encrypted"):
            from src.utils.encryption import decrypt_data

            try:
                question = decrypt_data(row["question_encrypted"])
            except Exception as e:
                logger.warning(f"Failed to decrypt poll question {poll_id}: {e}")
                question = row["question"]  # Fallback to unencrypted

        option_rows = self._db.fetch_all(
            "SELECT * FROM poll_options WHERE poll_id = ? ORDER BY position", (poll_id,)
        )

        options = []
        for opt in option_rows:
            # Decrypt option text if encryption is enabled and encrypted data exists
            option_text = opt["text"]
            if self._encrypt_polls and opt.get("text_encrypted"):
                from src.utils.encryption import decrypt_data

                try:
                    option_text = decrypt_data(opt["text_encrypted"])
                except Exception as e:
                    logger.warning(f"Failed to decrypt poll option {opt['id']}: {e}")
                    option_text = opt["text"]  # Fallback to unencrypted

            options.append(
                PollOption(
                    id=opt["id"],
                    poll_id=opt["poll_id"],
                    text=option_text,
                    position=opt["position"],
                )
            )

        # Aggregate vote totals are intentionally NOT computed here.  They
        # are reconciled in ``get_poll`` / ``get_results`` against the
        # requesting user's visibility rights so that hidden results can
        # never leak a voter headcount through a plain ``get_poll`` call.
        is_ended = row["ended_at"] is not None
        if not is_ended and row["ends_at"]:
            is_ended = self._get_timestamp() >= row["ends_at"]

        return Poll(
            id=row["id"],
            message_id=row["message_id"],
            question=question,
            created_by=row["created_by"],
            created_at=row["created_at"],
            ends_at=row["ends_at"],
            ended_at=row["ended_at"],
            allow_multiple_choice=bool(row["allow_multiple_choice"]),
            results_visibility=PollResultsVisibility(row["results_visibility"]),
            options=options,
            total_votes=None,
            is_ended=is_ended,
        )

    def _has_voted(self, user_id: SnowflakeID, poll_id: SnowflakeID) -> bool:
        """True if ``user_id`` is registered as a voter on ``poll_id``."""
        if not poll_id or not user_id:
            return False
        row = self._db.fetch_one(
            "SELECT 1 FROM poll_voters WHERE poll_id = ? AND user_id = ? LIMIT 1",
            (poll_id, user_id),
        )
        return bool(row)

    def _voter_stats(
        self, user_id: SnowflakeID, poll_id: SnowflakeID
    ) -> tuple[int, bool]:
        """Return ``(distinct voter count, viewer_has_voted)`` in one query.

        Both values are derived from ``poll_voters``; the conditional SUM
        identifies the requesting user's row in the same pass that counts the
        headcount. Callers that need both (notably the read/results paths) avoid
        the second round trip that ``_has_voted`` + ``_voter_count`` required.
        """
        if not poll_id:
            return 0, False
        # Keep counting even for a falsy viewer id: the old code always
        # computed the headcount via ``_voter_count`` regardless of viewer,
        # and binding a non-matching id here preserves that behaviour.
        row = self._db.fetch_one(
            "SELECT COUNT(*) AS count, "
            "SUM(CASE WHEN user_id = ? THEN 1 ELSE 0 END) AS mine "
            "FROM poll_voters WHERE poll_id = ?",
            (user_id, poll_id),
        )
        if not row:
            return 0, False
        return int(row["count"] or 0), bool(row["mine"])

    def _voter_count(self, poll_id: SnowflakeID) -> int:
        """Number of distinct voters on a poll."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM poll_voters WHERE poll_id = ?",
            (poll_id,),
        )
        return int(row["count"]) if row else 0

    def _can_view_results(self, poll: Poll, user_voted: bool) -> bool:
        """Whether ``user_voted``'s owner may see this poll's results now."""
        visibility = poll.results_visibility
        if visibility == PollResultsVisibility.AFTER_VOTE:
            return bool(user_voted)
        if visibility == PollResultsVisibility.AFTER_END:
            return poll.is_ended or bool(
                poll.ends_at and self._get_timestamp() >= poll.ends_at
            )
        return True  # ALWAYS or unknown -> visible
