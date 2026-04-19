"""
Profile manager - Core business logic for user profile operations.

Handles creating, updating, and reading user profiles with
custom status, bio, banner images, social links, and more.
"""

import time
import json
from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id


class ProfileManager:
    """Core profile manager handling all operations."""

    MAX_BIO_LENGTH = 1000
    MAX_STATUS_LENGTH = 128
    MAX_STATUS_EMOJI_LENGTH = 32
    MAX_SOCIAL_LINKS = 10
    ALLOWED_SOCIAL_PLATFORMS = {
        "github",
        "twitter",
        "linkedin",
        "website",
        "youtube",
        "twitch",
        "discord",
        "reddit",
        "mastodon",
        "other",
    }
    MAX_PRONOUNS_LENGTH = 40
    MAX_LOCATION_LENGTH = 100
    MAX_TIMEZONE_LENGTH = 64

    def __init__(self, db, auth_module=None):
        self._db = db
        self._auth = auth_module

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

    def _validate_social_links(
        self, links: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """Validate and sanitize social links."""
        if len(links) > self.MAX_SOCIAL_LINKS:
            raise ValueError(f"Maximum {self.MAX_SOCIAL_LINKS} social links allowed")

        validated = []
        for link in links:
            platform = link.get("platform", "other").lower()
            url = link.get("url", "").strip()
            if platform not in self.ALLOWED_SOCIAL_PLATFORMS:
                platform = "other"
            if not url:
                continue
            # Basic URL validation - must have a scheme
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            # Prevent XSS in URLs
            if "javascript:" in url.lower() or "data:" in url.lower():
                continue
            validated.append({"platform": platform, "url": url})
        return validated

    def get_profile(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get a user's profile, creating a default one if needed."""
        row = self._db.fetch_one(
            "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
        )
        if not row:
            # Create default profile
            return self._create_default_profile(user_id)

        data = dict(row)
        # Parse social links from JSON
        if data.get("social_links") and isinstance(data["social_links"], str):
            try:
                data["social_links"] = json.loads(data["social_links"])
            except (json.JSONDecodeError, TypeError):
                data["social_links"] = []
        elif not data.get("social_links"):
            data["social_links"] = []

        # Check if custom status has expired
        if data.get("custom_status_expires_at"):
            if data["custom_status_expires_at"] < self._get_timestamp():
                data["custom_status_text"] = None
                data["custom_status_emoji"] = None
                data["custom_status_expires_at"] = None
                # Clear expired status in DB too
                self._db.execute(
                    """UPDATE user_profiles SET custom_status_text = NULL,
                       custom_status_emoji = NULL, custom_status_expires_at = NULL
                       WHERE user_id = ?""",
                    (user_id,),
                )
                # Also clear from auth_users if present
                self._db.execute(
                    """UPDATE auth_users SET custom_status_text = NULL,
                       custom_status_emoji = NULL, custom_status_expires_at = NULL
                       WHERE id = ?""",
                    (user_id,),
                )

        return data

    def _create_default_profile(self, user_id: int) -> Dict[str, Any]:
        """Create a default profile for a user."""
        now = self._get_timestamp()
        profile_id = self._generate_id()

        self._db.execute(
            """INSERT INTO user_profiles
               (id, user_id, bio, social_links, created_at, updated_at)
               VALUES (?, ?, NULL, '[]', ?, ?)""",
            (profile_id, user_id, now, now),
        )
        return {
            "id": profile_id,
            "user_id": user_id,
            "bio": None,
            "banner_url": None,
            "social_links": [],
            "custom_status_text": None,
            "custom_status_emoji": None,
            "custom_status_expires_at": None,
            "pronouns": None,
            "location": None,
            "timezone": None,
            "created_at": now,
            "updated_at": now,
        }

    def update_profile(
        self,
        user_id: int,
        bio: Optional[str] = None,
        banner_url: Optional[str] = None,
        social_links: Optional[List[Dict[str, str]]] = None,
        pronouns: Optional[str] = None,
        location: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update a user's profile."""
        profile = self.get_profile(user_id)
        if not profile:
            profile = self._create_default_profile(user_id)

        now = self._get_timestamp()

        if bio is not None:
            bio = bio.strip()[: self.MAX_BIO_LENGTH]
            self._db.execute(
                "UPDATE user_profiles SET bio = ?, updated_at = ? WHERE user_id = ?",
                (bio, now, user_id),
            )

        if banner_url is not None:
            # Validate URL scheme
            if banner_url and not banner_url.startswith(("http://", "https://")):
                raise ValueError("Banner URL must use http or https")
            if banner_url and (
                "javascript:" in banner_url.lower() or "data:" in banner_url.lower()
            ):
                raise ValueError("Invalid banner URL scheme")
            self._db.execute(
                "UPDATE user_profiles SET banner_url = ?, updated_at = ? WHERE user_id = ?",
                (banner_url if banner_url else None, now, user_id),
            )

        if social_links is not None:
            validated = self._validate_social_links(social_links)
            self._db.execute(
                "UPDATE user_profiles SET social_links = ?, updated_at = ? WHERE user_id = ?",
                (json.dumps(validated), now, user_id),
            )

        if pronouns is not None:
            pronouns = pronouns.strip()[: self.MAX_PRONOUNS_LENGTH]
            self._db.execute(
                "UPDATE user_profiles SET pronouns = ?, updated_at = ? WHERE user_id = ?",
                (pronouns if pronouns else None, now, user_id),
            )

        if location is not None:
            location = location.strip()[: self.MAX_LOCATION_LENGTH]
            self._db.execute(
                "UPDATE user_profiles SET location = ?, updated_at = ? WHERE user_id = ?",
                (location if location else None, now, user_id),
            )

        if timezone is not None:
            timezone = timezone.strip()[: self.MAX_TIMEZONE_LENGTH]
            self._db.execute(
                "UPDATE user_profiles SET timezone = ?, updated_at = ? WHERE user_id = ?",
                (timezone if timezone else None, now, user_id),
            )

        logger.debug(f"User {user_id} updated their profile")
        result = self.get_profile(user_id)
        return result if result else {}

    def set_custom_status(
        self,
        user_id: int,
        text: Optional[str] = None,
        emoji: Optional[str] = None,
        expires_at: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Set or clear custom status for a user."""
        profile = self.get_profile(user_id)
        if not profile:
            profile = self._create_default_profile(user_id)

        now = self._get_timestamp()

        if text is not None:
            text = text.strip()[: self.MAX_STATUS_LENGTH]
        if emoji is not None:
            emoji = emoji.strip()[: self.MAX_STATUS_EMOJI_LENGTH]

        # Validate expiry time
        if expires_at is not None and expires_at < now:
            raise ValueError("Expiry time must be in the future")

        self._db.execute(
            """UPDATE user_profiles SET custom_status_text = ?, custom_status_emoji = ?,
               custom_status_expires_at = ?, updated_at = ? WHERE user_id = ?""",
            (text, emoji, expires_at, now, user_id),
        )

        # Also update auth_users for quick access in presence
        self._db.execute(
            """UPDATE auth_users SET custom_status_text = ?, custom_status_emoji = ?,
               custom_status_expires_at = ? WHERE id = ?""",
            (text, emoji, expires_at, user_id),
        )

        logger.debug(f"User {user_id} set custom status")
        result = self.get_profile(user_id)
        return result if result else {}

    def clear_custom_status(self, user_id: int) -> Dict[str, Any]:
        """Clear custom status for a user."""
        return self.set_custom_status(user_id, text=None, emoji=None, expires_at=None)

    def get_bulk_profiles(self, user_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Get profiles for multiple users efficiently."""
        if not user_ids:
            return {}

        results = {}
        # Batch fetch from user_profiles
        placeholders = ",".join(["?"] * len(user_ids))
        rows = self._db.fetch_all(
            f"SELECT * FROM user_profiles WHERE user_id IN ({placeholders})",
            tuple(user_ids),
        )
        for row in rows:
            data = dict(row)
            uid = data["user_id"]
            if data.get("social_links") and isinstance(data["social_links"], str):
                try:
                    data["social_links"] = json.loads(data["social_links"])
                except (json.JSONDecodeError, TypeError):
                    data["social_links"] = []
            elif not data.get("social_links"):
                data["social_links"] = []
            results[uid] = data

        # Create defaults for missing users
        for uid in user_ids:
            if uid not in results:
                results[uid] = self._create_default_profile(uid)

        return results
