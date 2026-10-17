"""``SumManager._is_solo`` must fail closed on an unknown subscription state.

Agent 30 L1: the helper that selects the solo read fast-path caught every
exception and returned ``True`` ("assume nobody else is subscribed"). An
unreadable subscription registry must instead deny the fast-path so the read
goes through the live session.
"""

import src.api.websocket.sum as sum_ws
from src.core.suite.sum.manager import SumManager
from src.core.suite.sum.schema import create_tables as create_sum_tables


def _manager(db, server_manager, tmp_path):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))


def test_is_solo_true_when_nobody_else_is_subscribed(
    db, two_users, server_manager, tmp_path
):
    owner, _ = two_users
    manager = _manager(db, server_manager, tmp_path)
    workbook = manager.create_workbook(owner.id, "Solo")
    assert manager._is_solo(int(workbook["id"])) is True


def test_is_solo_fails_closed_when_registry_unavailable(
    db, two_users, server_manager, tmp_path, monkeypatch
):
    owner, _ = two_users
    manager = _manager(db, server_manager, tmp_path)
    workbook = manager.create_workbook(owner.id, "Solo")

    def boom():
        raise RuntimeError("subscription registry unavailable")

    monkeypatch.setattr(sum_ws, "get_sum_subscription_registry", boom)
    assert manager._is_solo(int(workbook["id"])) is False


def test_is_solo_fails_closed_when_subscriber_lookup_raises(
    db, two_users, server_manager, tmp_path, monkeypatch
):
    owner, _ = two_users
    manager = _manager(db, server_manager, tmp_path)
    workbook = manager.create_workbook(owner.id, "Solo")

    class BrokenRegistry:
        def get_subscribers(self, workbook_id):
            raise RuntimeError("registry backend down")

    monkeypatch.setattr(sum_ws, "get_sum_subscription_registry", BrokenRegistry)
    assert manager._is_solo(int(workbook["id"])) is False
