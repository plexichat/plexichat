"""Voting, vote changes, results, and vote removal."""

from typing import List, Optional

import utils.logger as logger
from ...base import SnowflakeID

from ..exceptions import (
    AlreadyVotedError,
    MultipleVoteNotAllowedError,
    PollEndedError,
    PollNotFoundError,
    PollOptionNotFoundError,
)
from ..models import PollOption, PollResults
from ..limits import validate_option_ids
from .base import PollManagerBase, _is_unique_violation
from .post_gate import require_poll_post_access


def _affected_rows(cursor) -> Optional[int]:
    """Best-effort rowcount for a guarded write, or None when unavailable.

    Drivers differ: SQLite/pg8000 report an int, some wrappers return None or
    -1. Callers must treat a definite 0 as "guard failed" and fall back to a
    re-read when the count is unknown (never assume the write matched).
    """
    try:
        rowcount = cursor.rowcount
    except Exception:
        return None
    if rowcount is None or rowcount == -1:
        return None
    try:
        return int(rowcount)
    except (TypeError, ValueError):
        return None


def _guard_open_poll_in_txn(manager, poll_id: SnowflakeID) -> SnowflakeID:
    """Fail closed unless the poll is still open at write time.

    The ``is_ended`` / deadline checks in ``vote`` / ``change_vote`` /
    ``remove_vote`` run against a poll read *before* the write transaction is
    opened. A creator close or the periodic expiry sweep can land in that
    gap, so those checks alone are a check-then-write TOCTOU that lets a vote
    or vote change be recorded on an already-closed poll (mutating a settled
    result and its counts).

    This guarded no-op UPDATE runs *inside* the active transaction, after
    every pre-write check. It re-reads the authoritative ``ended_at`` /
    ``ends_at`` from ``poll_polls`` and takes a write lock on the poll row for
    the rest of the transaction, so no close can commit between this check
    and the vote/voter writes. A definite zero-row match (poll closed or
    gone) raises :class:`PollEndedError`; drivers that cannot report rowcount
    fall back to a direct authoritative re-read. Both paths abort the
    surrounding transaction by raising.

    Kept module-level (rather than a mixin method) so test stubs that compose
    the mixin methods directly -- without inheriting ``PollManagerBase`` --
    still exercise the exact production guard.

    Returns the authoritative timestamp captured inside the transaction for
    the write's ``voted_at`` values.
    """
    now = manager._get_timestamp()
    cursor = manager._db.execute(
        "UPDATE poll_polls SET ended_at = ended_at "
        "WHERE id = ? AND ended_at IS NULL "
        "AND (ends_at IS NULL OR ends_at > ?)",
        (poll_id, now),
        auto_commit=False,
    )
    affected = _affected_rows(cursor)
    if affected == 0:
        raise PollEndedError("Poll has ended")
    if affected is None:
        row = manager._db.fetch_one(
            "SELECT ended_at, ends_at FROM poll_polls WHERE id = ?", (poll_id,)
        )
        if (
            not row
            or row["ended_at"] is not None
            or (row["ends_at"] and now >= row["ends_at"])
        ):
            raise PollEndedError("Poll has ended")
    return now


class VotingMixin(PollManagerBase):
    """Cast and revise votes and compute visibility-aware results."""

    def vote(
        self, user_id: SnowflakeID, poll_id: SnowflakeID, option_ids: List[SnowflakeID]
    ) -> PollResults:
        """
        Vote on a poll.

        Args:
            user_id: ID of user voting
            poll_id: ID of poll
            option_ids: List of option IDs to vote for

        Returns:
            Updated PollResults

        Raises:
            PollNotFoundError: Poll not found
            PollEndedError: Poll has ended
            PollOptionNotFoundError: Option not found
            AlreadyVotedError: User already voted
            MultipleVoteNotAllowedError: Multiple votes not allowed
        """
        unique_option_ids = validate_option_ids(option_ids)

        poll = self.get_poll(poll_id, user_id)
        if not poll:
            raise PollNotFoundError("Poll not found")

        # View alone is not enough to mutate: a read-only channel viewer
        # must not vote. Fail closed before any write.
        require_poll_post_access(self, poll.message_id, user_id)

        if poll.is_ended or (poll.ends_at and self._get_timestamp() >= poll.ends_at):
            if not poll.is_ended:
                self._end_poll(poll_id)
            raise PollEndedError("Poll has ended")

        # Duplicates, id format and the bulk cap were already validated by
        # ``validate_option_ids`` before any database work.
        if not poll.allow_multiple_choice and len(unique_option_ids) > 1:
            raise MultipleVoteNotAllowedError(
                "This poll does not allow multiple choice voting"
            )

        # Validate all options exist in this poll (optimized single query)
        placeholders = ",".join("?" * len(unique_option_ids))
        valid_options = self._db.fetch_all(
            f"SELECT id FROM poll_options WHERE poll_id = ? AND id IN ({placeholders})",
            (poll_id, *unique_option_ids),
        )

        if len(valid_options) != len(unique_option_ids):
            raise PollOptionNotFoundError(
                "One or more selected options are invalid for this poll"
            )

        # One vote per user per poll.  The pre-check is a fast path; the
        # authoritative guard is the ``UNIQUE(poll_id, user_id)`` constraint
        # on ``poll_voters``, inserted in the SAME transaction so two racing
        # requests cannot both pass the check and both commit.  ``change_vote``
        # is the dedicated endpoint for users who want to revise a selection.
        if self._has_voted(user_id, poll_id):
            raise AlreadyVotedError(
                "User has already voted on this poll; "
                "call change_vote() to revise the selection"
            )

        try:
            self._db.begin_transaction()

            # Authoritative, in-transaction open-state check (closes the
            # pre-write TOCTOU with a concurrent close / expiry sweep) and the
            # timestamp used for every vote row written below.
            now = _guard_open_poll_in_txn(self, poll_id)

            self._db.execute(
                """INSERT INTO poll_voters (poll_id, user_id, voted_at)
                   VALUES (?, ?, ?)""",
                (poll_id, user_id, now),
                auto_commit=False,
            )

            # One batched INSERT for every selected option instead of one
            # execute() per option. All rows are written inside the same
            # transaction, so a constraint failure on any one of them rolls
            # the whole vote back (fail-closed) and the UNIQUE(poll_id,
            # user_id) guard on poll_voters above still arbitrates racing
            # voters across nodes. execute_many also clears the query-result
            # cache for the batch, and the read-cache invalidation below drops
            # the poll's cached results bodies after commit.
            vote_rows = [
                (self._generate_id(), poll_id, option_id, user_id, now)
                for option_id in unique_option_ids
            ]
            self._db.execute_many(
                """INSERT INTO poll_votes (id, poll_id, option_id, user_id, voted_at)
                   VALUES (?, ?, ?, ?, ?)""",
                vote_rows,
                auto_commit=False,
            )
            self._db.commit()
        except PollEndedError:
            self._db.rollback()
            raise
        except Exception as exc:
            self._db.rollback()
            # A UNIQUE violation on ``poll_voters`` (poll_id, user_id) means
            # a concurrent request already claimed this voter slot.
            if _is_unique_violation(exc):
                raise AlreadyVotedError("User has already voted on this poll") from exc
            raise

        # Counts changed: drop every viewer's cached detail/results body so
        # the get_results() below (and every concurrent reader) recomputes.
        self._invalidate_poll_read_cache(poll_id)

        logger.debug(f"User {user_id} voted on poll {poll_id}")

        self._emit_poll_update(poll, user_id, "vote")

        return self.get_results(poll_id, user_id)

    def change_vote(
        self, user_id: SnowflakeID, poll_id: SnowflakeID, option_ids: List[SnowflakeID]
    ) -> PollResults:
        """
        Revise a user's existing vote on a poll (replace previous selection).

        The ``poll_voters`` registration row is preserved (the user has
        still voted); only the per-option ``poll_votes`` rows are replaced
        atomically.

        Raises:
            PollNotFoundError: Poll not found
            PollEndedError: Poll has been closed/expired
            PollOptionNotFoundError: Option not valid for this poll
            MultipleVoteNotAllowedError: Multiple options on single-choice poll
        """
        unique_option_ids = validate_option_ids(option_ids)

        poll = self.get_poll(poll_id, user_id)
        if not poll:
            raise PollNotFoundError("Poll not found")

        # A read-only channel viewer may not change a vote there.
        require_poll_post_access(self, poll.message_id, user_id)

        if poll.is_ended or (poll.ends_at and self._get_timestamp() >= poll.ends_at):
            if not poll.is_ended:
                self._end_poll(poll_id)
            raise PollEndedError("Poll has ended")

        # Validated once up-front by ``validate_option_ids``.
        if not poll.allow_multiple_choice and len(unique_option_ids) > 1:
            raise MultipleVoteNotAllowedError(
                "This poll does not allow multiple choice voting"
            )

        placeholders = ",".join("?" * len(unique_option_ids))
        valid_options = self._db.fetch_all(
            f"SELECT id FROM poll_options WHERE poll_id = ? AND id IN ({placeholders})",
            (poll_id, *unique_option_ids),
        )
        if len(valid_options) != len(unique_option_ids):
            raise PollOptionNotFoundError(
                "One or more selected options are invalid for this poll"
            )

        try:
            self._db.begin_transaction()

            # Re-check the authoritative open state after every pre-write
            # check: a close/expiry landing in that gap must abort this
            # replacement instead of rewriting a settled result.
            now = _guard_open_poll_in_txn(self, poll_id)

            # Replace the user's previous per-option selections.
            self._db.execute(
                "DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ?",
                (poll_id, user_id),
                auto_commit=False,
            )

            # Keep the voter registration row in sync (a user revising a
            # vote is still a voter); insert only when absent so the
            # UNIQUE(poll_id, user_id) constraint is never hit.
            existing_voter = self._db.fetch_one(
                "SELECT 1 FROM poll_voters WHERE poll_id = ? AND user_id = ?",
                (poll_id, user_id),
            )
            if not existing_voter:
                self._db.execute(
                    """INSERT INTO poll_voters (poll_id, user_id, voted_at)
                       VALUES (?, ?, ?)""",
                    (poll_id, user_id, now),
                    auto_commit=False,
                )

            # One batched INSERT for every selected option, matching vote().
            # Still inside the same transaction, so a constraint failure rolls
            # the whole replacement back (fail-closed): the user's previous
            # selections are not lost on a partial write.
            vote_rows = [
                (self._generate_id(), poll_id, option_id, user_id, now)
                for option_id in unique_option_ids
            ]
            self._db.execute_many(
                """INSERT INTO poll_votes (id, poll_id, option_id, user_id, voted_at)
                   VALUES (?, ?, ?, ?, ?)""",
                vote_rows,
                auto_commit=False,
            )
            self._db.commit()
        except PollEndedError:
            self._db.rollback()
            raise
        except Exception as exc:
            self._db.rollback()
            if _is_unique_violation(exc):
                raise AlreadyVotedError(
                    "User already voted; call change_vote() to revise the selection"
                ) from exc
            raise

        # Per-option tallies changed even though the voter count did not.
        self._invalidate_poll_read_cache(poll_id)

        logger.debug(f"User {user_id} changed vote on poll {poll_id}")

        self._emit_poll_update(poll, user_id, "change")

        return self.get_results(poll_id, user_id)

    def get_results(self, poll_id: SnowflakeID, user_id: SnowflakeID) -> PollResults:
        """
        Get poll results.

        Args:
            poll_id: ID of poll
            user_id: ID of user requesting results

        Returns:
            PollResults

        Raises:
            PollNotFoundError: Poll not found
        """
        cache = getattr(self, "_poll_read_cache", None)
        key = None
        if cache is not None:
            # Live authz gate FIRST: the channel-access check is cheap and
            # must not be skipped on a cache hit. Denials are never cached.
            if not self.can_access_poll(poll_id, user_id):
                raise PollNotFoundError("Poll not found")
            key = self._poll_read_key("results", poll_id, user_id)
            cached = cache.get(key)
            if cached is not None:
                return cached

        poll = self.get_poll(poll_id, user_id)
        if not poll:
            raise PollNotFoundError("Poll not found")

        # One conditional-aggregation pass over poll_votes yields both the
        # caller's own option ids and the per-option totals. This used to be
        # three queries (user votes, GROUP BY counts, and a separate voter
        # count); the last one is redundant because get_poll() above already
        # reconciled ``poll.total_votes`` under the same visibility rule.
        rows = self._db.fetch_all(
            """SELECT option_id,
                      COUNT(*) AS count,
                      SUM(CASE WHEN user_id = ? THEN 1 ELSE 0 END) AS mine
               FROM poll_votes WHERE poll_id = ? GROUP BY option_id""",
            (user_id, poll_id),
        )
        user_vote_ids = [row["option_id"] for row in rows if row["mine"]]
        user_voted = bool(user_vote_ids)

        # Check visibility
        can_see_results = self._can_view_results(poll, user_voted)

        # Counts are only exposed to entitled viewers; the query above
        # computed them regardless, but they stay private here.
        vote_counts = (
            {row["option_id"]: row["count"] for row in rows} if can_see_results else {}
        )

        options_with_counts = []
        for option in poll.options:
            # If results are hidden, we return None for vote_count instead of 0
            vote_count = vote_counts.get(option.id, 0) if can_see_results else None
            options_with_counts.append(
                PollOption(
                    id=option.id,
                    poll_id=option.poll_id,
                    text=option.text,
                    position=option.position,
                    vote_count=vote_count,
                )
            )

        # Total votes = number of unique participants.  Reuse the value
        # get_poll() already computed under the same visibility rule rather
        # than issuing a duplicate COUNT query.
        total_voters = poll.total_votes if can_see_results else None

        results = PollResults(
            poll=poll,
            options=options_with_counts,
            total_votes=total_voters,
            user_voted=user_voted,
            user_votes=user_vote_ids,
        )
        if cache is not None:
            cache.set(key, results)
        return results

    def has_voted(self, user_id: SnowflakeID, poll_id: SnowflakeID) -> bool:
        """Return True if `user_id` has cast at least one vote on `poll_id`.

        This is a cheap existence check — it returns False for polls that
        don't exist, which keeps callers from having to first verify the
        poll themselves.
        """
        if not poll_id or not user_id:
            return False
        return self._has_voted(user_id, poll_id)

    def remove_vote(self, user_id: SnowflakeID, poll_id: SnowflakeID) -> bool:
        """Remove every vote `user_id` previously cast on `poll_id`.

        Returns True when at least one row was deleted.  Raises
        :class:`PollNotFoundError` if the poll itself does not exist so
        callers can distinguish "user hadn't voted" from "no such poll",
        and :class:`PollEndedError` once the poll has ended (consistent
        with :meth:`vote` — recorded outcomes of closed polls must not
        be altered after the fact).
        """
        poll = self.get_poll(poll_id, user_id)
        if not poll:
            raise PollNotFoundError("Poll not found")

        # A read-only channel viewer may not remove a vote there.
        require_poll_post_access(self, poll.message_id, user_id)

        if poll.is_ended or (poll.ends_at and self._get_timestamp() >= poll.ends_at):
            if not poll.is_ended:
                self._end_poll(poll_id)
            raise PollEndedError("Poll has ended")

        # Both deletes run in ONE transaction: a crash between them must
        # never leave the user de-voted but still registered as a voter
        # (which would block re-voting) or vice versa.
        # Pre-delete existence is the fallback when a driver reports an
        # unknown rowcount (None/-1) or lacks ``rowcount`` entirely: the
        # DELETEs below already succeeded at that point, so prior
        # existence implies rows were removed.
        had_voter_row = self._has_voted(user_id, poll_id)
        had_vote_rows = bool(
            self._db.fetch_one(
                "SELECT 1 FROM poll_votes WHERE poll_id = ? AND user_id = ? LIMIT 1",
                (poll_id, user_id),
            )
        )

        def _deleted_count(cursor) -> Optional[int]:
            try:
                rowcount = cursor.rowcount
            except Exception:
                return None
            if rowcount is None or rowcount == -1:
                return None
            try:
                return int(rowcount)
            except (TypeError, ValueError):
                return None

        votes_deleted: Optional[int] = None
        voters_deleted: Optional[int] = None
        try:
            self._db.begin_transaction()
            # A poll that closed after the pre-check above must not have its
            # recorded outcome altered by this removal either.
            _guard_open_poll_in_txn(self, poll_id)
            cursor = self._db.execute(
                "DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ?",
                (poll_id, user_id),
                auto_commit=False,
            )
            votes_deleted = _deleted_count(cursor)
            try:
                cursor.close()
            except Exception as close_error:
                logger.debug(f"poll vote cursor close failed: {close_error}")

            # De-register the voter slot so the user can vote again on this poll.
            voters_cursor = self._db.execute(
                "DELETE FROM poll_voters WHERE poll_id = ? AND user_id = ?",
                (poll_id, user_id),
                auto_commit=False,
            )
            voters_deleted = _deleted_count(voters_cursor)
            try:
                voters_cursor.close()
            except Exception as close_error:
                logger.debug(f"poll voters cursor close failed: {close_error}")
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        # Both the headcount and the per-option tallies changed.
        self._invalidate_poll_read_cache(poll_id)

        logger.debug(
            "User %s vote(s) on poll %s cleared (%s/%s row(s))",
            user_id,
            poll_id,
            votes_deleted if votes_deleted is not None else "?",
            voters_deleted if voters_deleted is not None else "?",
        )

        self._emit_poll_update(poll, user_id, "remove")

        if votes_deleted is not None and voters_deleted is not None:
            return (votes_deleted > 0) or (voters_deleted > 0)
        return bool(had_voter_row or had_vote_rows)
