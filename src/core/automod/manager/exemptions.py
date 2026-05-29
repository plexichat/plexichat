import json
from typing import Optional

from src.core.base import SnowflakeID
from ..models import Rule, Exemption
from ..exceptions import ExemptionError


from .protocol import AutoModProtocol


class ExemptionMixin(AutoModProtocol):
    def _is_exempt(
        self, server_id: SnowflakeID, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> bool:
        server_row = self._db.fetch_one(
            "SELECT owner_id FROM srv_servers WHERE id = ? AND deleted = 0",
            (server_id,),
        )
        if server_row and int(server_row["owner_id"]) == int(user_id):
            return True

        channel_exempt = self._db.fetch_one(
            """SELECT id FROM automod_exemptions 
               WHERE server_id = ? AND target_type = 'channel' AND target_id = ? AND rule_id IS NULL""",
            (server_id, channel_id),
        )
        if channel_exempt:
            return True

        user_roles = self._db.fetch_all(
            """SELECT role_id FROM srv_member_roles 
               JOIN srv_members ON srv_member_roles.member_id = srv_members.id
               WHERE srv_members.server_id = ? AND srv_members.user_id = ?""",
            (server_id, user_id),
        )
        role_ids = [r["role_id"] for r in user_roles]

        if role_ids:
            placeholders = ",".join("?" * len(role_ids))
            role_rows = self._db.fetch_all(
                f"SELECT permissions FROM srv_roles WHERE id IN ({placeholders}) AND deleted = 0",
                tuple(role_ids),
            )
            for rr in role_rows:
                try:
                    perms = json.loads(rr.get("permissions") or "{}")
                except Exception:
                    perms = {}
                if isinstance(perms, dict) and perms.get("administrator") is True:
                    return True

        if role_ids:
            placeholders = ",".join("?" * len(role_ids))
            role_exempt = self._db.fetch_one(
                f"""SELECT id FROM automod_exemptions
                    WHERE server_id = ? AND target_type = 'role' AND target_id IN ({placeholders}) AND rule_id IS NULL""",
                (server_id, *role_ids),
            )
            if role_exempt:
                return True

        return False

    def _is_exempt_from_rule(
        self, rule: Rule, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> bool:
        if channel_id in rule.exempt_channels:
            return True

        user_roles = self._db.fetch_all(
            """SELECT role_id FROM srv_member_roles 
               JOIN srv_members ON srv_member_roles.member_id = srv_members.id
               WHERE srv_members.server_id = ? AND srv_members.user_id = ?""",
            (rule.server_id, user_id),
        )
        user_role_ids = {r["role_id"] for r in user_roles}

        if user_role_ids & set(rule.exempt_roles):
            return True

        if rule.applied_roles:
            if not (user_role_ids & set(rule.applied_roles)):
                return True

        channel_exempt = self._db.fetch_one(
            """SELECT id FROM automod_exemptions 
               WHERE server_id = ? AND target_type = 'channel' AND target_id = ? AND rule_id = ?""",
            (rule.server_id, channel_id, rule.id),
        )
        if channel_exempt:
            return True

        if user_role_ids:
            placeholders = ",".join("?" * len(user_role_ids))
            role_exempt = self._db.fetch_one(
                f"""SELECT id FROM automod_exemptions
                    WHERE server_id = ? AND target_type = 'role' AND target_id IN ({placeholders}) AND rule_id = ?""",
                (rule.server_id, *user_role_ids, rule.id),
            )
            if role_exempt:
                return True

        return False

    def add_exemption(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        target_type: str,
        target_id: SnowflakeID,
        rule_id: Optional[SnowflakeID] = None,
    ) -> Exemption:
        if target_type not in ["role", "channel"]:
            raise ExemptionError("target_type must be 'role' or 'channel'")

        existing = self._db.fetch_one(
            """SELECT id FROM automod_exemptions 
               WHERE server_id = ? AND target_type = ? AND target_id = ? AND rule_id IS ?""",
            (server_id, target_type, target_id, rule_id),
        )

        if existing:
            raise ExemptionError("Exemption already exists")

        now = self._get_timestamp()
        exemption_id = self._generate_id()

        self._db.execute(
            """INSERT INTO automod_exemptions 
               (id, server_id, rule_id, target_type, target_id, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (exemption_id, server_id, rule_id, target_type, target_id, now, user_id),
        )

        return Exemption(
            id=exemption_id,
            server_id=server_id,
            rule_id=rule_id,
            target_type=target_type,
            target_id=target_id,
            created_at=now,
            created_by=user_id,
        )

    def remove_exemption(self, user_id: SnowflakeID, exemption_id: SnowflakeID) -> bool:
        existing = self._db.fetch_one(
            "SELECT id FROM automod_exemptions WHERE id = ?", (exemption_id,)
        )

        if not existing:
            raise ExemptionError("Exemption not found")

        self._db.execute("DELETE FROM automod_exemptions WHERE id = ?", (exemption_id,))
        return True
