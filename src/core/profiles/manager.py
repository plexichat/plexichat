"""
Profile manager - Core business logic for user profile operations.

Handles creating, updating, and reading user profiles with
custom status, bio, banner images, social links, and more.
"""

import time
import json
from enum import Enum
from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id


class _Unset(Enum):
    """Sentinel for "field not provided" in :meth:`set_custom_status`.

    An enum member (not a bare ``object()``) so the type checker can narrow
    on ``is _UNSET``: the member has a distinct ``Literal`` type, so the
    ``else`` branches below correctly exclude the sentinel.
    """

    UNSET = 0


_UNSET = _Unset.UNSET


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
            # Skip malformed entries instead of raising on a missing .get().
            if not isinstance(link, dict):
                logger.warning("Skipping non-dict social link entry")
                continue
            platform = str(link.get("platform", "other")).lower()
            url = str(link.get("url", "")).strip()
            if platform not in self.ALLOWED_SOCIAL_PLATFORMS:
                platform = "other"
            if not url:
                continue
            # Basic URL validation - must have a scheme
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            # Reject dangerous schemes by *prefix* (not by substring), so a
            # legitimate URL whose path happens to contain "javascript" is kept
            # and only true javascript:/data: URIs are dropped.
            lowered = url.lower()
            if lowered.startswith(("javascript:", "data:")):
                continue
            validated.append({"platform": platform, "url": url})

        if len(validated) > self.MAX_SOCIAL_LINKS:
            raise ValueError(f"Maximum {self.MAX_SOCIAL_LINKS} social links allowed")
        return validated

    def get_profile(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get a user's profile.

        Read-only: this never mutates the database. If no row exists a default
        in-memory dict is returned (no INSERT), so a fetch can be served by a
        read replica and does not burn a write transaction. The persisted row
        is created lazily on the first write via :meth:`_ensure_profile_row`.
        """
        row = self._db.fetch_one(
            "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
        )
        if not row:
            return self._default_profile_dict(user_id)

        data = dict(row)
        # Parse social links from JSON
        if data.get("social_links") and isinstance(data["social_links"], str):
            try:
                data["social_links"] = json.loads(data["social_links"])
            except (json.JSONDecodeError, TypeError):
                data["social_links"] = []
        elif not data.get("social_links"):
            data["social_links"] = []

        # A read must not execute UPDATEs. We flag expiry in the returned
        # dict only; the canonical cleanup is owned by ``set_custom_status``
        # (which reaps on write) and ``reap_expired_custom_statuses``.
        if data.get("custom_status_expires_at"):
            if data["custom_status_expires_at"] < self._get_timestamp():
                data["custom_status_text"] = None
                data["custom_status_emoji"] = None
                data["custom_status_expires_at"] = None
                data["_custom_status_expired"] = True

        return data

    def _default_profile_dict(self, user_id: int) -> Dict[str, Any]:
        """Build an in-memory default profile (no DB write)."""
        now = self._get_timestamp()
        return {
            "id": None,
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

    def _ensure_profile_row(self, user_id: int) -> None:
        """Create the profile row for ``user_id`` if it does not exist yet.

        Uses ``INSERT OR IGNORE`` keyed on the UNIQUE(user_id) constraint so a
        concurrent first-write cannot raise ``IntegrityError`` and the call is
        idempotent no matter how many times it runs.
        """
        now = self._get_timestamp()
        profile_id = self._generate_id()
        self._db.execute(
            """INSERT OR IGNORE INTO user_profiles
               (id, user_id, bio, social_links, created_at, updated_at)
               VALUES (?, ?, NULL, '[]', ?, ?)""",
            (profile_id, user_id, now, now),
        )

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
        """Update a user's profile.

        N+2 COLLAPSE: previous implementation issued one UPDATE per
        changed field (6 round trips when every field was supplied).
        We now build a single UPDATE statement with only the
        changed field list, parameterised, so 1 round trip per
        update regardless of how many fields were supplied.
        """
        self._ensure_profile_row(user_id)

        now = self._get_timestamp()
        sets: List[str] = ["updated_at = ?"]
        params: List[Any] = [now]

        if bio is not None:
            bio = bio.strip()[: self.MAX_BIO_LENGTH]
            # Normalise empty bio to NULL so it is indistinguishable from
            # "never set", matching the other optional text fields.
            sets.append("bio = ?")
            params.append(bio if bio else None)

        if banner_url is not None:
            # Validate URL scheme up-front so we never persist an
            # invalid value, even with the dynamic SQL pattern.
            banner_url = banner_url.strip()
            if banner_url and not banner_url.startswith(("http://", "https://")):
                raise ValueError("Banner URL must use http or https")
            if banner_url and (
                "javascript:" in banner_url.lower() or "data:" in banner_url.lower()
            ):
                raise ValueError("Invalid banner URL scheme")
            sets.append("banner_url = ?")
            params.append(banner_url if banner_url else None)

        if social_links is not None:
            validated = self._validate_social_links(social_links)
            sets.append("social_links = ?")
            params.append(json.dumps(validated))

        if pronouns is not None:
            pronouns = pronouns.strip()[: self.MAX_PRONOUNS_LENGTH]
            sets.append("pronouns = ?")
            params.append(pronouns if pronouns else None)

        if location is not None:
            location = location.strip()[: self.MAX_LOCATION_LENGTH]
            sets.append("location = ?")
            params.append(location if location else None)

        if timezone is not None:
            timezone = timezone.strip()[: self.MAX_TIMEZONE_LENGTH]
            sets.append("timezone = ?")
            params.append(timezone if timezone else None)

        # len(params) > 1 means caller supplied at least one field
        # beyond updated_at; otherwise this is an idempotent no-op.
        if len(params) > 1:
            params.append(user_id)
            self._db.execute(
                f"UPDATE user_profiles SET {', '.join(sets)} WHERE user_id = ?",
                tuple(params),
            )

        logger.debug(f"User {user_id} updated their profile")
        result = self.get_profile(user_id)
        return result if result else {}

    def set_custom_status(
        self,
        user_id: int,
        text: "str | None | _Unset" = _UNSET,
        emoji: "str | None | _Unset" = _UNSET,
        expires_at: "int | None | _Unset" = _UNSET,
    ) -> Dict[str, Any]:
        """Set (or partially update) a user's custom status.

        A field left at its default sentinel is left unchanged, so a caller can
        update just the text, just the emoji, or just the expiry without wiping
        the others. Pass an explicit ``None`` to clear a field.
        """
        self._ensure_profile_row(user_id)
        now = self._get_timestamp()
        current = self.get_profile(user_id)
        if current is None:
            current = self._default_profile_dict(user_id)

        # Reap any already-expired status for this user while we are here.
        if current.get("_custom_status_expired"):
            self.reap_expired_custom_statuses()

        if text is _UNSET:
            new_text = current.get("custom_status_text")
        elif text is None:
            new_text = None
        else:
            stripped = text.strip()[: self.MAX_STATUS_LENGTH]
            new_text = stripped if stripped else None

        if emoji is _UNSET:
            new_emoji = current.get("custom_status_emoji")
        elif emoji is None:
            new_emoji = None
        else:
            stripped = emoji.strip()[: self.MAX_STATUS_EMOJI_LENGTH]
            new_emoji = stripped if stripped else None

        if expires_at is _UNSET:
            new_expires = current.get("custom_status_expires_at")
        else:
            new_expires = self._normalise_expiry(expires_at, now)

        self._db.execute(
            """UPDATE user_profiles SET custom_status_text = ?, custom_status_emoji = ?,
               custom_status_expires_at = ?, updated_at = ? WHERE user_id = ?""",
            (new_text, new_emoji, new_expires, now, user_id),
        )

        # Also update auth_users for quick access in presence
        self._db.execute(
            """UPDATE auth_users SET custom_status_text = ?, custom_status_emoji = ?,
               custom_status_expires_at = ? WHERE id = ?""",
            (new_text, new_emoji, new_expires, user_id),
        )

        logger.debug(f"User {user_id} set custom status")
        result = self.get_profile(user_id)
        return result if result is not None else self._default_profile_dict(user_id)

    @staticmethod
    def _normalise_expiry(expires_at: Optional[int], now: int) -> Optional[int]:
        """Validate and normalise an expiry timestamp.

        Accepts either epoch milliseconds or epoch seconds (auto-detected and
        converted) so callers are not forced into a single unit. Raises
        ``ValueError`` if the value is not in the future. ``None`` clears the
        expiry and is returned unchanged.
        """
        if expires_at is None:
            return None
        if expires_at < 10_000_000_000:  # below ~year 2286 in seconds -> seconds
            expires_at = expires_at * 1000
        if expires_at < now:
            raise ValueError("Expiry time must be in the future")
        return expires_at

    def reap_expired_custom_statuses(self) -> int:
        """Clear custom statuses whose expiry has already passed.

        Returns the number of rows affected. Intended to be driven by a
        background task; it is also invoked opportunistically by
        :meth:`set_custom_status` when an expired status is read.
        """
        now = self._get_timestamp()
        result = self._db.execute(
            """UPDATE user_profiles
               SET custom_status_text = NULL, custom_status_emoji = NULL,
                   custom_status_expires_at = NULL, updated_at = ?
               WHERE custom_status_expires_at IS NOT NULL
                 AND custom_status_expires_at < ?""",
            (now, now),
        )
        try:
            return int(getattr(result, "rowcount", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def clear_custom_status(self, user_id: int) -> Dict[str, Any]:
        """Clear custom status for a user."""
        return self.set_custom_status(user_id, text=None, emoji=None, expires_at=None)

    def get_bulk_profiles(self, user_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Get profiles for multiple users efficiently.

        Read-only: missing users get an in-memory default dict, exactly like
        :meth:`get_profile`. We never INSERT on a read, so supplying a list of
        arbitrary ids cannot be used to pollute ``user_profiles`` or to amplify
        writes.
        """
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
            if data.get("custom_status_expires_at"):
                if data["custom_status_expires_at"] < self._get_timestamp():
                    data["custom_status_text"] = None
                    data["custom_status_emoji"] = None
                    data["custom_status_expires_at"] = None
                    data["_custom_status_expired"] = True
            results[uid] = data

        # In-memory defaults for missing users (no DB write).
        for uid in user_ids:
            if uid not in results:
                results[uid] = self._default_profile_dict(uid)

        return results
