"""
Typed dataclasses describing avatar/icon metadata.

These are plain data carriers; they decouple the route layer from the shape of
the dicts returned by :mod:`src.core.avatars.manager` and make the upload
response mapping explicit and typed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AvatarMetadata:
    """Describes a stored avatar or server icon."""

    width: int
    height: int
    size: int
    content_type: str
    animated: bool
    checksum: str
    url: str


@dataclass(frozen=True)
class AvatarUploadResult:
    """Normalised result of a successful avatar/icon upload."""

    success: bool
    url: str
    width: int
    height: int
    size: int
    animated: bool


def metadata_from_manager(result: dict) -> AvatarMetadata:
    """Build :class:`AvatarMetadata` from a manager result dict."""

    return AvatarMetadata(
        width=int(result["width"]),
        height=int(result["height"]),
        size=int(result["size"]),
        content_type=str(result.get("content_type", "application/octet-stream")),
        animated=bool(result.get("animated", False)),
        checksum=str(result.get("checksum", "")),
        url=str(result["url"]),
    )


def upload_result_from_manager(result: dict) -> AvatarUploadResult:
    """Build :class:`AvatarUploadResult` from a manager result dict."""

    return AvatarUploadResult(
        success=True,
        url=str(result["url"]),
        width=int(result["width"]),
        height=int(result["height"]),
        size=int(result["size"]),
        animated=bool(result.get("animated", False)),
    )


__all__ = [
    "AvatarMetadata",
    "AvatarUploadResult",
    "metadata_from_manager",
    "upload_result_from_manager",
]
