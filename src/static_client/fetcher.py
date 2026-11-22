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
from typing import Any, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

import utils.logger as logger
from src.core.security.outbound import parse_outbound_url
from src.utils.common_utils.utils.version.core import (
    Version,
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
# Safety factor converting the compressed download cap into an uncompressed
# extraction budget. At the default 100MB zip cap this allows ~2GB unpacked,
# which is far larger than any real client dist while still bounding a
# decompression bomb.
_UNCOMPRESSED_FACTOR = 20
# Pre-extraction archive guards. A release dist.zip never needs thousands of
# members, and a payload that expands more than a few hundredfold while being
# larger than a megabyte is a zip bomb rather than web assets. These are
# checked from the ZIP central directory *before* any member is written.
_MAX_ARCHIVE_ENTRIES = 20_000
_MAX_EXPANSION_RATIO = 256
_RATIO_MIN_OUTPUT_BYTES = 8 * 1024 * 1024
# Redirects are resolved by us, never by the HTTP client. Bound the chain and
# re-run the shared SSRF gate on every hop so a public release endpoint cannot
# steer a credentialed request to loopback or the cloud metadata service.
_MAX_SAFE_REDIRECTS = 5
_CREDENTIAL_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "private-token", "cookie"}
)
# A SHA256 digest is exactly 32 bytes rendered as 64 lowercase hex characters.
_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


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


def _check_member_name(info: zipfile.ZipInfo) -> None:
    """Reject absolute/drive/traversal/symlink member names before extraction."""
    name = info.filename
    # Reject absolute paths and Windows drive letters
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        raise FetchError(f"Refusing to extract unsafe path: {name!r}")
    if ".." in Path(name).parts:
        raise FetchError(f"Refusing to extract path with '..': {name!r}")
    # Reject symlinks (and other special Unix file types)
    if (info.external_attr >> 16) & 0o170000 == 0o120000:
        raise FetchError(f"Refusing to extract symlink: {name!r}")


def _preflight_archive(
    zf: zipfile.ZipFile, max_uncompressed_bytes: Optional[int]
) -> list[zipfile.ZipInfo]:
    """Validate archive metadata before any member is materialized.

    The ZIP central directory declares each member's uncompressed and
    compressed sizes, so the whole expansion can be checked without inflating
    anything. Rejecting here means a crafted archive leaves no partial output
    on disk and cannot burn CPU/disk bit by bit until a mid-stream limit fires.
    """
    infos: list[zipfile.ZipInfo] = []
    declared_total = 0
    compressed_total = 0
    for info in zf.infolist():
        name = info.filename
        if not name or name.endswith("/"):
            continue
        _check_member_name(info)
        infos.append(info)
        if len(infos) > _MAX_ARCHIVE_ENTRIES:
            raise FetchError(f"Archive has too many entries (>{_MAX_ARCHIVE_ENTRIES})")
        size = info.file_size
        if size < 0:
            raise FetchError(f"Refusing to extract member with invalid size: {name!r}")
        if max_uncompressed_bytes is not None and size > max_uncompressed_bytes:
            raise FetchError(
                f"Extraction exceeded max_uncompressed_bytes={max_uncompressed_bytes}"
            )
        declared_total += size
        if (
            max_uncompressed_bytes is not None
            and declared_total > max_uncompressed_bytes
        ):
            raise FetchError(
                f"Extraction exceeded max_uncompressed_bytes={max_uncompressed_bytes}"
            )
        compressed_total += max(0, info.compress_size)

    # A payload that expands many hundredfold is a bomb regardless of the
    # absolute cap (which callers may raise). The floor keeps legitimately
    # compressible but small assets from tripping the ratio check.
    if declared_total > _RATIO_MIN_OUTPUT_BYTES and compressed_total > 0:
        if declared_total // compressed_total > _MAX_EXPANSION_RATIO:
            raise FetchError(
                f"Archive expansion ratio exceeds {_MAX_EXPANSION_RATIO}:1"
            )
    return infos


def _safe_extract_zip(
    zf: zipfile.ZipFile,
    target: Path,
    max_uncompressed_bytes: Optional[int] = None,
) -> None:
    """Extract *zf* into *target* safely, defending against zip-slip and bombs.

    Every member is validated from archive metadata before any file is
    written: absolute/drive/traversal/symlink names, entry count, cumulative
    uncompressed size and expansion ratio are all bounded up front. The
    per-chunk budget below is kept as defence in depth in case a crafted
    archive lies about ``file_size``. If anything fails, all files created
    during this call are removed so no partial tree is left behind.
    """
    # Validate the whole archive before touching the filesystem.
    infos = _preflight_archive(zf, max_uncompressed_bytes)

    target_resolved = target.resolve()
    pre_existing = set(target.rglob("*"))
    total_written = 0
    try:
        for info in infos:
            name = info.filename
            dest = (target / name).resolve()
            try:
                dest.relative_to(target_resolved)
            except ValueError as exc:
                raise FetchError(
                    f"Refusing to extract path outside target: {name!r}"
                ) from exc
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, open(dest, "wb") as out:
                while True:
                    chunk = src.read(_CHUNK)
                    if not chunk:
                        break
                    total_written += len(chunk)
                    if (
                        max_uncompressed_bytes is not None
                        and total_written > max_uncompressed_bytes
                    ):
                        raise FetchError(
                            "Extraction exceeded max_uncompressed_bytes="
                            f"{max_uncompressed_bytes}"
                        )
                    out.write(chunk)
    except BaseException:
        # Fail closed: remove every path created by this call, deepest first
        # so directories are only removed once their contents are gone.
        created = [p for p in target.rglob("*") if p not in pre_existing]
        for path in sorted(created, key=lambda p: len(p.parts), reverse=True):
            try:
                if path.is_dir() and not path.is_symlink():
                    path.rmdir()
                else:
                    path.unlink()
            except OSError:
                pass
        raise


def _origin(url: str) -> str:
    """Scheme+authority of *url*, used to decide when to drop credentials."""
    parsed = urlparse(url)
    return f"{parsed.scheme.lower()}://{(parsed.netloc or '').lower()}"


def _follow_safe_redirects(
    response: Any,
    url: str,
    *,
    headers: Optional[dict] = None,
    timeout: int = 30,
    verify: bool = True,
    stream: bool = False,
    params: Optional[dict] = None,
) -> Any:
    """Follow 3xx hops from *response*, re-validating each target.

    Callers issue the initial (operator-configured) request themselves with
    ``allow_redirects=False`` and hand the response here. ``requests`` follows
    redirects by default and preserves custom headers (including
    ``PRIVATE-TOKEN``) across a cross-host redirect, so a public endpoint that
    answers ``302 Location: http://169.254.169.254/...`` would otherwise turn
    one approved request into a credentialed request to an address no gate ever
    approved. The asset URLs are worse: they arrive in the GitLab API response,
    so a compromised release feed can point straight at an internal target.

    Every hop is resolved, checked against the shared outbound gate and fetched
    with auto-follow still disabled. Credentials are dropped when the origin
    changes and the chain is bounded. Legitimate redirects to other public
    hosts still work; unsafe ones fail closed.
    """
    current = response
    current_url = url
    current_headers = dict(headers or {})
    initial_origin = _origin(url)

    for _ in range(_MAX_SAFE_REDIRECTS):
        if not 300 <= current.status_code < 400:
            return current
        location = current.headers.get("Location")
        if not location:
            return current

        next_url = urljoin(current_url, str(location))
        try:
            parse_outbound_url(next_url, "static client redirect URL")
        except Exception as exc:
            # Never echo the target (or its resolved IP) into logs; only the
            # fact that the redirect was refused is useful here.
            logger.warning(f"Refusing unsafe static-client redirect: {exc}")
            raise FetchError(
                "Refusing static-client redirect to a forbidden address"
            ) from exc

        if _origin(next_url) != initial_origin:
            # A release token must never be replayed to a different origin --
            # even one that passed the gate.
            current_headers = {
                name: value
                for name, value in current_headers.items()
                if name.lower() not in _CREDENTIAL_HEADERS
            }
        close = getattr(current, "close", None)
        if callable(close):
            close()
        current_url = next_url
        current = requests.get(
            current_url,
            headers=current_headers,
            params=params,
            timeout=timeout,
            verify=verify,
            stream=stream,
            allow_redirects=False,
        )

    if 300 <= current.status_code < 400:
        raise FetchError("Too many redirects while fetching a static client release")
    return current


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
        first = requests.get(
            url,
            headers=headers,
            timeout=timeout,
            verify=verify,
            stream=True,
            allow_redirects=False,
        )
        with _follow_safe_redirects(
            first,
            url,
            headers=headers,
            timeout=timeout,
            verify=verify,
            stream=True,
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
) -> Tuple[int, ...]:
    """Return the release tuple used to find matching client releases."""
    return desired.release


def _release_version_tag(major: int, minor: int, patch: int = 0) -> str:
    """Build a regex that matches PEP 440 releases for a given major.minor.patch."""
    return rf"^{major}\.{minor}\.{patch}(?:[abrc]\d+)?$"


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
                    allow_redirects=False,
                )
                resp = _follow_safe_redirects(
                    resp,
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
        self, major: int, minor: int, patch: int = 0
    ) -> List[Tuple[Version, ReleaseAsset]]:
        """Return all matching (version, asset) pairs sorted newest-first.

        The list is sorted by :func:`compare_version_objects` in descending
        order so callers can iterate newest-to-oldest. The full list is
        returned (not just the newest) so the manager can fall back to an
        older release when the newest one fails the ``min_age`` check.
        """
        pattern = re.compile(_release_version_tag(major, minor, patch))
        results: List[Tuple[Version, ReleaseAsset]] = []
        for release in self.list_releases():
            tag = release.get("tag_name") if isinstance(release, dict) else None
            if not isinstance(tag, str) or not pattern.match(tag):
                continue
            try:
                ver = parse_version(tag)
            except (TypeError, ValueError) as tag_error:
                # Defensive against malformed release tags; the log names the
                # tag so a bad release can be fixed upstream.
                logger.debug(
                    f"skipping release with unparsable tag {tag!r}: {tag_error}"
                )
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
                pair[0].release,
                pair[0].pre or (),
                pair[0].post if pair[0].post is not None else -1,
                pair[0].dev if pair[0].dev is not None else -1,
            ),
            reverse=True,
        )
        return results

    def find_release_for_version(
        self, major: int, minor: int, patch: int = 0
    ) -> Optional[ReleaseAsset]:
        """Find the release with the highest build for (major, minor, patch).

        Returns ``None`` if no matching release exists. This is a thin
        wrapper around :meth:`list_release_assets_for_version` kept for
        back-compat with existing callers.
        """
        matches = self.list_release_assets_for_version(major, minor, patch)
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

        expected = ""
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
            sha_text = sha_path.read_text(encoding="utf-8").strip()
            parts = sha_text.split()
            if not parts:
                raise FetchError(
                    f"Empty SHA256 checksum file for release {asset.tag_name}"
                )
            expected = parts[0].lower()
            # Fail closed on a malformed checksum. The previous code accepted
            # any token from the file, so a truncated/placeholder value could
            # turn verification into a no-op against a crafted archive.
            if not _SHA256_HEX_RE.fullmatch(expected):
                raise FetchError(
                    f"Malformed SHA256 checksum for release {asset.tag_name}"
                )
            logger.info(
                f"static_client: expected sha256={expected[:16]}… ({asset.tag_name})"
            )
        elif getattr(git_lab, "require_sha256", True):
            # The zip becomes the web app served to every user. Installing it
            # with no checksum asset means a tampered/compromised release is
            # accepted silently. Refuse by default; the explicit opt-out is
            # git_lab.require_sha256 = false for legacy release pipelines.
            raise FetchError(
                f"Release {asset.tag_name} has no SHA256 checksum asset; "
                "refusing to install unverified client code"
            )
        else:
            logger.warning(
                "static_client: release %s has no sha256 file and require_sha256 "
                "is disabled; installing WITHOUT integrity verification",
                asset.tag_name,
            )

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

        if getattr(git_lab, "require_sha256", True) and not expected:
            raise FetchError(
                f"Release {asset.tag_name} has no SHA256 checksum to verify"
            )
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


def install_zip_at(
    zip_path: Path, target: Path, max_uncompressed_bytes: Optional[int] = None
) -> None:
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
        _safe_extract_zip(zf, target, max_uncompressed_bytes=max_uncompressed_bytes)


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
        except (TypeError, ValueError):  # skip non-version directories
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
    """Delete installed versions, retaining at most ``max_total``.

    Versions in *keep* (the active version) are never deleted. Beyond those,
    the most recent installed versions are retained so older releases remain
    available for rollback, up to the ``max_total`` cap. Returns the removed
    version strings.
    """
    keep_set = set(keep)
    installed = list_installed_versions(install_dir)
    # Newest-first; string sort matches ``list_installed_versions`` ordering.
    by_newest = sorted(installed, key=lambda iv: iv.version, reverse=True)

    retained: List[str] = list(keep_set)
    for iv in by_newest:
        if len(retained) >= max_total:
            break
        if iv.version not in retained:
            retained.append(iv.version)

    removed: List[str] = []
    for iv in installed:
        if iv.version in retained:
            continue
        try:
            shutil.rmtree(iv.install_path)
            removed.append(iv.version)
        except OSError as exc:
            logger.warning(f"static_client: failed to prune {iv.version}: {exc}")
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
