"""Voter rosters and per-user vote history."""

from typing import List, Optional

from ...base import SnowflakeID

from ..exceptions import PermissionDeniedError, PollNotFoundError
from ..models import PollResultsVisibility, PollVote
from .base import PollManagerBase
from .reading import _can_view_poll_message  # shared live channel-access gate


class RosterMixin(PollManagerBase):
    """Expose voter rosters under the creator/moderator permission gate."""

    def get_voters(
        self, poll_id: SnowflakeID, user_id: SnowflakeID
    ) -> List[SnowflakeID]:
        """Return the discrete list of user IDs that voted on `poll_id`.

        Visibility-aware (mirrors :meth:`get_results`):

        * ``AFTER_VOTE`` — never (the roster is private from the start);
        * ``AFTER_END``  — only once the poll is ended or its end-time has
          passed;
        * ``ALWAYS``     — every distinct voter.

        Raises :class:`PollNotFoundError` if the poll itself does not exist.
        """
        # The roster decision needs only the poll's own metadata (creator,
        # visibility, end-time).  Hydrating a full Poll via ``get_poll`` used
        # to fetch the option list *and* run the voter-headcount aggregate,
        # both of which were discarded on every request.  Fetch the single
        # poll row directly instead, but keep the exact same live
        # channel-access gate (``_can_view_poll_message``) that ``get_poll``
        # runs on every call, so a viewer who lost access is still denied.
        row = self._db.fetch_one("SELECT * FROM poll_polls WHERE id = ?", (poll_id,))
        if not row:
            raise PollNotFoundError("Poll not found")

        messaging = self._require_messaging()
        if not _can_view_poll_message(messaging, row["message_id"], user_id):
            raise PollNotFoundError("Poll not found")

        # Per-request permission boundary: the discrete voter roster
        # is privacy-sensitive (it answers "who voted for what" rather
        # than just aggregate counts) so we gate access on a
        # creator-or-moderator check.  The creator always sees the
        # roster (needed for refunds / dedupe / close-out), as does
        # any caller with the ``admin.polls.view_voters`` permission
        # granted through the auth module.
        try:
            is_creator = int(row["created_by"]) == int(user_id)
        except (TypeError, ValueError):
            is_creator = False
        if not is_creator and not self._has_admin_poll_voters_perm(user_id):
            raise PermissionDeniedError(
                "Only the poll creator (or a moderator) can view the voter roster"
            )

        visibility = PollResultsVisibility(row["results_visibility"])
        if visibility == PollResultsVisibility.AFTER_VOTE:
            return []

        if visibility == PollResultsVisibility.AFTER_END:
            is_ended = row["ended_at"] is not None
            if not is_ended and row["ends_at"]:
                is_ended = self._get_timestamp() >= row["ends_at"]
            if not is_ended:
                return []

        rows = self._db.fetch_all(
            "SELECT user_id FROM poll_voters WHERE poll_id = ? ORDER BY user_id",
            (poll_id,),
        )
        return [int(row["user_id"]) for row in rows]

    def get_user_votes(
        self, user_id: SnowflakeID, poll_id: Optional[SnowflakeID] = None
    ) -> List[PollVote]:
        """Return every vote `user_id` has ever cast, optionally scoped to a poll.

        Without a `poll_id` filter the query returns the full history of
        the user's voting activity (used by the integration test
        ``test_get_user_votes``); with one, it returns just the rows
        attached to that poll.
        """
        if poll_id is not None:
            rows = self._db.fetch_all(
                "SELECT * FROM poll_votes WHERE user_id = ? AND poll_id = ? ORDER BY voted_at DESC",
                (user_id, poll_id),
            )
        else:
            rows = self._db.fetch_all(
                "SELECT * FROM poll_votes WHERE user_id = ? ORDER BY voted_at DESC",
                (user_id,),
            )
        return [
            PollVote(
                id=row["id"],
                poll_id=row["poll_id"],
                option_id=row["option_id"],
                user_id=row["user_id"],
                voted_at=row["voted_at"],
            )
            for row in rows
        ]

    def _has_admin_poll_voters_perm(self, user_id: SnowflakeID) -> bool:
        """Check the configured auth module for ``admin.polls.view_voters``.

        Returns False when no auth module is wired up so anonymous
        callers can never see voter rosters by accident.
        """
        auth_mod = getattr(self, "_auth", None)
        if auth_mod is None or not user_id:
            return False
        try:
            perm_check = getattr(auth_mod, "has_permission", None)
            if perm_check is None:
                return False
            return bool(perm_check(int(user_id), "admin.polls.view_voters"))
        except Exception:  # pragma: no cover -- defensive
            return False
