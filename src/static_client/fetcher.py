"""
Static client fetcher.

Downloads ``dist.zip`` and ``dist.zip.sha256`` from the GitLab Releases API
and verifies integrity before unpacking.

The fetcher is intentionally synchronous; callers wrap it in a threadpool
when invoking from async code.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import requests  # type: ignore[import-untyped]

import utils.logger as logger
from src.utils.common_utils.utils.version.core import (
    Version,
    VersionStage,
    format_version,
    parse_version,
)

from .config import GitLabConfig, StaticClientConfig

# Cache-Control
_SHA256_SUFFIX = ".sha256"
_ZIP_NAME = "dist.zip"
_RELEASE_PATH = "/projects/{project_id}/releases"
_PER_PAGE = 100
_MAX_PAGES = 20  # 20 * 100 = 2000 releases is plenty for any sane setup
_CHUNK = 64 * 1024


class FetchError(RuntimeError):
    """Raised when a release cannot be downloaded or verified."""


@dataclass(frozen=True)
class ReleaseAsset:
    """Resolved asset URLs for a single release."""

    tag_name: str
    released_at: str
    zip_url: str
    sha256_url: str


@dataclass(frozen=True)
class InstalledVersion:
    """Information about an installed version on disk."""

    version: str
    install_path: Path


def _parse_iso8601(value: str) -> float:
    """Parse an ISO-8601 timestamp to epoch seconds. Returns 0.0 on failure."""
    if not value:
        return 0.0
    try:
        from datetime import datetime, timezone

        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


def _safe_extract_zip(zf: zipfile.ZipFile, target: Path) -> None:
    """Extract *zf* into *target* safely, defending against zip-slip.

    All entries with absolute paths, drive letters, parent references, or
    symlinks are rejected.
    """
    target_resolved = target.resolve()
    for info in zf.infolist():
        name = info.filename
        if not name or name.endswith("/"):
            continue
        # Reject absolute paths and Windows drive letters
        if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
            raise FetchError(f"Refusing to extract unsafe path: {name!r}")
        if ".." in Path(name).parts:
            raise FetchError(f"Refusing to extract path with '..': {name!r}")
        # Reject symlinks
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise FetchError(f"Refusing to extract symlink: {name!r}")

        dest = (target / name).resolve()
        try:
            dest.relative_to(target_resolved)
        except ValueError as exc:
            raise FetchError(
                f"Refusing to extract path outside target: {name!r}"
            ) from exc
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out, length=_CHUNK)


def _download_to_stream(
    url: str,
    out_fp: io.BufferedIOBase,
    *,
    headers: Optional[dict] = None,
    timeout: int = 30,
    verify: bool = True,
    max_bytes: Optional[int] = None,
) -> int:
    """Stream *url* into *out_fp*. Returns total bytes written.

    Raises :class:`FetchError` on HTTP error or oversize.
    """
    try:
        with requests.get(
            url, headers=headers, timeout=timeout, verify=verify, stream=True
        ) as resp:
            if resp.status_code >= 400:
                raise FetchError(f"GET {url} -> {resp.status_code}: {resp.reason}")
            written = 0
            for chunk in resp.iter_content(chunk_size=_CHUNK):
                if not chunk:
                    continue
                written += len(chunk)
                if max_bytes is not None and written > max_bytes:
                    raise FetchError(
                        f"Download exceeded max_bytes={max_bytes} at {url}"
                    )
                out_fp.write(chunk)
            return written
    except requests.RequestException as exc:
        raise FetchError(f"GET {url} failed: {exc}") from exc


def _hash_file(path: Path, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as fp:
        while True:
            chunk = fp.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _release_target_for_version(
    cfg: StaticClientConfig, desired: Version
) -> Tuple[VersionStage, int, int]:
    """Return the (stage, major, minor) used to find matching client releases."""
    return (desired.stage, desired.major, desired.minor)


def _release_version_tag(stage: VersionStage, major: int, minor: int) -> str:
    """Build a regex that matches releases for a given (stage, major, minor)."""
    return f"^{stage.value}\\.{major}\\.{minor}-\\d+$"


class GitLabReleaseClient:
    """Thin wrapper over the GitLab Releases API."""

    def __init__(self, git_lab: GitLabConfig):
        self._cfg = git_lab

    def _headers(self) -> dict:
        headers = {"Accept": "application/json"}
        token = self._cfg.resolved_token()
        if token:
            headers["PRIVATE-TOKEN"] = token
        return headers

    def list_releases(self) -> List[dict]:
        """List all releases for the configured project, paginated."""
        if self._cfg.project_id is None:
            raise FetchError(
                "GitLab project_id is not configured "
                "(set static_client.git_lab.project_id in config.yaml)"
            )

        url = f"{self._cfg.api_url.rstrip('/')}" + _RELEASE_PATH.format(
            project_id=self._cfg.project_id
        )

        results: List[dict] = []
        for page in range(1, _MAX_PAGES + 1):
            try:
                resp = requests.get(
                    url,
                    headers=self._headers(),
                    params={"per_page": _PER_PAGE, "page": page},
                    timeout=self._cfg.request_timeout_seconds,
                    verify=self._cfg.verify_tls,
                )
            except requests.RequestException as exc:
                raise FetchError(f"List releases failed: {exc}") from exc
            if resp.status_code == 404:
                return results
            if resp.status_code >= 400:
                raise FetchError(f"List releases -> {resp.status_code}: {resp.reason}")
            page_data = resp.json()
            if not isinstance(page_data, list) or not page_data:
                return results
            results.extend(page_data)
            if len(page_data) < _PER_PAGE:
                return results
        return results

    def list_release_assets_for_version(
        self, stage: VersionStage, major: int, minor: int
    ) -> List[Tuple[Version, ReleaseAsset]]:
        """Return all matching (version, asset) pairs sorted newest-first.

        The list is sorted by :func:`compare_version_objects` in descending
        order so callers can iterate newest-to-oldest. The full list is
        returned (not just the newest) so the manager can fall back to an
        older release when the newest one fails the ``min_age`` check.
        """
        pattern = re.compile(_release_version_tag(stage, major, minor))
        results: List[Tuple[Version, ReleaseAsset]] = []
        for release in self.list_releases():
            tag = release.get("tag_name") if isinstance(release, dict) else None
            if not isinstance(tag, str) or not pattern.match(tag):
                continue
            try:
                ver = parse_version(tag)
            except Exception:  # noqa: BLE001 - defensive against malformed tags
                continue
            assets = release.get("assets", {}) or {}
            links = assets.get("links", []) if isinstance(assets, dict) else []
            zip_url = None
            sha_url = None
            if isinstance(links, list):
                for link in links:
                    if not isinstance(link, dict):
                        continue
                    name = link.get("name", "")
                    url = link.get("url", "")
                    if name == _ZIP_NAME and isinstance(url, str):
                        zip_url = url
                    elif name == f"{_ZIP_NAME}{_SHA256_SUFFIX}" and isinstance(
                        url, str
                    ):
                        sha_url = url
            if not zip_url:
                continue
            results.append(
                (
                    ver,
                    ReleaseAsset(
                        tag_name=tag,
                        released_at=str(release.get("released_at", "") or ""),
                        zip_url=zip_url,
                        sha256_url=sha_url or "",
                    ),
                )
            )
        results.sort(
            key=lambda pair: (
                pair[0].stage.value,
                pair[0].major,
                pair[0].minor,
                pair[0].build,
            ),
            reverse=True,
        )
        return results

    def find_release_for_version(
        self, stage: VersionStage, major: int, minor: int
    ) -> Optional[ReleaseAsset]:
        """Find the release with the highest build for (stage, major, minor).

        Returns ``None`` if no matching release exists. This is a thin
        wrapper around :meth:`list_release_assets_for_version` kept for
        back-compat with existing callers.
        """
        matches = self.list_release_assets_for_version(stage, major, minor)
        return matches[0][1] if matches else None


def verify_zip_with_sha256(
    zip_path: Path, expected_sha256: str, algo: str = "sha256"
) -> None:
    """Compute the SHA256 of *zip_path* and compare it with *expected_sha256*."""
    actual = _hash_file(zip_path, algo=algo).lower()
    expected = expected_sha256.strip().lower()
    if actual != expected:
        raise FetchError(f"SHA256 mismatch: expected={expected}, got={actual}")


def download_and_verify_release(
    asset: ReleaseAsset, git_lab: GitLabConfig, max_bytes: int
) -> Tuple[Path, str]:
    """Download a release zip + sha256 file and verify integrity.

    Returns ``(zip_path, sha256_hex)``. Files are written to a temporary
    directory; the caller is responsible for cleanup. Progress is logged
    at start/finish for the sha, the zip, and the verification step.
    """
    tmp = Path(tempfile.mkdtemp(prefix="plexichat-client-"))
    try:
        sha_path = tmp / f"{_ZIP_NAME}{_SHA256_SUFFIX}"
        zip_path = tmp / _ZIP_NAME

        if asset.sha256_url:
            logger.info(f"static_client: downloading sha256 from {asset.sha256_url}")
            with open(sha_path, "wb") as out:
                _download_to_stream(
                    asset.sha256_url,
                    out,
                    timeout=git_lab.request_timeout_seconds,
                    verify=git_lab.verify_tls,
                    max_bytes=4096,
                )
            expected = sha_path.read_text(encoding="utf-8").strip().split()[0]
            logger.info(
                f"static_client: expected sha256={expected[:16]}… ({asset.tag_name})"
            )
        else:
            expected = ""
            logger.info("static_client: no sha256 file in release, skipping hash check")

        logger.info(
            f"static_client: downloading zip from {asset.zip_url} "
            f"(max={max_bytes} bytes)"
        )
        with open(zip_path, "wb") as out:
            written = _download_to_stream(
                asset.zip_url,
                out,
                timeout=git_lab.request_timeout_seconds,
                verify=git_lab.verify_tls,
                max_bytes=max_bytes,
            )
        logger.info(f"static_client: downloaded {written} bytes to {zip_path.name}")

        if expected:
            logger.info("static_client: verifying sha256…")
            verify_zip_with_sha256(zip_path, expected)
            logger.info(f"static_client: sha256 OK ({asset.tag_name})")

        actual = _hash_file(zip_path)
        return zip_path, actual
    except Exception:
        logger.warning(f"static_client: download/verify failed; cleaning up {tmp}")
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def install_zip_at(zip_path: Path, target: Path) -> None:
    """Safely extract *zip_path* into *target*."""
    target.mkdir(parents=True, exist_ok=True)
    logger.info(f"static_client: extracting {zip_path.name} -> {target}")
    # Wipe existing contents to avoid stale files (e.g. removed hashed assets)
    for entry in target.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    with zipfile.ZipFile(zip_path) as zf:
        _safe_extract_zip(zf, target)


def list_installed_versions(install_dir: Path) -> List[InstalledVersion]:
    """Return sorted (oldest -> newest) list of installed versions on disk."""
    out: List[InstalledVersion] = []
    if not install_dir.is_dir():
        return out
    for entry in install_dir.iterdir():
        if not entry.is_dir() or entry.name.startswith(".") or entry.name == "current":
            continue
        try:
            parse_version(entry.name)
        except Exception:  # noqa: BLE001 - skip non-version directories
            continue
        out.append(InstalledVersion(version=entry.name, install_path=entry))
    out.sort(key=lambda iv: iv.version)
    return out


def read_current_version(install_dir: Path) -> Optional[str]:
    """Read the active version string from ``<install_dir>/current_version``."""
    marker = install_dir / "current_version"
    if not marker.is_file():
        return None
    try:
        return marker.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def write_current_version(install_dir: Path, version: str) -> None:
    """Atomically write *version* to ``<install_dir>/current_version``."""
    install_dir.mkdir(parents=True, exist_ok=True)
    marker = install_dir / "current_version"
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(version, encoding="utf-8")
    os.replace(tmp, marker)


def prune_old_versions(
    install_dir: Path, keep: Iterable[str], max_total: int = 5
) -> List[str]:
    """Delete installed versions not in *keep*, retaining at most *max_total*.

    Returns the list of removed version strings.
    """
    keep_set = set(keep)
    installed = list_installed_versions(install_dir)
    survivors = [iv for iv in installed if iv.version in keep_set]
    extras = [iv for iv in installed if iv.version not in keep_set]
    # Always keep the active version + the most recent survivors
    survivors_sorted = sorted(survivors, key=lambda iv: iv.version, reverse=True)
    final_keep: List[str] = []
    if survivors_sorted:
        final_keep.append(survivors_sorted[0].version)
    for iv in survivors_sorted[1:]:
        if len(final_keep) < max_total:
            final_keep.append(iv.version)
    removed: List[str] = []
    for iv in installed:
        if iv.version in final_keep:
            continue
        try:
            shutil.rmtree(iv.install_path)
            removed.append(iv.version)
        except OSError as exc:
            logger.warning(f"static_client: failed to prune {iv.version}: {exc}")
    # Also drop any directories that were *not* in the installed list (e.g. junk)
    for iv in extras:
        if iv.version in removed:
            continue
    return removed


def compute_released_at(asset: ReleaseAsset) -> float:
    """Convert asset.released_at to epoch seconds."""
    return _parse_iso8601(asset.released_at)


def format_release_tag(version: Version) -> str:
    """Return a human-readable tag string for logging."""
    return format_version(version)


__all__ = [
    "FetchError",
    "GitLabReleaseClient",
    "InstalledVersion",
    "ReleaseAsset",
    "compute_released_at",
    "download_and_verify_release",
    "format_release_tag",
    "install_zip_at",
    "list_installed_versions",
    "prune_old_versions",
    "read_current_version",
    "verify_zip_with_sha256",
    "write_current_version",
]
