"""Regression: the reaper's configured ``batch_size`` must be clamped.

``AccountReaper.harvest`` passed ``batch_size`` straight into the SQL
``LIMIT ?``. SQLite reads ``LIMIT -1`` as *unlimited*, so a non-positive
configured batch size would purge every frozen account in a single cycle,
and ``LIMIT 0`` would stall purges forever. The value is now clamped to at
least one account per cycle.
"""

import time
import uuid

from src.core.auth.deletion_log import DeletionLog
from src.core.auth.reaper import AccountReaper


def _freeze(auth_manager, label):
    unique = uuid.uuid4().hex[:8]
    user = auth_manager.register(
        username=f"{label}_{unique}",
        email=f"{label}_{unique}@test.local",
        password="TestPass123!",
    )
    assert auth_manager.schedule_account_deletion(user.id, "TestPass123!") is True
    return user


def _reaper(db, auth_manager, tmp_path):
    reaper = AccountReaper(db, auth_manager)
    reaper._log = DeletionLog(file_path=str(tmp_path / "deletion_log.jsonl"))
    return reaper


def _record_purges(reaper):
    purged = []

    def _record(user_id, username):
        purged.append(user_id)

    reaper.purge_user = _record
    return purged


class TestReaperBatchSizeClamp:
    def test_negative_batch_size_clamped_to_one(self, db, auth_manager, tmp_path):
        users = [_freeze(auth_manager, f"reabatch{i}") for i in range(3)]
        now_ms = int(time.time() * 1000)
        for user in users:
            db.execute(
                "UPDATE auth_users SET deletion_at = ? WHERE id = ?",
                (now_ms - 1000, user.id),
            )

        reaper = _reaper(db, auth_manager, tmp_path)
        reaper._reaper_config["batch_size"] = -1
        purged = _record_purges(reaper)
        reaper.harvest()

        # LIMIT -1 would have purged all three.
        assert len(purged) == 1

    def test_zero_batch_size_still_purges_one(self, db, auth_manager, tmp_path):
        user = _freeze(auth_manager, "reazero")
        db.execute(
            "UPDATE auth_users SET deletion_at = ? WHERE id = ?",
            (int(time.time() * 1000) - 1000, user.id),
        )

        reaper = _reaper(db, auth_manager, tmp_path)
        reaper._reaper_config["batch_size"] = 0
        purged = _record_purges(reaper)
        reaper.harvest()

        # LIMIT 0 would leave the account frozen forever.
        assert purged == [user.id]
