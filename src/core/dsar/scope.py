"""Authoritative DSAR export scope metadata.

The data collector owns the concrete per-table collection logic; this module
provides the stable, operator-facing description of what a DSAR export covers so
API responses, manifests, and in-repo docs can reference the same source of
truth. Category keys here MUST match ``DataCollector._collectors``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

DSAR_EXPORT_VERSION = "1.0"

# Keyed by category name as emitted in the export payload.
CATEGORY_SCOPE: Dict[str, Dict[str, Any]] = {
    "identity": {
        "label": "Identity",
        "description": "Core account identity fields (username, account metadata, verification/lock/deletion state). Password hashes, TOTP secrets, and encrypted/raw secret values are not exported in cleartext.",
        "sensitive_fields_redacted": [
            "password_hash",
            "totp_secret_encrypted",
            "backup_codes_hash",
            "email_encrypted",
        ],
    },
    "sessions": {
        "label": "Sessions and devices",
        "description": "Authentication sessions, registered devices, and known IP/security session metadata associated with the account.",
    },
    "profile": {
        "label": "Profile and preferences",
        "description": "User profile, settings, content filters, messaging/user preferences, custom status, and activity records.",
    },
    "messages": {
        "label": "Messages and conversations",
        "description": "Messages authored/accessible per collector rules, conversation participation/ownership records, forwarded/scheduled messages, edit history, and bookmarks.",
    },
    "relationships": {
        "label": "Relationships",
        "description": "Friend relationships/requests and blocked-user records.",
    },
    "servers": {
        "label": "Server memberships",
        "description": "Server membership records and onboarding progress tied to the account.",
    },
    "content": {
        "label": "Content and reactions",
        "description": "Pinned-message records, reactions, attachment metadata/records, and related content artifacts as collected for the user.",
    },
    "notifications": {
        "label": "Notifications",
        "description": "Notification records, unread counters, notification settings, and per-channel overrides.",
    },
    "oauth": {
        "label": "External OAuth accounts",
        "description": "Linked external OAuth account records; provider secrets/tokens are not exposed beyond what the collector explicitly serializes.",
    },
    "applications": {
        "label": "Applications",
        "description": "Owned applications, installations, and application OAuth token records associated with the account.",
    },
    "reports": {
        "label": "Reports",
        "description": "Message and user reports filed by or involving the account as collected by the reports collector.",
    },
    "feedback": {
        "label": "Feedback",
        "description": "Feedback submitted by the account.",
    },
    "search": {
        "label": "Search history",
        "description": "Search history and saved searches.",
    },
    "features": {
        "label": "Feature flags and usage",
        "description": "User feature assignments, feature usage records, and feature audit entries.",
    },
    "polls": {
        "label": "Polls",
        "description": "Poll votes and polls created by the account.",
    },
    "voice": {
        "label": "Voice",
        "description": "Voice state, voice calls where the user is initiator or consented participant, voice-call artifacts, and transcript artifacts (including extracted transcript text for portability).",
    },
    "automod": {
        "label": "AutoMod",
        "description": "AutoMod violations, reputation, and exemption records associated with the account.",
    },
    "presence": {
        "label": "Presence",
        "description": "Presence and typing-indicator records.",
    },
    "stickers": {
        "label": "Sticker usage",
        "description": "Sticker usage records.",
    },
    "soundboard": {
        "label": "Soundboard usage",
        "description": "Soundboard usage records.",
    },
    "media": {
        "label": "Media metadata",
        "description": "Media file metadata, avatar records, and API access-token metadata records as collected for the account. Binary media objects are not embedded in the JSON/ZIP category payloads unless a collector explicitly includes them.",
    },
    "suite": {
        "label": "Suite (Stash/PlexiSum/audit)",
        "description": "Suite-owned records for PlexiStash and PlexiSum where the account is owner, creator, actor, or subject per collector semantics, plus relevant suite audit-log entries.",
    },
}


def categories() -> List[str]:
    return list(CATEGORY_SCOPE.keys())


def build_scope_manifest(
    actual_categories: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a scope manifest for the categories actually present/exported."""
    keys = actual_categories if actual_categories is not None else categories()
    category_entries = []
    for key in keys:
        meta = dict(
            CATEGORY_SCOPE.get(
                key, {"label": key, "description": "Collector category."}
            )
        )
        meta["category"] = key
        category_entries.append(meta)
    return {
        "export_version": DSAR_EXPORT_VERSION,
        "category_count": len(category_entries),
        "categories": category_entries,
        "notes": [
            "Export completeness is reported in export_metadata.complete; any per-category collection failures are listed in export_metadata.warnings.",
            "Some sensitive values may be redacted or omitted by collectors; redaction is category-specific and may be reflected in category metadata where applicable.",
            "This manifest describes collector categories present in the export, not a guarantee that every upstream table exists in every deployment/schema state.",
        ],
    }
