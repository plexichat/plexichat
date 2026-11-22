"""
Profile manager - Core business logic for user profile operations.

Handles creating, updating, and reading user profiles with
custom status, bio, banner images, social links, and more.
"""

import ipaddress
import json
import socket
from enum import Enum
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

import utils.logger as logger
from src.core.base import timestamp_ms, generate_id
from src.core.embeds.validator import sanitize_content
from src.core.embeds.exceptions import EmbedSanitizationError
from src.core.security.errors import OutboundUrlRejected
from src.core.security.hardening import (
    HardeningViolation,
    reject_control_characters,
    require_http_url,
)

# Bulk profile hydration (fan-out of friend/server/member lists) must never
# bind an unbounded IN list: SQLite caps variables per statement and a giant
# list also produces a poor plan. Same budget as the other bulk-id paths.
_BULK_ID_CHUNK = 500


def _iter_id_chunks(ids: List[int], size: int = _BULK_ID_CHUNK):
    for start in range(0, len(ids), size):
        yield ids[start : start + size]


# Profile strings are rendered to other users and stored verbatim. Bound every
# one of them before it reaches the database or a response, and refuse control
# characters so a bio or URL cannot forge a log line or a header. The URL
# ceiling applies to banners and to any avatar-style URL alike.
MAX_PROFILE_URL_LENGTH = 2048
MAX_SOCIAL_LINK_URL_LENGTH = 2048
MAX_SOCIAL_PLATFORM_LENGTH = 32
MAX_AVATAR_URL_LENGTH = MAX_PROFILE_URL_LENGTH


def _bounded_text(value: Any, field: str, maximum: int) -> str:
    """Return ``value`` as a stripped, length- and control-safe string."""
    text = reject_control_characters(str(value or "").strip(), field)
    if len(text) > maximum:
        raise HardeningViolation(f"{field} exceeds maximum length of {maximum}")
    return text


class _Unset(Enum):
    """Sentinel for "field not provided" in :meth:`set_custom_status`.

    An enum member (not a bare ``object()``) so the type checker can narrow
    on ``is _UNSET``: the member has a distinct ``Literal`` type, so the
    ``else`` branches below correctly exclude the sentinel.
    """

    UNSET = 0


_UNSET = _Unset.UNSET

# Internal bookkeeping keys that must never cross the API boundary
# (see ``_strip_internal_keys``).
_INTERNAL_PROFILE_KEYS = ("_custom_status_expired",)


# Profile metadata is resolved on every profile view (``SELECT * FROM
# user_profiles`` plus social-link JSON decoding). The API read path memoizes
# that raw fetch under this namespace with per-credential keys and a bounded L1
# tier -- see ``src.api.routes.features.profiles``. Every mutation below drops
# the affected entries so a cached projection can never outlive a write.
PROFILE_READ_CACHE_PREFIX = "user_profile_read_api"


def invalidate_profile_read_cache(user_id: Optional[int] = None) -> None:
    """Drop cached profile metadata after a write.

    ``@cached`` keys are partitioned per credential (the *viewer*) and per
    target user, so a single profile write changes what every viewer sees for
    that target: all of their entries must go. ``user_id`` scopes the drop to
    one profile; ``None`` clears the whole namespace (bulk reaper / resets).

    This is a best-effort coherence hook: failures are swallowed so a cache
    outage can never fail an otherwise successful profile write. A missed
    invalidation can only leave a stale entry until its short TTL expires.
    """
    try:
        from src.core.database import invalidate_pattern

        if user_id is None:
            invalidate_pattern(f"{PROFILE_READ_CACHE_PREFIX}:*")
            return
        uid = int(user_id)
        # User-token keys end in ``user_id:int:<uid>``; bot-token keys carry the
        # target as ``user_id=<uid>`` (see authenticated_cache_key). Both must
        # be cleared because a write is visible to every credential.
        invalidate_pattern(f"{PROFILE_READ_CACHE_PREFIX}:*:user_id:int:{uid}")
        invalidate_pattern(f"{PROFILE_READ_CACHE_PREFIX}:bot:*:user_id={uid}")
    except Exception as e:
        logger.debug(f"Profile read cache invalidation failed for {user_id}: {e}")


def _strip_internal_keys(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``data`` without internal bookkeeping keys.

    ``get_profile`` flags expired custom statuses for the manager's own use;
    the flag is stripped here so API responses never expose it (F7).
    """
    if not data:
        return data
    return {k: v for k, v in data.items() if k not in _INTERNAL_PROFILE_KEYS}


def _is_internal_address(address: Any) -> bool:
    """True for an address no public profile link should ever target."""
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def _host_is_internal(hostname: str) -> bool:
    """Recognise internal hosts without resolving DNS.

    ``ipaddress`` only parses canonical IPv4/IPv6 spellings. Browsers, however,
    treat several alternative spellings of the same literal as an IP address:
    a decimal integer (``2130706433``), hex (``0x7f000001``), octal
    (``0177.0.0.1``) and shorthand with fewer than four octets (``127.1``).
    Every one of them resolves to loopback in a viewer's browser, so letting
    them through the guard defeated its purpose: a stored profile link could
    silently target the viewer's own machine or local network.

    ``socket.inet_aton`` parses exactly those numeric/shorthand forms and,
    crucially, never consults DNS. Anything it cannot parse is a hostname and
    is left alone (resolved-address checks belong in the server-side fetch
    gate, :mod:`src.core.security.outbound`, which this module is not).
    """
    host = hostname.rstrip(".")
    try:
        return _is_internal_address(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        packed = socket.inet_aton(host)
    except (OSError, ValueError, UnicodeError):
        return False
    return _is_internal_address(ipaddress.ip_address(packed))


def _ssrf_guard_url(url: str, field_name: str) -> None:
    """Reject a stored link whose host is plainly internal.

    Social links and banner URLs are rendered for a browser to open; the server
    never fetches them. Resolving DNS here made saving a profile depend on the
    resolver, so a valid link such as ``https://cdn.example.com/...`` was
    rejected whenever resolution was unavailable (offline install, CI, a
    resolver outage) even though nothing was ever going to connect to it.

    Literal internal addresses -- including the numeric and shorthand IPv4
    spellings browsers accept -- are still refused, without touching the
    network. Resolved-address checks belong where the server does connect, in
    :mod:`src.core.security.outbound`.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise OutboundUrlRejected(
            f"{field_name} is not a valid URL", field=field_name
        ) from exc
    # A literal backslash in the authority is a parser differential: urlparse
    # leaves it in ``netloc`` and reports the host to the RIGHT of an ``@``,
    # while browsers and http(s) clients read ``\`` as ``/`` and open the
    # address on the LEFT. ``https://169.254.169.254\@public.example/`` would
    # otherwise be stored as a public link and open the metadata address in a
    # viewer's browser. Percent-encoded ``%5C`` and path backslashes are data,
    # not authority, and stay allowed. Mirrors the server-side fetch gate in
    # :mod:`src.core.security.outbound` and web_push._require_public_endpoint.
    if "\\" in (parsed.netloc or ""):
        raise OutboundUrlRejected(
            f"{field_name} must not contain a backslash in the URL authority",
            field=field_name,
        )
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise OutboundUrlRejected(f"{field_name} is missing a host", field=field_name)
    host = hostname.rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise OutboundUrlRejected(
            f"{field_name} must not point at an internal host", field=field_name
        )
    if _host_is_internal(hostname):
        raise OutboundUrlRejected(
            f"{field_name} must not point at an internal address", field=field_name
        )


def _validate_profile_url(url: str, field_name: str) -> str:
    """Apply the http(s) scheme allowlist and then the literal SSRF gate.

    ``require_http_url`` rejects non-absolute URLs, embedded credentials and
    dangerous schemes (``javascript:``, ``data:``, ``file:`` ...); the existing
    host gate additionally refuses plainly internal addresses. Every stored
    profile URL goes through both, so no caller can bypass either check.
    """
    candidate = require_http_url(url, field_name)
    _ssrf_guard_url(candidate, field_name)
    return candidate


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
        return timestamp_ms()

    def _generate_id(self) -> int:
        return generate_id()

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
            platform = _bounded_text(
                link.get("platform", "other"),
                "social link platform",
                MAX_SOCIAL_PLATFORM_LENGTH,
            ).lower()
            url = _bounded_text(
                link.get("url", ""), "social link url", MAX_SOCIAL_LINK_URL_LENGTH
            )
            if platform not in self.ALLOWED_SOCIAL_PLATFORMS:
                platform = "other"
            if not url:
                continue
            # A scheme-less host is a common shorthand; upgrade it to https so
            # the shared allowlist can judge it. Anything with an explicit
            # scheme is left as-is and rejected by require_http_url if unsafe.
            if "://" not in url:
                url = "https://" + url
            url = _validate_profile_url(url, "social link url")
            validated.append({"platform": platform, "url": url})

        if len(validated) > self.MAX_SOCIAL_LINKS:
            raise ValueError(f"Maximum {self.MAX_SOCIAL_LINKS} social links allowed")
        return validated

    def get_profile(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get a user's profile (public shape; internal keys stripped).

        Read-only: this never mutates the database. If no row exists a default
        in-memory dict is returned (no INSERT), so a fetch can be served by a
        read replica and does not burn a write transaction. The persisted row
        is created lazily on the first write via :meth:`_ensure_profile_row`.
        """
        return _strip_internal_keys(self._get_profile_data(user_id))

    def _get_profile_data(self, user_id: int) -> Dict[str, Any]:
        """Raw profile fetch including the internal ``_custom_status_expired``
        flag. Callers serving this dict to clients MUST pass it through
        :func:`_strip_internal_keys` first."""
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

        Clearing semantics (explicit, documented):
          - ``None`` (or the kwarg omitted) means "leave unchanged";
          - an empty string means "clear this field" — after strip/truncate
            the empty value is persisted as NULL;
          - ``social_links=[]`` clears all links (``[]`` is stored).
        This is what lets clients actually clear a previously-set field
        instead of it persisting forever.
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
            bio = _bounded_text(bio, "bio", self.MAX_BIO_LENGTH)
            # Defence in depth: bio is rendered to profile viewers, so run it
            # through the same XSS denylist used for rendered embed text.
            try:
                bio = sanitize_content(bio)
            except EmbedSanitizationError as exc:
                raise ValueError(f"Bio contains unsafe content: {exc}") from exc
            # Normalise empty bio to NULL so it is indistinguishable from
            # "never set", matching the other optional text fields.
            sets.append("bio = ?")
            params.append(bio if bio else None)

        if banner_url is not None:
            # A single gate for both the scheme allowlist and internal hosts;
            # the scheme validator also refuses embedded credentials.
            banner_url = _bounded_text(banner_url, "banner_url", MAX_PROFILE_URL_LENGTH)
            if banner_url:
                banner_url = _validate_profile_url(banner_url, "banner_url")
            sets.append("banner_url = ?")
            params.append(banner_url if banner_url else None)

        if social_links is not None:
            validated = self._validate_social_links(social_links)
            sets.append("social_links = ?")
            params.append(json.dumps(validated))

        if pronouns is not None:
            pronouns = _bounded_text(pronouns, "pronouns", self.MAX_PRONOUNS_LENGTH)
            sets.append("pronouns = ?")
            params.append(pronouns if pronouns else None)

        if location is not None:
            location = _bounded_text(location, "location", self.MAX_LOCATION_LENGTH)
            sets.append("location = ?")
            params.append(location if location else None)

        if timezone is not None:
            timezone = _bounded_text(timezone, "timezone", self.MAX_TIMEZONE_LENGTH)
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
        # Drop every viewer's cached copy of this profile after the write.
        invalidate_profile_read_cache(user_id)
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
        current = self._get_profile_data(user_id)
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
            stripped = _bounded_text(text, "custom_status", self.MAX_STATUS_LENGTH)
            new_text = stripped if stripped else None

        if emoji is _UNSET:
            new_emoji = current.get("custom_status_emoji")
        elif emoji is None:
            new_emoji = None
        else:
            stripped = _bounded_text(
                emoji, "custom_status_emoji", self.MAX_STATUS_EMOJI_LENGTH
            )
            new_emoji = stripped if stripped else None

        if expires_at is _UNSET:
            new_expires = current.get("custom_status_expires_at")
        else:
            new_expires = self._normalise_expiry(expires_at, now)

        # Dual write (user_profiles + auth_users) must be atomic: if the
        # second UPDATE failed the two stores would diverge — presence reads
        # auth_users while profile reads user_profiles.
        self._db.begin_transaction()
        try:
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
        except Exception:
            self._db.rollback()
            raise
        else:
            self._db.commit()

        logger.debug(f"User {user_id} set custom status")
        # The status fields are part of the cached profile projection.
        invalidate_profile_read_cache(user_id)
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
        # Bulk write may have changed many profiles; drop the whole namespace.
        invalidate_profile_read_cache(None)
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
        # Batch fetch from user_profiles with bounded IN lists. Dedupe in
        # first-seen order so a repeated id cannot inflate the bind count.
        unique_ids = list(dict.fromkeys(user_ids))
        for chunk in _iter_id_chunks(unique_ids):
            placeholders = ",".join(["?"] * len(chunk))
            rows = self._db.fetch_all(
                f"SELECT * FROM user_profiles WHERE user_id IN ({placeholders})",
                tuple(chunk),
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
                results[uid] = _strip_internal_keys(data)

        # In-memory defaults for missing users (no DB write).
        for uid in user_ids:
            if uid not in results:
                results[uid] = self._default_profile_dict(uid)

        return results
