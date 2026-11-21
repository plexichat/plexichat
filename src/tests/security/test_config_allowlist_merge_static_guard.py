"""Static guard: broad list-valued config defaults must be explicitly classified.

``ConfigLoader._deep_merge`` concatenates string lists.  Without the
``_EXPLICIT_ALLOWLIST_PATHS`` restore step, an operator who *narrows* a security
allowlist silently gets the broad code defaults re-added - a fail-open
widening.  The restore step only covers the paths named in that tuple, so a new
allowlist added to ``config_defaults.py`` is an unprotected gate until someone
remembers to add its path.

This guard enumerates every list-valued default that ships with a broad,
non-empty value and requires each one to be in exactly one bucket:

* a merge-protected security allowlist (named in
  ``utils.config._EXPLICIT_ALLOWLIST_PATHS``), or
* a documented non-allowlist - a denylist, or a capability/catalog/operational
  list where re-adding the defaults is not a privilege widening.

Adding a broad list default without classifying it fails the test with the
exact path to triage.  The parametrized self-test removes a protected path and
proves the guard flags it, so the guard cannot pass vacuously.
"""

from typing import Any, Dict, Set, Tuple

import pytest

import utils.config as config_utils
from config_defaults import get_default_config

ConfigPath = Tuple[str, ...]

# Security gates whose explicit operator value must win over the broad
# defaults.  Keeping this expectation in the test means deleting a tuple entry
# from the module is caught directly, not only via the generic classifier.
SECURITY_ALLOWLISTS = frozenset(
    {
        ("api", "cors_origins"),
        ("api", "cors_allow_methods"),
        ("api", "cors_allow_headers"),
        ("embeds", "url_preview", "allowed_schemes"),
        ("admin_ui", "host_restriction", "allowed_hosts"),
    }
)

# Every other non-empty list default is classified here with the reason it is
# not a fail-open access-control allowlist.  A "broad-by-design" entry is a
# supported-format/feature catalog or an operational list: re-adding those
# defaults restores product capability, it does not grant a caller a privilege
# the operator meant to withhold.  A "DENYLIST" entry is fail-safe in the same
# direction - re-adding defaults forbids more, never less.
DOCUMENTED_NON_ALLOWLISTS: Dict[ConfigPath, str] = {
    ("authentication", "account_deletion", "reminder_days_before_purge"): (
        "numeric schedule offsets, not an access-control list"
    ),
    ("authentication", "dsar", "export_formats"): (
        "broad-by-design: supported DSAR export formats"
    ),
    ("media", "auto_route_to_database", "content_types"): (
        "content-type routing rule; widening only routes more content to storage"
    ),
    ("media", "allowed_types", "image"): (
        "broad-by-design: supported image upload MIME catalog"
    ),
    ("media", "allowed_types", "video"): (
        "broad-by-design: supported video upload MIME catalog"
    ),
    ("media", "allowed_types", "audio"): (
        "broad-by-design: supported audio upload MIME catalog"
    ),
    ("media", "allowed_types", "document"): (
        "broad-by-design: supported document upload MIME catalog"
    ),
    ("media", "thumbnail_sizes"): (
        "numeric thumbnail dimensions, not an access-control list"
    ),
    ("user_features", "available_badges"): (
        "broad-by-design: catalog of awardable badges"
    ),
    ("voice", "stun_urls"): ("broad-by-design: default STUN infrastructure endpoints"),
    ("selftest", "excluded_endpoints"): (
        "self-test exclusion list; operational, not a request authorization gate"
    ),
    ("admin_ui", "approval_workflows", "require_approval_for"): (
        "fail-safe: re-adding defaults requires approval for more actions, never fewer"
    ),
    ("emojis", "allowed_formats"): (
        "broad-by-design: supported emoji upload MIME catalog"
    ),
    ("stickers", "allowed_formats"): ("broad-by-design: supported sticker formats"),
    ("soundboard", "allowed_formats"): (
        "broad-by-design: supported sound upload formats"
    ),
    ("artifacts", "plexiscript", "allowed_languages"): (
        "broad-by-design: supported code-editor language catalog"
    ),
    ("artifacts", "plexiscript", "allowed_extensions"): (
        "broad-by-design: supported code-editor extension catalog"
    ),
    ("artifacts", "plexiscript", "blocked_extensions"): (
        "DENYLIST: re-adding defaults blocks more extensions, never fewer"
    ),
    ("bots", "bot_avatar_allowed_types"): (
        "broad-by-design: supported bot-avatar MIME catalog"
    ),
    ("avatars", "allowed_types"): (
        "broad-by-design: supported avatar upload MIME catalog"
    ),
    ("avatars", "default_colors"): ("broad-by-design: default avatar color palette"),
    ("transcript_export", "allowed_formats"): (
        "broad-by-design: supported transcript export formats"
    ),
    ("dsar", "export_formats"): ("broad-by-design: supported DSAR export formats"),
}


def _broad_list_paths(node: Any, prefix: ConfigPath = ()) -> Set[ConfigPath]:
    """Tuple paths of every list default that ships non-empty."""
    found: Set[ConfigPath] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            found |= _broad_list_paths(value, prefix + (str(key),))
    elif isinstance(node, list) and node:
        found.add(prefix)
    return found


def _default_paths() -> Set[ConfigPath]:
    # Pin the version: this is a pure structural scan of the shipped defaults.
    return _broad_list_paths(get_default_config(version="static-guard"))


def _protected_paths() -> Set[ConfigPath]:
    return {tuple(path) for path in config_utils._EXPLICIT_ALLOWLIST_PATHS}


def _unclassified(
    paths: Set[ConfigPath],
    protected: Set[ConfigPath],
    documented: Dict[ConfigPath, str] = DOCUMENTED_NON_ALLOWLISTS,
):
    return sorted(
        path for path in paths if path not in protected and path not in documented
    )


def test_security_allowlists_are_merge_protected():
    """All known fail-open gates must be restored from the raw config."""
    missing = sorted(SECURITY_ALLOWLISTS - _protected_paths())
    assert not missing, (
        "Security allowlist(s) missing from utils.config._EXPLICIT_ALLOWLIST_PATHS: "
        f"{missing}. Narrowing them would silently re-add the broad defaults."
    )


def test_every_broad_list_default_is_classified():
    """A new broad list default cannot ship without a security decision."""
    unclassified = _unclassified(_default_paths(), _protected_paths())
    assert not unclassified, (
        "Broad list-valued config default(s) are neither merge-protected nor "
        f"documented: {unclassified}. If narrowing the list is a hardening, add "
        "the path to utils.config._EXPLICIT_ALLOWLIST_PATHS. Otherwise add it to "
        "DOCUMENTED_NON_ALLOWLISTS in this test with a reason (denylist / "
        "broad-by-design catalog)."
    )


def test_documented_exceptions_are_exact():
    """Stale or doubly-classified entries must not rot the guard."""
    paths = _default_paths()
    stale = sorted(set(DOCUMENTED_NON_ALLOWLISTS) - paths)
    assert not stale, f"Documented entry no longer in config defaults: {stale}"
    overlap = sorted(set(DOCUMENTED_NON_ALLOWLISTS) & _protected_paths())
    assert not overlap, f"Path both documented and merge-protected: {overlap}"


@pytest.mark.parametrize("victim", sorted(SECURITY_ALLOWLISTS))
def test_guard_flags_removed_protected_path(monkeypatch, victim):
    """Non-vacuity: deleting any protected tuple entry must be flagged."""
    paths = _default_paths()
    assert not _unclassified(paths, _protected_paths())

    monkeypatch.setattr(
        config_utils,
        "_EXPLICIT_ALLOWLIST_PATHS",
        tuple(
            entry
            for entry in config_utils._EXPLICIT_ALLOWLIST_PATHS
            if tuple(entry) != victim
        ),
    )
    assert victim in _unclassified(paths, _protected_paths())
