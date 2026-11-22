"""Regression: a command rename that loses a UNIQUE race fails closed.

``register_command`` translates a concurrent UNIQUE constraint violation on
``app_commands.application_id/name/server_id`` into ``CommandValidationError``
(the domain error the create path's callers already handle). The sibling
``update_command`` path only runs an advisory ``SELECT ... AND id != ?``
duplicate pre-check before a bare ``UPDATE app_commands SET name = ?``. Under
concurrency two renames (or a rename racing a create) can both pass that
pre-check, and the loser's UPDATE raises a raw ``IntegrityError`` (HTTP 500)
instead of the same domain error the create path surfaces.

The UNIQUE violation must be rolled back and translated to the create path's
``CommandValidationError``; every other DB error must propagate unchanged.
"""

from unittest.mock import MagicMock

import pytest

from src.core.applications.commands.registry import CommandRegistry
from src.core.applications.exceptions import CommandValidationError
from src.core.applications.models import CommandType


def _command_row():
    """A valid stored CHAT_INPUT command row for ``_row_to_command``."""
    return {
        "id": 500,
        "application_id": 10,
        "name": "old_name",
        "description": "old description",
        "command_type": int(CommandType.CHAT_INPUT),
        "server_id": None,
        "options": "[]",
        "default_member_permissions": "0",
        "dm_permission": 1,
        "nsfw": 0,
        "version": 1,
        "created_at": 1,
        "updated_at": 1,
    }


def _registry_with_execute(exc):
    """Registry whose fetch_one serves the command + a missed duplicate check.

    ``db.execute`` raises ``exc`` on the UPDATE. ``fetch_one`` returns the
    command for ``get_command`` and ``None`` for the duplicate pre-check, so the
    UNIQUE constraint at write time is the only thing that can catch the race.
    """
    db = MagicMock()

    def fake_fetch_one(sql, params=None, **kwargs):
        sql = str(sql)
        if "FROM app_commands WHERE id = ?" in sql:
            return _command_row()
        # The advisory duplicate pre-check misses (the concurrent rename won).
        return None

    db.fetch_one.side_effect = fake_fetch_one
    db.execute.side_effect = exc
    return CommandRegistry(db, {}), db


def test_update_command_unique_violation_becomes_domain_error():
    """A UNIQUE race on rename must surface as CommandValidationError, not 500."""
    exc = Exception(
        "UNIQUE constraint failed: app_commands.application_id_name_server_id_unique"
    )
    registry, db = _registry_with_execute(exc)

    with pytest.raises(CommandValidationError, match="already exists"):
        registry.update_command(500, name="taken_name")

    # The failed statement must be rolled back, not left inside an open txn.
    db.rollback.assert_called_once()


def test_update_command_other_db_error_still_propagates():
    """A non-UNIQUE failure must not be mislabelled as a name collision."""
    exc = Exception("disk I/O error")
    registry, db = _registry_with_execute(exc)

    with pytest.raises(Exception, match="disk I/O error") as caught:
        registry.update_command(500, name="some_name")

    assert not isinstance(caught.value, CommandValidationError)
