"""A manual AutoMod action must be attributed to the permission-checked actor.

``trigger_action`` verifies ``user_id`` (the caller) holds the action permission
and can manage the target, then writes that id as ``moderator_id`` in the
AutoMod audit row.  The action executors, however, read ``bot_user_id`` from the
caller-supplied ``context`` and pass it to the permission-checked servers layer
(``ban_member`` / ``kick_member`` / ``delete_message``).  A caller can therefore
supply a *different* identity there, so the servers audit row and the ban row
name one principal while the AutoMod audit row names another.  That is a
broken audit trail: the actor actually recorded by the authoritative servers
layer is not the actor AutoMod authorised and logged.

Fixing this fail-closed means the executor actor is always the same id that was
permission- and hierarchy-checked, regardless of what the caller puts in
``context``.
"""

import pytest

from src.core.automod.models import ActionType


@pytest.mark.automod
class TestManualActionActorAttribution:
    def _seed_moderator_and_victim(self, modules, user_pool, server, owner):
        moderator = user_pool.get_user()
        victim = user_pool.get_user()
        modules.servers.add_member(server.id, moderator.id)
        modules.servers.add_member(server.id, victim.id)
        role = modules.servers.create_role(
            owner.id,
            server.id,
            name="Banners",
            permissions={"members.ban": True},
        )
        modules.servers.assign_role(owner.id, server.id, moderator.id, role.id)
        assert (
            modules.servers.has_permission(moderator.id, server.id, "members.ban")
            is True
        )
        return moderator, victim

    def test_context_bot_user_id_cannot_spoof_ban_actor(
        self, automod_manager, test_server, user_pool, modules, db
    ):
        server, owner = test_server
        moderator, victim = self._seed_moderator_and_victim(
            modules, user_pool, server, owner
        )

        # The caller tries to attribute the ban to the server owner (who has
        # the permission and can manage the target) while AutoMod authorises
        # and logs it as the moderator.
        assert (
            automod_manager.trigger_action(
                user_id=moderator.id,
                server_id=server.id,
                target_user_id=victim.id,
                action_type=ActionType.BAN_USER,
                reason="attribution regression",
                context={"bot_user_id": owner.id},
            )
            is True
        )

        ban = db.fetch_one(
            "SELECT banned_by FROM srv_bans WHERE server_id = ? AND user_id = ?",
            (server.id, victim.id),
        )
        assert ban is not None
        assert ban["banned_by"] == moderator.id, (
            "ban row must name the permission-checked moderator, not the "
            "caller-supplied bot_user_id"
        )

        server_audit = db.fetch_all(
            "SELECT user_id FROM srv_audit_log WHERE server_id = ? AND action = ?",
            (server.id, "member_ban"),
        )
        assert server_audit, "the permission-checked ban must write a server audit row"
        assert all(row["user_id"] == moderator.id for row in server_audit), (
            "server audit must name the permission-checked moderator"
        )

        automod_entries = automod_manager.get_audit_log(server.id)
        assert len(automod_entries) == 1
        assert automod_entries[0].moderator_id == moderator.id
        assert automod_entries[0].target_user_id == victim.id
        assert automod_entries[0].server_id == server.id

    def test_context_bot_user_id_cannot_spoof_kick_actor(
        self, automod_manager, test_server, user_pool, modules, db
    ):
        server, owner = test_server
        moderator, victim = self._seed_moderator_and_victim(
            modules, user_pool, server, owner
        )
        # Give the moderator the kick bit as well.
        kick_role = modules.servers.create_role(
            owner.id, server.id, name="Kickers", permissions={"members.kick": True}
        )
        modules.servers.assign_role(owner.id, server.id, moderator.id, kick_role.id)

        assert (
            automod_manager.trigger_action(
                user_id=moderator.id,
                server_id=server.id,
                target_user_id=victim.id,
                action_type=ActionType.KICK_USER,
                reason="attribution regression",
                context={"bot_user_id": owner.id},
            )
            is True
        )

        server_audit = db.fetch_all(
            "SELECT user_id FROM srv_audit_log WHERE server_id = ? AND action = ?",
            (server.id, "member_kick"),
        )
        assert server_audit, "the permission-checked kick must write a server audit row"
        assert all(row["user_id"] == moderator.id for row in server_audit), (
            "server audit must name the permission-checked moderator"
        )

        automod_entries = automod_manager.get_audit_log(server.id)
        assert len(automod_entries) == 1
        assert automod_entries[0].moderator_id == moderator.id
