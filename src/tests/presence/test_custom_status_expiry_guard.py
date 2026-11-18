"""Regression: custom-status expiry must be a bounded, plain integer.

``set_custom_status`` persists ``expires_at`` verbatim into an INTEGER column
and the read path later coerces it with ``int()``. An unvalidated value could
overflow a 64-bit column (Postgres) or be a junk string that makes the cached
read raise ``ValueError`` — a 500 for anyone viewing that user's presence.
Only plain integers in signed-64-bit range are accepted now.
"""

import pytest

from src.core.presence.exceptions import InvalidStatusError


BAD_EXPIRIES = [
    "not-a-number",   # junk string -> int() ValueError on read
    "1_000",          # int() accepts PEP-515 underscores; not a real timestamp
    "١٢٣",            # Arabic-Indic digits — homoglyph, not ASCII
    1.5,              # fraction must not be truncated
    True,             # bool is not an integer timestamp
    2**63,            # above signed-64-bit -> DB overflow
    -(10**9),         # negative epoch
]


@pytest.mark.presence
class TestCustomStatusExpiryGuard:
    @pytest.mark.parametrize("bad", BAD_EXPIRIES)
    def test_invalid_expiry_rejected(self, fresh_users, bad):
        user1, _user2, presence = fresh_users
        with pytest.raises(InvalidStatusError):
            presence.set_custom_status(
                user_id=user1.id, text="status", expires_at=bad
            )

    def test_valid_future_expiry_accepted(self, fresh_users):
        user1, _user2, presence = fresh_users
        presence.set_custom_status(
            user_id=user1.id, text="away", expires_at=4102444800000
        )
        status = presence.get_custom_status(user1.id)
        assert status is not None
        assert status.expires_at == 4102444800000

    def test_past_expiry_still_accepted_for_cleanup(self, fresh_users):
        """Legacy callers seed an already-expired status to exercise reaping."""
        user1, _user2, presence = fresh_users
        presence.set_custom_status(user_id=user1.id, text="stale", expires_at=1)
        assert presence.get_custom_status(user1.id) is None

    def test_no_expiry_still_accepted(self, fresh_users):
        user1, _user2, presence = fresh_users
        result = presence.set_custom_status(user_id=user1.id, text="always")
        assert result is not None
