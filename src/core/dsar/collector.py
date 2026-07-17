import json
import time
from typing import Dict, Any

import utils.logger as logger


class DataCollector:
    """
    Collects ALL user data across ALL tables for DSAR exports.
    GDPR Article 20 Right to Data Portability compliance.
    """

    def __init__(self, db):
        self._db = db

    def collect_all(self, user_id: int) -> Dict[str, Any]:
        """
        Collect all user data organized by category.
        Returns a dictionary with all categories populated.
        """
        export_time = int(time.time())
        export_version = "1.0"

        return {
            "exported_at": export_time,
            "user_id": user_id,
            "export_version": export_version,
            "identity": self._collect_identity(user_id),
            "sessions": self._collect_sessions(user_id),
            "profile": self._collect_profile(user_id),
            "messages": self._collect_messages(user_id),
            "relationships": self._collect_relationships(user_id),
            "servers": self._collect_servers(user_id),
            "content": self._collect_content(user_id),
            "notifications": self._collect_notifications(user_id),
            "oauth": self._collect_oauth(user_id),
            "applications": self._collect_applications(user_id),
            "reports": self._collect_reports(user_id),
            "feedback": self._collect_feedback(user_id),
            "search": self._collect_search(user_id),
            "features": self._collect_features(user_id),
            "polls": self._collect_polls(user_id),
            "voice": self._collect_voice(user_id),
            "automod": self._collect_automod(user_id),
            "presence": self._collect_presence(user_id),
            "stickers": self._collect_stickers(user_id),
            "soundboard": self._collect_soundboard(user_id),
            "media": self._collect_media(user_id),
            "avatars": self._collect_avatars(user_id),
            "api_tokens": self._collect_api_tokens(user_id),
        }

    def count_records(self, user_id: int) -> Dict[str, int]:
        """
        Count records per table/category for preview purposes.
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
            ("user_avatars", "avatars"),
            ("auth_api_access_tokens", "api_tokens"),
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
                logger.debug(f"Could not count {table}: {e}")

        return counts

    def _collect_identity(self, user_id: int) -> Dict[str, Any]:
        """Collect identity data from auth_users."""
        try:
            user = self._db.fetch_one(
                """
                SELECT id, account_type, username, email_index, email_encrypted,
                       created_at, updated_at, email_verified, account_locked,
                       locked_until, failed_login_attempts, last_login_at,
                       totp_enabled, avatar_url, age_verified, date_of_birth,
                       deletion_status, deletion_at, custom_status_text,
                       custom_status_emoji, custom_status_expires_at
                FROM auth_users WHERE id = ?
                """,
                (user_id,),
            )
            if not user:
                return {}

            result = dict(user)
            if "password_hash" in result:
                del result["password_hash"]
            if "email_encrypted" in result and result.get("email_encrypted"):
                result["email_encrypted"] = "(encrypted)"
            if "totp_secret_encrypted" in result:
                del result["totp_secret_encrypted"]
            if "backup_codes_hash" in result:
                del result["backup_codes_hash"]

            return result
        except Exception as e:
            logger.error(f"Failed to collect identity for user {user_id}: {e}")
            return {}

    def _collect_sessions(self, user_id: int) -> Dict[str, Any]:
        """Collect session data."""
        sessions = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, device_id, ip_encrypted, ua_encrypted, created_at,
                       expires_at, last_activity, revoked
                FROM auth_sessions WHERE user_id = ?
                """,
                (user_id,),
            )
            for row in rows:
                r = dict(row)
                sessions.append(r)
        except Exception as e:
            logger.error(f"Failed to collect sessions for user {user_id}: {e}")

        devices = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, fingerprint, name, device_type, first_seen_at, last_seen_at
                FROM auth_devices WHERE user_id = ?
                """,
                (user_id,),
            )
            for row in rows:
                devices.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect devices for user {user_id}: {e}")

        known_ips = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, ip_encrypted, first_seen_at, last_seen_at
                FROM auth_known_ips WHERE user_id = ?
                """,
                (user_id,),
            )
            for row in rows:
                known_ips.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect known IPs for user {user_id}: {e}")

        return {
            "sessions": sessions,
            "devices": devices,
            "known_ips": known_ips,
        }

    def _collect_profile(self, user_id: int) -> Dict[str, Any]:
        """Collect profile data."""
        profiles = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                profiles.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect profiles for user {user_id}: {e}")

        settings = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM user_settings WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                settings.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect settings for user {user_id}: {e}")

        content_filters = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM msg_content_filters WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                content_filters.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect content filters for user {user_id}: {e}")

        user_msg_settings = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM msg_user_settings WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                user_msg_settings.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect msg user settings for user {user_id}: {e}")

        custom_status = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM pres_custom_status WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                custom_status.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect custom status for user {user_id}: {e}")

        activity = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM pres_activity WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                activity.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect activity for user {user_id}: {e}")

        return {
            "profiles": profiles,
            "settings": settings,
            "content_filters": content_filters,
            "msg_settings": user_msg_settings,
            "custom_status": custom_status,
            "activity": activity,
        }

    def _collect_messages(self, user_id: int) -> Dict[str, Any]:
        """Collect message data."""
        messages = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, conversation_id, author_id, content, created_at,
                       edited_at, deleted_at, is_forwarded, is_scheduled
                FROM msg_messages WHERE author_id = ?
                """,
                (user_id,),
            )
            for row in rows:
                r = dict(row)
                if r.get("content", "").startswith("ENC:"):
                    r["content"] = "(encrypted)"
                messages.append(r)
        except Exception as e:
            logger.error(f"Failed to collect messages for user {user_id}: {e}")

        participants = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT mp.id, mp.conversation_id, mp.user_id, mp.joined_at,
                       mp.left_at, mp.nick, mp.is_owner, mp.is_muted
                FROM msg_participants mp
                WHERE mp.user_id = ?
                """,
                (user_id,),
            )
            for row in rows:
                participants.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect participants for user {user_id}: {e}")

        conversations = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM msg_conversations WHERE owner_id = ?", (user_id,)
            )
            for row in rows:
                conversations.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect conversations for user {user_id}: {e}")

        forwarded = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM msg_forwarded WHERE original_author_id = ?", (user_id,)
            )
            for row in rows:
                forwarded.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect forwarded for user {user_id}: {e}")

        scheduled = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM msg_scheduled WHERE author_id = ?", (user_id,)
            )
            for row in rows:
                r = dict(row)
                if r.get("content", "").startswith("ENC:"):
                    r["content"] = "(encrypted)"
                scheduled.append(r)
        except Exception as e:
            logger.error(f"Failed to collect scheduled for user {user_id}: {e}")

        edit_history = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM msg_edit_history WHERE editor_id = ?", (user_id,)
            )
            for row in rows:
                edit_history.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect edit history for user {user_id}: {e}")

        bookmarks = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM user_bookmarks WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                bookmarks.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect bookmarks for user {user_id}: {e}")

        return {
            "messages": messages,
            "participants": participants,
            "conversations": conversations,
            "forwarded": forwarded,
            "scheduled": scheduled,
            "edit_history": edit_history,
            "bookmarks": bookmarks,
        }

    def _collect_relationships(self, user_id: int) -> Dict[str, Any]:
        """Collect relationship data."""
        friends = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT rf.*, au.username as friend_username
                FROM rel_friends rf
                JOIN auth_users au ON rf.friend_id = au.id
                WHERE rf.user_id = ?
                """,
                (user_id,),
            )
            for row in rows:
                friends.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect friends for user {user_id}: {e}")

        incoming_requests = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT rfr.*, au.username as sender_username
                FROM rel_friend_requests rfr
                JOIN auth_users au ON rfr.sender_id = au.id
                WHERE rfr.recipient_id = ?
                """,
                (user_id,),
            )
            for row in rows:
                incoming_requests.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect incoming requests for user {user_id}: {e}")

        outgoing_requests = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT rfr.*, au.username as recipient_username
                FROM rel_friend_requests rfr
                JOIN auth_users au ON rfr.recipient_id = au.id
                WHERE rfr.sender_id = ?
                """,
                (user_id,),
            )
            for row in rows:
                outgoing_requests.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect outgoing requests for user {user_id}: {e}")

        blocked = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT rb.*, au.username as blocked_username
                FROM rel_blocked rb
                JOIN auth_users au ON rb.blocked_id = au.id
                WHERE rb.blocker_id = ?
                """,
                (user_id,),
            )
            for row in rows:
                blocked.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect blocked for user {user_id}: {e}")

        return {
            "friends": friends,
            "incoming_friend_requests": incoming_requests,
            "outgoing_friend_requests": outgoing_requests,
            "blocked_users": blocked,
        }

    def _collect_servers(self, user_id: int) -> Dict[str, Any]:
        """Collect server membership data."""
        memberships = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT sm.id, sm.server_id, sm.nickname, sm.joined_at, sm.roles
                FROM srv_members sm
                WHERE sm.user_id = ?
                """,
                (user_id,),
            )
            for row in rows:
                memberships.append(dict(row))
        except Exception as e:
            logger.error(
                f"Failed to collect server memberships for user {user_id}: {e}"
            )

        onboarding = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM srv_onboarding_progress WHERE user_id = ?",
                (user_id,),
            )
            for row in rows:
                onboarding.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect onboarding for user {user_id}: {e}")

        return {
            "server_memberships": memberships,
            "onboarding_progress": onboarding,
        }

    def _collect_content(self, user_id: int) -> Dict[str, Any]:
        """Collect content data (pins, reactions, attachments metadata)."""
        pinned = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM msg_pinned WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                pinned.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect pinned for user {user_id}: {e}")

        reactions = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM react_reactions WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                reactions.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect reactions for user {user_id}: {e}")

        attachments = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, message_id, file_name, file_type, file_size,
                       width, height, duration, created_at
                FROM msg_attachments WHERE user_id = ?
                """,
                (user_id,),
            )
            for row in rows:
                r = dict(row)
                attachments.append(r)
        except Exception as e:
            logger.error(f"Failed to collect attachments for user {user_id}: {e}")

        return {
            "pinned_messages": pinned,
            "reactions": reactions,
            "attachments": attachments,
        }

    def _collect_notifications(self, user_id: int) -> Dict[str, Any]:
        """Collect notification data."""
        notifications = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM notif_notifications WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                notifications.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect notifications for user {user_id}: {e}")

        unread = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM notif_unread WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                unread.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect unread for user {user_id}: {e}")

        settings = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM notif_settings WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                settings.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect notif settings for user {user_id}: {e}")

        channel_overrides = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM notif_channel_overrides WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                channel_overrides.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect channel overrides for user {user_id}: {e}")

        return {
            "notifications": notifications,
            "unread_counts": unread,
            "settings": settings,
            "channel_overrides": channel_overrides,
        }

    def _collect_oauth(self, user_id: int) -> Dict[str, Any]:
        """Collect OAuth account data."""
        accounts = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, provider, external_id_encrypted, email_index, created_at, last_login_at
                FROM auth_external_accounts WHERE user_id = ?
                """,
                (user_id,),
            )
            for row in rows:
                r = dict(row)
                if r.get("external_id_encrypted"):
                    r["external_id_encrypted"] = "(encrypted)"
                accounts.append(r)
        except Exception as e:
            logger.error(f"Failed to collect OAuth accounts for user {user_id}: {e}")

        return {"external_accounts": accounts}

    def _collect_applications(self, user_id: int) -> Dict[str, Any]:
        """Collect application data."""
        owned = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM app_applications WHERE owner_id = ?", (user_id,)
            )
            for row in rows:
                r = dict(row)
                if "bot_token_encrypted" in r:
                    del r["bot_token_encrypted"]
                if "public_key_encrypted" in r:
                    del r["public_key_encrypted"]
                owned.append(r)
        except Exception as e:
            logger.error(
                f"Failed to collect owned applications for user {user_id}: {e}"
            )

        installations = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM app_installations WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                installations.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect installations for user {user_id}: {e}")

        tokens = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, name, description, created_at, first_used_at,
                       last_used_at, expires_at, revoked, use_count_total
                FROM app_oauth_tokens WHERE user_id = ?
                """,
                (user_id,),
            )
            for row in rows:
                tokens.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect OAuth tokens for user {user_id}: {e}")

        return {
            "owned_applications": owned,
            "installations": installations,
            "oauth_tokens": tokens,
        }

    def _collect_reports(self, user_id: int) -> Dict[str, Any]:
        """Collect report data."""
        as_reporter = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM message_reports WHERE reporter_id = ?", (user_id,)
            )
            for row in rows:
                as_reporter.append(dict(row))
        except Exception as e:
            logger.error(
                f"Failed to collect message reports as reporter for user {user_id}: {e}"
            )

        as_reported_user = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM message_reports WHERE reported_user_id = ?", (user_id,)
            )
            for row in rows:
                as_reported_user.append(dict(row))
        except Exception as e:
            logger.error(
                f"Failed to collect message reports as reported for user {user_id}: {e}"
            )

        user_reports_as_reporter = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM user_reports WHERE reporter_id = ?", (user_id,)
            )
            for row in rows:
                user_reports_as_reporter.append(dict(row))
        except Exception as e:
            logger.error(
                f"Failed to collect user reports as reporter for user {user_id}: {e}"
            )

        user_reports_as_reported = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM user_reports WHERE reported_user_id = ?", (user_id,)
            )
            for row in rows:
                user_reports_as_reported.append(dict(row))
        except Exception as e:
            logger.error(
                f"Failed to collect user reports as reported for user {user_id}: {e}"
            )

        return {
            "message_reports_as_reporter": as_reporter,
            "message_reports_as_reported_user": as_reported_user,
            "user_reports_as_reporter": user_reports_as_reporter,
            "user_reports_as_reported_user": user_reports_as_reported,
        }

    def _collect_feedback(self, user_id: int) -> Dict[str, Any]:
        """Collect feedback data."""
        feedback_items = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM feedback WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                feedback_items.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect feedback for user {user_id}: {e}")

        return {"feedback": feedback_items}

    def _collect_search(self, user_id: int) -> Dict[str, Any]:
        """Collect search history data."""
        history = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM search_history WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                history.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect search history for user {user_id}: {e}")

        saved = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM saved_searches WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                saved.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect saved searches for user {user_id}: {e}")

        return {
            "search_history": history,
            "saved_searches": saved,
        }

    def _collect_features(self, user_id: int) -> Dict[str, Any]:
        """Collect feature flags data."""
        features = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM user_features WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                features.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect features for user {user_id}: {e}")

        usage = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM user_feature_usage WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                usage.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect feature usage for user {user_id}: {e}")

        audit = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM user_features_audit WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                audit.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect features audit for user {user_id}: {e}")

        return {
            "features": features,
            "feature_usage": usage,
            "features_audit": audit,
        }

    def _collect_polls(self, user_id: int) -> Dict[str, Any]:
        """Collect poll data."""
        votes = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM poll_votes WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                votes.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect poll votes for user {user_id}: {e}")

        polls = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM poll_polls WHERE creator_id = ?", (user_id,)
            )
            for row in rows:
                polls.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect polls for user {user_id}: {e}")

        return {
            "poll_votes": votes,
            "created_polls": polls,
        }

    def _collect_voice(self, user_id: int) -> Dict[str, Any]:
        """Collect voice state data plus call/transcript artifacts.

        Includes the user's ``voice_states`` rows, any ``voice_calls`` they
        initiated or consented to, and the linked ``artifacts`` (voice_call and
        transcript) they own. Transcript text is included inline so the export is
        human-readable, in line with DSAR data-portability requirements.
        """
        states = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM voice_states WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                states.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect voice states for user {user_id}: {e}")

        voice_calls = []
        try:
            # Calls initiated by the user OR where the user consented (the
            # consented_participants JSON column is matched via LIKE on the
            # integer user id, which is safe because ids are stored as JSON
            # arrays of integers).
            rows = self._db.fetch_all(
                """
                SELECT * FROM voice_calls
                WHERE initiator_id = ?
                   OR consented_participants LIKE ?
                """,
                (user_id, f"%{user_id}%"),
            )
            for row in rows:
                voice_calls.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect voice calls for user {user_id}: {e}")

        call_artifacts = []
        transcripts = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT * FROM artifacts
                WHERE author_id = ?
                  AND artifact_type IN ('voice_call', 'transcript')
                """,
                (user_id,),
            )
            for row in rows:
                r = dict(row)
                if r.get("artifact_type") == "transcript":
                    payload = r.get("payload")
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except (ValueError, TypeError):
                            payload = {}
                    if isinstance(payload, dict):
                        # Pull the flattened transcript text out so the export
                        # is readable without re-parsing the segment array.
                        r["transcript_text"] = payload.get("text", "")
                    transcripts.append(r)
                else:
                    call_artifacts.append(r)
        except Exception as e:
            logger.error(f"Failed to collect voice artifacts for user {user_id}: {e}")

        return {
            "voice_states": states,
            "voice_calls": voice_calls,
            "voice_call_artifacts": call_artifacts,
            "transcripts": transcripts,
        }

    def _collect_automod(self, user_id: int) -> Dict[str, Any]:
        """Collect automod data."""
        violations = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM automod_violations WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                violations.append(dict(row))
        except Exception as e:
            logger.error(
                f"Failed to collect automod violations for user {user_id}: {e}"
            )

        reputation = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM automod_reputation WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                reputation.append(dict(row))
        except Exception as e:
            logger.error(
                f"Failed to collect automod reputation for user {user_id}: {e}"
            )

        exemptions = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM automod_exemptions WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                exemptions.append(dict(row))
        except Exception as e:
            logger.error(
                f"Failed to collect automod exemptions for user {user_id}: {e}"
            )

        return {
            "violations": violations,
            "reputation": reputation,
            "exemptions": exemptions,
        }

    def _collect_presence(self, user_id: int) -> Dict[str, Any]:
        """Collect presence data."""
        presence = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM pres_presence WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                presence.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect presence for user {user_id}: {e}")

        typing = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM pres_typing WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                typing.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect typing for user {user_id}: {e}")

        return {
            "presence": presence,
            "typing": typing,
        }

    def _collect_stickers(self, user_id: int) -> Dict[str, Any]:
        """Collect sticker usage data."""
        usage = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM sticker_usage WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                usage.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect sticker usage for user {user_id}: {e}")

        return {"sticker_usage": usage}

    def _collect_soundboard(self, user_id: int) -> Dict[str, Any]:
        """Collect soundboard usage data."""
        usage = []
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM soundboard_usage WHERE user_id = ?", (user_id,)
            )
            for row in rows:
                usage.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect soundboard usage for user {user_id}: {e}")

        return {"soundboard_usage": usage}

    def _collect_media(self, user_id: int) -> Dict[str, Any]:
        """Collect media files metadata (not blob data)."""
        files = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, file_name, file_type, file_size, width, height,
                       duration, hash, created_at, accessed_at
                FROM media_files WHERE uploaded_by = ?
                """,
                (user_id,),
            )
            for row in rows:
                files.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect media files for user {user_id}: {e}")

        return {"media_files": files}

    def _collect_avatars(self, user_id: int) -> Dict[str, Any]:
        """Collect avatar metadata (not blob data)."""
        avatars = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, avatar_url, is_default, created_at
                FROM user_avatars WHERE user_id = ?
                """,
                (user_id,),
            )
            for row in rows:
                avatars.append(dict(row))
        except Exception as e:
            logger.error(f"Failed to collect avatars for user {user_id}: {e}")

        return {"avatars": avatars}

    def _collect_api_tokens(self, user_id: int) -> Dict[str, Any]:
        """Collect API access tokens (without secrets)."""
        tokens = []
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, name, description, created_at, first_used_at,
                       last_used_at, expires_at, revoked, use_count_total,
                       scope_mode
                FROM auth_api_access_tokens WHERE created_by = ?
                """,
                (user_id,),
            )
            for row in rows:
                r = dict(row)
                if "token_encrypted" in r:
                    del r["token_encrypted"]
                tokens.append(r)
        except Exception as e:
            logger.error(f"Failed to collect API tokens for user {user_id}: {e}")

        return {"api_tokens": tokens}
