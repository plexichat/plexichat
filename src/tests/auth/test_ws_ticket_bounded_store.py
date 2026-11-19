"""Regression: the single-use gateway-ticket store must stay bounded.

``auth_ws_tickets`` used to grow without limit: every ticket minted for a
gateway handshake left a permanent row and nothing ever deleted expired ones
(the expires_at index was never used). Any authenticated client could inflate
it without bound through ordinary reconnects. This suite pins that expired
tickets are actually pruned, while live tickets are never swept.
"""

from unittest.mock import patch

import src.core.auth.tokens as tokens
from src.core.auth.managers.session.tickets import TicketMixin


def _mint(auth_manager, user_id, session_id):
    ticket, _ = auth_manager.create_ws_ticket_for_session(user_id, session_id)
    parsed = tokens.parse_token(ticket)
    assert parsed is not None
    return ticket, parsed["id"]


def _row_count(db, ticket_id):
    row = db.fetch_one(
        "SELECT COUNT(*) AS c FROM auth_ws_tickets WHERE id = ?", (ticket_id,)
    )
    return int(row["c"]) if row else 0


def test_expired_ticket_pruned_on_next_mint(auth_manager, db, test_user_with_token):
    result = auth_manager.login(
        test_user_with_token["username"], test_user_with_token["password"]
    )
    user = auth_manager.get_user_by_username(test_user_with_token["username"])
    session_id = result.session.id

    old_ticket, old_id = _mint(auth_manager, user.id, session_id)
    assert _row_count(db, old_id) == 1

    # Age the first ticket out. Force the rate-limited sweep to run.
    db.execute("UPDATE auth_ws_tickets SET expires_at = 1 WHERE id = ?", (old_id,))
    with patch.object(TicketMixin, "_last_ws_ticket_cleanup", 0):
        new_ticket, new_id = _mint(auth_manager, user.id, session_id)

    # The expired row is gone, the fresh row survives, and it still works.
    assert _row_count(db, old_id) == 0
    assert _row_count(db, new_id) == 1
    assert auth_manager.verify_token(new_ticket).valid is True


def test_prune_never_drops_live_tickets(auth_manager, db, test_user_with_token):
    result = auth_manager.login(
        test_user_with_token["username"], test_user_with_token["password"]
    )
    user = auth_manager.get_user_by_username(test_user_with_token["username"])

    with patch.object(TicketMixin, "_last_ws_ticket_cleanup", 0):
        first, first_id = _mint(auth_manager, user.id, result.session.id)
    with patch.object(TicketMixin, "_last_ws_ticket_cleanup", 0):
        second, second_id = _mint(auth_manager, user.id, result.session.id)

    assert _row_count(db, first_id) == 1
    assert _row_count(db, second_id) == 1
    assert auth_manager.verify_token(first).valid is True
    assert auth_manager.verify_token(second).valid is True


def test_reaper_sweeps_expired_tickets(auth_manager, db, test_user_with_token):
    from src.core.auth.reaper import AccountReaper

    result = auth_manager.login(
        test_user_with_token["username"], test_user_with_token["password"]
    )
    user = auth_manager.get_user_by_username(test_user_with_token["username"])
    ticket, ticket_id = _mint(auth_manager, user.id, result.session.id)
    db.execute("UPDATE auth_ws_tickets SET expires_at = 1 WHERE id = ?", (ticket_id,))

    reaper = AccountReaper(db, auth_manager)
    reaper._cleanup_ws_tickets()

    assert _row_count(db, ticket_id) == 0
