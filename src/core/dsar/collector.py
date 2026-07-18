"""
DSAR Data Collector - Thin Orchestrator

Composes domain-specific collectors to assemble complete user data exports
for GDPR Article 20 Right to Data Portability compliance.
"""

import time
from typing import Any, Dict

from .collectors import (
    ApplicationsCollector,
    AutomodCollector,
    ContentCollector,
    FeedbackCollector,
    FeaturesCollector,
    IdentityCollector,
    MessagesCollector,
    MediaCollector,
    NotificationsCollector,
    OAuthCollector,
    PollsCollector,
    PresenceCollector,
    ProfileCollector,
    RelationshipsCollector,
    ReportsCollector,
    SearchCollector,
    ServersCollector,
    SessionsCollector,
    SoundboardCollector,
    StickersCollector,
    VoiceCollector,
)


class DataCollector:
    """
    Orchestrates all domain collectors for DSAR exports.

    Maintains the same public API as the original monolithic collector.
    """

    def __init__(self, db):
        self._db = db
        self._collectors = {
            "identity": IdentityCollector(db),
            "sessions": SessionsCollector(db),
            "profile": ProfileCollector(db),
            "messages": MessagesCollector(db),
            "relationships": RelationshipsCollector(db),
            "servers": ServersCollector(db),
            "content": ContentCollector(db),
            "notifications": NotificationsCollector(db),
            "oauth": OAuthCollector(db),
            "applications": ApplicationsCollector(db),
            "reports": ReportsCollector(db),
            "feedback": FeedbackCollector(db),
            "search": SearchCollector(db),
            "features": FeaturesCollector(db),
            "polls": PollsCollector(db),
            "voice": VoiceCollector(db),
            "automod": AutomodCollector(db),
            "presence": PresenceCollector(db),
            "stickers": StickersCollector(db),
            "soundboard": SoundboardCollector(db),
            "media": MediaCollector(db),
        }

    def collect_all(self, user_id: int) -> Dict[str, Any]:
        """
        Collect all user data organized by category.

        Returns a dictionary with all categories populated.
        """
        export_time = int(time.time())
        export_version = "1.0"

        result = {
            "exported_at": export_time,
            "user_id": user_id,
            "export_version": export_version,
        }

        # Delegate to each collector
        for category, collector in self._collectors.items():
            result[category] = collector.collect(self._db, user_id)

        return result

    def count_records(self, user_id: int) -> Dict[str, int]:
        """
        Count records per table/category for preview purposes.

        Maintains the original counting logic for DSAR preview accuracy.
        """
        counts = {}
        tables = [
            ("auth_users", "identity"),
            ("auth_sessions", "sessions"),
            ("auth_devices", "sessions"),
            ("auth_known_ips", "sessions"),
            ("user_profiles", "profile"),
            ("user_settings", "profile"),
            ("msg_content_filters", "profile"),
            ("msg_user_settings", "profile"),
            ("pres_custom_status", "profile"),
            ("pres_activity", "profile"),
            ("msg_messages", "messages"),
            ("msg_participants", "messages"),
            ("msg_conversations", "messages"),
            ("msg_forwarded", "messages"),
            ("msg_scheduled", "messages"),
            ("msg_edit_history", "messages"),
            ("user_bookmarks", "messages"),
            ("rel_friends", "relationships"),
            ("rel_friend_requests", "relationships"),
            ("rel_blocked", "relationships"),
            ("srv_members", "servers"),
            ("srv_onboarding_progress", "servers"),
            ("msg_pinned", "content"),
            ("react_reactions", "content"),
            ("msg_attachments", "content"),
            ("notif_notifications", "notifications"),
            ("notif_unread", "notifications"),
            ("notif_settings", "notifications"),
            ("notif_channel_overrides", "notifications"),
            ("auth_external_accounts", "oauth"),
            ("app_applications", "applications"),
            ("app_installations", "applications"),
            ("app_oauth_tokens", "applications"),
            ("message_reports", "reports"),
            ("user_reports", "reports"),
            ("feedback", "feedback"),
            ("search_history", "search"),
            ("saved_searches", "search"),
            ("user_features", "features"),
            ("user_feature_usage", "features"),
            ("user_features_audit", "features"),
            ("poll_votes", "polls"),
            ("poll_polls", "polls"),
            ("voice_states", "voice"),
            ("automod_violations", "automod"),
            ("automod_reputation", "automod"),
            ("automod_exemptions", "automod"),
            ("pres_presence", "presence"),
            ("pres_typing", "presence"),
            ("sticker_usage", "stickers"),
            ("soundboard_usage", "soundboard"),
            ("media_files", "media"),
            ("user_avatars", "media"),
            ("auth_api_access_tokens", "media"),
        ]

        for table, category in tables:
            try:
                result = self._db.fetch_one(
                    f"SELECT COUNT(*) as count FROM {table} WHERE user_id = ?",
                    (user_id,),
                )
                if result:
                    key = f"{category}_{table}"
                    counts[key] = (
                        result.get("count", 0)
                        if isinstance(result, dict)
                        else result[0]
                    )
            except Exception as e:
                import utils.logger as logger

                logger.debug(f"Could not count {table}: {e}")

        # Special tables not keyed on user_id
        try:
            result = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM artifacts WHERE author_id = ?",
                (user_id,),
            )
            if result:
                counts["content_artifacts"] = (
                    result.get("count", 0) if isinstance(result, dict) else result[0]
                )
        except Exception as e:
            import utils.logger as logger

            logger.debug(f"Could not count artifacts: {e}")

        try:
            result = self._db.fetch_one(
                """
                SELECT COUNT(*) as count FROM artifacts
                WHERE author_id = ? AND artifact_type = 'transcript'
                """,
                (user_id,),
            )
            if result:
                counts["content_transcripts"] = (
                    result.get("count", 0) if isinstance(result, dict) else result[0]
                )
        except Exception as e:
            import utils.logger as logger

            logger.debug(f"Could not count transcripts: {e}")

        try:
            result = self._db.fetch_one(
                """
                SELECT COUNT(*) as count FROM voice_calls
                WHERE initiator_id = ?
                   OR (consented_participants IS NOT NULL
                       AND EXISTS (SELECT 1 FROM json_each(consented_participants) WHERE value = ?))
                """,
                (user_id, user_id),
            )
            if result:
                counts["voice_voice_calls"] = (
                    result.get("count", 0) if isinstance(result, dict) else result[0]
                )
        except Exception as e:
            import utils.logger as logger

            logger.debug(f"Could not count voice_calls: {e}")

        return counts
