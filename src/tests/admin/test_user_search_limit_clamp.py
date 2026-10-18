"""Regression tests: admin user search must clamp ``limit`` from below.

``search_users`` in ``src.core.admin.users`` forwarded the caller's ``limit``
straight into SQL ``LIMIT ?``. SQLite reads ``LIMIT -1`` as *unlimited*, so
``GET /admin/users/search?limit=-1`` returned every ``auth_users`` row instead
of an empty page. The helper now clamps ``limit`` to ``[0, MAX_PAGE_SIZE]``
and ``offset`` to ``>= 0``.
"""

from unittest.mock import MagicMock

from src.core.admin.users import MAX_PAGE_SIZE, search_users


def _fetch_call_kwargs(db):
    """Return the params of the last ``db.fetch_all`` call."""
    _query, params = db.fetch_all.call_args.args
    return params


class TestAdminUserSearchNegativeLimitClamp:
    def test_negative_limit_is_empty(self, db, registered_user):
        """A negative limit returns an empty page instead of every user."""
        _user, _auth, username = registered_user

        # Sanity check: a positive limit still finds the registered user.
        assert search_users(db, username, limit=10, offset=0)

        assert search_users(db, username, limit=-1, offset=0) == []
        assert search_users(db, username, limit=0, offset=0) == []

    def test_negative_limit_is_empty_by_id(self, db, registered_user):
        """The ID branch clamps ``limit`` from below too."""
        user, _auth, _username = registered_user

        assert search_users(db, str(user.id), limit=10, offset=0)
        assert search_users(db, str(user.id), limit=-1, offset=0) == []

    def test_limit_is_clamped_to_max_page_size(self):
        """The page bound is clamped on both sides."""
        db = MagicMock()
        db.fetch_all.return_value = []

        search_users(db, "someone", limit=10**6, offset=0)

        # The trailing params are the (clamped) limit and offset.
        assert _fetch_call_kwargs(db)[-2:] == (MAX_PAGE_SIZE, 0)

    def test_offset_is_clamped_from_below(self):
        """A negative offset is clamped to zero."""
        db = MagicMock()
        db.fetch_all.return_value = []

        search_users(db, "someone", limit=20, offset=-5)

        assert _fetch_call_kwargs(db)[-2:] == (20, 0)
