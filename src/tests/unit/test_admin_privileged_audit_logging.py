"""Destructive admin actions must always reach the database audit table.

``log_admin_action`` overrides ``admin_ui.audit.log_to_database`` for a fixed
set of privileged action identifiers: an operator may switch to file-only
logging for routine noise, but must not be able to erase the audit trail for
role/privilege changes, account deletion or approval decisions. The old set
held permission-scope names that no route ever passes, so the override never
fired. These tests pin the action identifiers the admin routes actually emit.
"""

from unittest.mock import patch

import pytest

from src.core.admin import permissions as perm_core


pytestmark = pytest.mark.unit


class _Logger:
    def __init__(self):
        self.calls = []

    def log_action(self, db, entry, log_to_file=True, log_to_db=True):
        self.calls.append((entry.action, log_to_file, log_to_db))
        return True


def _use_file_only_logging(monkeypatch):
    cfg = {"admin_ui": {"audit": {"log_to_file": False, "log_to_database": False}}}
    monkeypatch.setattr(
        "utils.config.get", lambda key, default=None: cfg.get(key, default)
    )


@pytest.mark.parametrize(
    "action",
    [
        "role.delete",
        "role.update",
        "role.assign",
        "role.revoke",
        "admin_user.delete",
        "admin_user.toggle_status",
        "force_password_change",
        "approval.approve",
        "approval.reject",
        "approval.cancel",
        # Legacy/direct callers that still use scope-style identifiers.
        "users.force_purge",
        "users.delete",
        "servers.delete",
        "config.modify",
        "config.security",
        "config.edit",
    ],
)
def test_privileged_actions_are_always_db_logged(monkeypatch, action):
    _use_file_only_logging(monkeypatch)
    logger = _Logger()
    with patch("src.core.admin.logging.get_admin_logger", return_value=logger):
        assert perm_core.log_admin_action(object(), 1, action) is True
    assert logger.calls == [(action, False, True)]


def test_benign_action_still_respects_disabled_db_logging(monkeypatch):
    _use_file_only_logging(monkeypatch)
    logger = _Logger()
    with patch("src.core.admin.logging.get_admin_logger", return_value=logger):
        perm_core.log_admin_action(object(), 1, "role.list")
    assert logger.calls == [("role.list", False, False)]


def test_default_config_db_logs_every_action(monkeypatch):
    monkeypatch.setattr("utils.config.get", lambda key, default=None: {})
    logger = _Logger()
    with patch("src.core.admin.logging.get_admin_logger", return_value=logger):
        perm_core.log_admin_action(object(), 1, "anything.read")
    assert logger.calls == [("anything.read", True, True)]
