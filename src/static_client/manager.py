"""
Static client manager.

High-level orchestrator that decides which version of the web client should
be active, performs the GitLab fetch+verify+unpack when needed, writes the
runtime config.js, and exposes the on-disk install path to the router.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import utils.config as _config
import utils.logger as logger
import utils.version as _version
from src.utils.common_utils.utils.version.core import (
    Version,
    compare_version_objects,
    parse_version,
)

from .config import StaticClientConfig, get_static_client_config
from .fetcher import (
    FetchError,
    GitLabReleaseClient,
    InstalledVersion,
    ReleaseAsset,
    compute_released_at,
    download_and_verify_release,
    format_release_tag,
    install_zip_at,
    list_installed_versions,
    prune_old_versions,
    read_current_version,
    write_current_version,
)

# Re-export InstallResult to keep callers stable
from . import fetcher as _fetcher  # noqa: F401  (kept for back-compat)


@dataclass(frozen=True)
class InstallResult:
    """Result of a fetch+install attempt."""

    installed_version: Optional[str]
    already_current: bool
    error: Optional[str] = None


class StaticClientManager:
    """Owns the on-disk install state for the web client."""

    def __init__(self, cfg: Optional[StaticClientConfig] = None):
        self._cfg = cfg or get_static_client_config()
        self._install_dir = self._cfg.install_path()
        self._install_dir.mkdir(parents=True, exist_ok=True)
        self._last_check: float = 0.0
        self._last_installed: Optional[str] = None

    @property
    def config(self) -> StaticClientConfig:
        return self._cfg

    @property
    def install_dir(self) -> Path:
        return self._install_dir

    def current_version(self) -> Optional[str]:
        """Return the version string marked active on disk, if any."""
        return read_current_version(self._install_dir)

    def current_install_path(self) -> Optional[Path]:
        """Return the absolute path to the active dist directory, if any."""
        version = self.current_version()
        if not version:
            return None
        path = self._install_dir / version
        if not path.is_dir():
            return None
        return path

    def installed_versions(self) -> List[InstalledVersion]:
        """Return all on-disk installed versions, oldest first."""
        return list_installed_versions(self._install_dir)

    def server_version(self) -> Optional[Version]:
        """Return the current server's parsed version, if available."""
        try:
            return _version.current()
        except (RuntimeError, ValueError):
            return None

    def desired_release_target(
        self, now: Optional[float] = None
    ) -> Optional[Tuple[Version, ReleaseAsset]]:
        """Return the (version, asset) that should be served right now.

        Returns ``None`` if no matching release exists yet.
        """
        if self._cfg.source != "gitlab_release":
            return None
        if self._cfg.git_lab.project_id is None:
            logger.debug("static_client: git_lab.project_id not configured")
            return None

        server_ver = self.server_version()
        if server_ver is None:
            return None

        client = GitLabReleaseClient(self._cfg.git_lab)
        asset = client.find_release_for_version(
            server_ver.stage, server_ver.major, server_ver.minor
        )
        if asset is None:
            return None
        try:
            parsed = parse_version(asset.tag_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"static_client: skipping release with unparsable tag {asset.tag_name!r}: {exc}"
            )
            return None
        return parsed, asset

    def _should_fetch(self, asset: ReleaseAsset, parsed: Version, now: float) -> bool:
        """Return True if the release passes the min-age check."""
        if not self._cfg.auto_update:
            return True
        released_at = compute_released_at(asset)
        if released_at <= 0.0:
            return True  # unknown release time - allow
        age = now - released_at
        if age < self._cfg.auto_update_min_age_seconds:
            logger.info(
                f"static_client: release {format_release_tag(parsed)} is "
                f"only {int(age)}s old, below min_age="
                f"{self._cfg.auto_update_min_age_seconds}s; skipping"
            )
            return False
        return True

    def ensure_active(self) -> InstallResult:
        """Make sure the active version matches the desired release.

        Idempotent: returns ``already_current=True`` if nothing to do.
        Does nothing if ``enabled`` is False.
        """
        if not self._cfg.enabled:
            return InstallResult(
                installed_version=self.current_version(),
                already_current=True,
                error="disabled",
            )

        try:
            target = self.desired_release_target()
        except FetchError as exc:
            return InstallResult(
                installed_version=self.current_version(),
                already_current=True,
                error=f"resolve: {exc}",
            )

        if target is None:
            return InstallResult(
                installed_version=self.current_version(),
                already_current=True,
                error="no_matching_release",
            )

        parsed, asset = target
        current = self.current_version()

        if current is not None:
            try:
                if compare_version_objects(parsed, parse_version(current)) <= 0:
                    return InstallResult(
                        installed_version=current,
                        already_current=True,
                    )
            except Exception:  # noqa: BLE001
                pass  # fall through and reinstall

        if not self._should_fetch(asset, parsed, time.time()):
            return InstallResult(
                installed_version=current,
                already_current=True,
                error="below_min_age",
            )

        return self._fetch_and_install(parsed, asset)

    def _fetch_and_install(self, parsed: Version, asset: ReleaseAsset) -> InstallResult:
        target_dir = self._install_dir / format_release_tag(parsed)
        try:
            zip_path, _sha = download_and_verify_release(
                asset, self._cfg.git_lab, self._cfg.max_zip_size_bytes
            )
            install_zip_at(zip_path, target_dir)
        except FetchError as exc:
            logger.warning(
                f"static_client: failed to install {format_release_tag(parsed)}: {exc}"
            )
            return InstallResult(
                installed_version=self.current_version(),
                already_current=True,
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                f"static_client: unexpected error installing {format_release_tag(parsed)}: {exc}",
                exc_info=True,
            )
            return InstallResult(
                installed_version=self.current_version(),
                already_current=True,
                error=f"unexpected: {exc}",
            )

        if self._cfg.config_injection.enabled:
            try:
                self._write_runtime_config(target_dir, format_release_tag(parsed))
            except OSError as exc:
                logger.warning(f"static_client: failed to write config.js: {exc}")

        write_current_version(self._install_dir, format_release_tag(parsed))
        prune_old_versions(self._install_dir, keep=[format_release_tag(parsed)])
        self._last_installed = format_release_tag(parsed)
        self._last_check = time.time()

        if self._cfg.log_downloads:
            logger.info(
                f"static_client: installed {format_release_tag(parsed)} -> {target_dir}"
            )
        return InstallResult(
            installed_version=format_release_tag(parsed),
            already_current=False,
        )

    def _write_runtime_config(self, target_dir: Path, version: str) -> None:
        """Write the runtime config.js into the freshly installed dist."""
        cfg = self._cfg.config_injection
        origin = self._detect_origin()
        content = cfg.content.format(origin=origin, version=version)
        out = target_dir / cfg.filename
        out.write_text(content, encoding="utf-8")

    def _detect_origin(self) -> str:
        """Return a sensible ``serverUrl`` value (same-origin by default)."""
        try:
            api_cfg = _config.get("api", {}) or {}
            origins = api_cfg.get("cors_origins", []) or []
            if isinstance(origins, list):
                for o in origins:
                    if isinstance(o, str) and o.startswith(("http://", "https://")):
                        return o
        except RuntimeError:
            pass
        # Fall back to same-origin (empty string means use current page origin)
        return ""

    def maybe_check(self) -> InstallResult:
        """Run :meth:`ensure_active` only if the auto-update interval has elapsed."""
        if not self._cfg.enabled:
            return InstallResult(
                installed_version=self.current_version(),
                already_current=True,
                error="disabled",
            )
        if not self._cfg.auto_update:
            return self.ensure_active()
        now = time.time()
        if now - self._last_check < self._cfg.auto_update_check_interval_seconds:
            return InstallResult(
                installed_version=self.current_version(),
                already_current=True,
            )
        self._last_check = now
        return self.ensure_active()


_manager: Optional[StaticClientManager] = None


def get_static_client_manager() -> Optional[StaticClientManager]:
    """Return the process-wide manager, or ``None`` if feature is disabled."""
    global _manager
    if _manager is not None:
        return _manager
    cfg = get_static_client_config()
    if not cfg.enabled:
        return None
    _manager = StaticClientManager(cfg)
    return _manager


def reset_static_client_manager() -> None:
    """Drop the cached manager (used in tests)."""
    global _manager
    _manager = None


__all__ = [
    "InstallResult",
    "StaticClientManager",
    "get_static_client_manager",
    "reset_static_client_manager",
]
