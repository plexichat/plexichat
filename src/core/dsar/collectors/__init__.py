"""
DSAR Data Collectors Subpackage

This package contains domain-specific data collectors for DSAR (Data Subject Access Request)
exports. Each collector handles a specific domain of user data.

Collectors:
    - identity: User identity and authentication data (auth_users)
    - sessions: Sessions, devices, known IPs (auth_sessions, auth_devices, auth_known_ips)
    - profile: Profiles, settings, custom status, activity (user_profiles, user_settings, msg_content_filters, msg_user_settings, pres_custom_status, pres_activity)
    - messages: Messages, participants, conversations, forwarded, scheduled, edit history, bookmarks
    - relationships: Friends, friend requests, blocked users (rel_friends, rel_friend_requests, rel_blocked)
    - servers: Server memberships and onboarding (srv_members, srv_onboarding_progress)
    - content: Pinned messages, reactions, attachments (msg_pinned, react_reactions, msg_attachments)
    - notifications: Notifications, unread counts, settings, channel overrides (notif_notifications, notif_unread, notif_settings, notif_channel_overrides)
    - oauth: External OAuth accounts (auth_external_accounts)
    - applications: Owned applications, installations, OAuth tokens (app_applications, app_installations, app_oauth_tokens)
    - reports: Message reports and user reports (message_reports, user_reports)
    - feedback: User feedback (feedback)
    - search: Search history and saved searches (search_history, saved_searches)
    - features: Feature flags, usage, audit (user_features, user_feature_usage, user_features_audit)
    - polls: Poll votes and created polls (poll_votes, poll_polls)
    - voice: Voice states, calls, call artifacts, transcripts (voice_states, voice_calls, artifacts)
    - automod: Automod violations, reputation, exemptions (automod_violations, automod_reputation, automod_exemptions)
    - presence: Presence and typing indicators (pres_presence, pres_typing)
    - stickers: Sticker usage (sticker_usage)
    - soundboard: Soundboard usage (soundboard_usage)
    - media: Media files metadata, avatars, API tokens (media_files, user_avatars, auth_api_access_tokens)
"""

from .identity import IdentityCollector
from .sessions import SessionsCollector
from .profile import ProfileCollector
from .messages import MessagesCollector
from .relationships import RelationshipsCollector
from .servers import ServersCollector
from .content import ContentCollector
from .notifications import NotificationsCollector
from .oauth import OAuthCollector
from .applications import ApplicationsCollector
from .reports import ReportsCollector
from .feedback import FeedbackCollector
from .search import SearchCollector
from .features import FeaturesCollector
from .polls import PollsCollector
from .voice import VoiceCollector
from .automod import AutomodCollector
from .presence import PresenceCollector
from .stickers import StickersCollector
from .soundboard import SoundboardCollector
from .media import MediaCollector

__all__ = [
    "IdentityCollector",
    "SessionsCollector",
    "ProfileCollector",
    "MessagesCollector",
    "RelationshipsCollector",
    "ServersCollector",
    "ContentCollector",
    "NotificationsCollector",
    "OAuthCollector",
    "ApplicationsCollector",
    "ReportsCollector",
    "FeedbackCollector",
    "SearchCollector",
    "FeaturesCollector",
    "PollsCollector",
    "VoiceCollector",
    "AutomodCollector",
    "PresenceCollector",
    "StickersCollector",
    "SoundboardCollector",
    "MediaCollector",
]
