"""Regression tests: applications listings must clamp ``limit`` from below.

``AdminMixin.get_admin_bot_applications`` / ``get_admin_bot_requests`` only
bounded ``limit`` from above, and ``BotManagementMixin.get_bot_directory``
forwarded it almost straight into SQL ``LIMIT ?``. SQLite reads ``LIMIT -1``
as *unlimited*, so ``GET /admin/bots/applications?limit=-1`` (and the requests
listing) returned every row instead of an empty page. The helpers now clamp
``limit`` to ``[0, MAX_*_PAGE_SIZE]`` and ``offset`` to ``>= 0``.
"""

import pytest


pytestmark = pytest.mark.applications


def _seed_application(app_manager, owner_id, name):
    return app_manager.create_application(owner_id=owner_id, name=name)


def _attach_bot(db, owner_id, application_id, bot_id):
    """Give ``application_id`` a real ``bot_id`` for the directory listing.

    ``app_applications.bot_id`` is a foreign key into ``auth_bots`` (and FK
    enforcement is on in the test database), so seed an ``auth_bots`` row
    rather than pointing at a nonexistent id.
    """
    db.execute(
        """INSERT INTO auth_bots
           (id, owner_id, username, display_name, token_hash, permissions,
            created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (bot_id, owner_id, f"dirbot_{bot_id}", "Directory Bot", "hash", "{}", 1),
    )
    db.execute(
        "UPDATE app_applications SET bot_id = ? WHERE id = ?",
        (bot_id, application_id),
    )


class TestAdminBotListingNegativeLimitClamp:
    def test_applications_negative_limit_is_empty(self, app_manager, test_user):
        _seed_application(app_manager, test_user.id, "Listing App A")
        _seed_application(app_manager, test_user.id, "Listing App B")

        assert app_manager.get_admin_bot_applications(limit=-1) == []
        assert app_manager.get_admin_bot_applications(limit=0) == []
        # A positive limit still returns the seeded rows.
        assert len(app_manager.get_admin_bot_applications(limit=10)) == 2

    def test_applications_limit_is_capped_at_max_page_size(
        self, app_manager, test_user
    ):
        _seed_application(app_manager, test_user.id, "Capped App A")
        _seed_application(app_manager, test_user.id, "Capped App B")

        # Over-cap limits never materialize an unbounded result set.
        assert len(app_manager.get_admin_bot_applications(limit=10**6)) == 2

    def test_requests_negative_limit_is_empty(self, db, app_manager, test_user):
        app = _seed_application(app_manager, test_user.id, "Request App")
        for req_id in (1, 2):
            db.execute(
                """INSERT INTO app_bot_requests
                   (id, server_id, application_id, requester_id, reason, status,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (req_id, 1000 + req_id, app.id, test_user.id, "reason", req_id, req_id),
            )

        assert app_manager.get_admin_bot_requests(limit=-1) == []
        assert app_manager.get_admin_bot_requests(limit=0) == []
        assert len(app_manager.get_admin_bot_requests(limit=10)) == 2

    def test_bot_directory_negative_limit_is_empty(self, db, app_manager, test_user):
        for offset, name in enumerate(("Directory Bot A", "Directory Bot B")):
            app = _seed_application(app_manager, test_user.id, name)
            _attach_bot(db, test_user.id, app.id, 500000 + offset)

        result = app_manager.get_bot_directory(limit=-1)
        assert result["bot_list"] == []
        # The count is computed before the page, so it still reflects the
        # full matching set even when the page itself is empty.
        assert result["total"] == 2

        assert app_manager.get_bot_directory(limit=0)["bot_list"] == []
        assert len(app_manager.get_bot_directory(limit=10)["bot_list"]) == 2
