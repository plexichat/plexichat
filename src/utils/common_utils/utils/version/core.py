"""
Version parsing and comparison core implementation.

Version Format: PEP 440
- epoch: optional epoch segment (e.g. ``1!``)
- release: ``major.minor.patch`` series of non-negative integers
- pre: pre-release tag and number, one of ``a``/``alpha``, ``b``/``beta``, ``rc``/``candidate``
- post: post-release number (``.postN``)
- dev: development release number (``.devN``)
- local: local version segment (``+local``)

Examples: ``1.0.0a212``, ``1.0.0``, ``2.1.0rc3``, ``1.0.0.post1``

Usage:
    from src.utils.common_utils.utils.version.core import parse_version, format_version

    version = parse_version("1.0.0a212")
    assert format_version(version) == "1.0.0a212"
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple


_PRE_TAG_ALIASES = {
    "a": "a",
    "alpha": "a",
    "b": "b",
    "beta": "b",
    "rc": "rc",
    "candidate": "rc",
}

_PRE_RANK = {"a": 0, "b": 1, "rc": 2}


class InvalidVersionError(ValueError):
    """Raised when a version string is invalid."""

    pass


@dataclass(frozen=True)
class Version:
    """Immutable PEP 440 version representation."""

    epoch: int = 0
    release: Tuple[int, ...] = ()
    pre: Optional[Tuple[str, int]] = None
    post: Optional[int] = None
    dev: Optional[int] = None
    local: Optional[str] = None

    def __post_init__(self):
        # Validate release: non-empty tuple of non-negative ints, major >= 1
        if not isinstance(self.release, tuple) or len(self.release) == 0:
            raise InvalidVersionError(
                f"Release must be a non-empty tuple of ints, got {self.release!r}"
            )
        for index, part in enumerate(self.release):
            if not isinstance(part, int) or part < 0:
                raise InvalidVersionError(
                    f"Release part {index} must be a non-negative int, got {part!r}"
                )
        if self.release[0] < 1:
            raise InvalidVersionError(
                f"Major version (first release segment) must be >= 1, got {self.release[0]}"
            )

        # Canonicalize and validate pre-release tag
        if self.pre is not None:
            if not isinstance(self.pre, tuple) or len(self.pre) != 2:
                raise InvalidVersionError(
                    f"Pre must be a (tag, number) tuple, got {self.pre!r}"
                )
            tag, number = self.pre
            if tag not in _PRE_TAG_ALIASES:
                raise InvalidVersionError(
                    f"Pre tag must be one of {sorted(_PRE_TAG_ALIASES)}, got {tag!r}"
                )
            if not isinstance(number, int) or number < 1:
                raise InvalidVersionError(f"Pre number must be >= 1, got {number!r}")
            object.__setattr__(self, "pre", (_PRE_TAG_ALIASES[tag], number))

        if self.post is not None and (not isinstance(self.post, int) or self.post < 1):
            raise InvalidVersionError(f"Post must be a positive int, got {self.post!r}")
        if self.dev is not None and (not isinstance(self.dev, int) or self.dev < 0):
            raise InvalidVersionError(
                f"Dev must be a non-negative int, got {self.dev!r}"
            )


# Full PEP 440 pattern: [epoch!]release[pre][.postN][.devN][+local]
VERSION_PATTERN = re.compile(
    r"^(?:(?P<epoch>\d+)!)?"
    r"(?P<release>\d+(?:\.\d+)*)"
    r"(?:(?P<pre_tag>a|b|rc|alpha|beta|candidate)(?P<pre_num>\d+))?"
    r"(?:\.post(?P<post>\d+))?"
    r"(?:\.dev(?P<dev>\d+))?"
    r"(?:\+(?P<local>.+))?$"
)


def parse_version(version_string: Optional[str]) -> Version:
    """
    Parse a PEP 440 version string into a Version object.

    Args:
        version_string: Version string (e.g., "1.0.0a212")

    Returns:
        Version object

    Raises:
        InvalidVersionError: If version string format is invalid
    """
    if not version_string:
        raise InvalidVersionError("Version string cannot be empty")

    # Check for newlines or null bytes before stripping
    if any(c in version_string for c in "\r\n\x00"):
        raise InvalidVersionError(
            "Version string cannot contain newlines or null bytes"
        )

    version_string = version_string.strip().lower()

    match = VERSION_PATTERN.match(version_string)
    if not match:
        raise InvalidVersionError(
            f"Invalid version format: '{version_string}'. "
            f"Expected PEP 440 format (e.g., '1.0.0a212', '1.0.0', '2.1.0rc3')"
        )

    epoch_str = match.group("epoch")
    release_str = match.group("release")
    pre_tag = match.group("pre_tag")
    pre_num = match.group("pre_num")
    post_str = match.group("post")
    dev_str = match.group("dev")
    local_str = match.group("local")

    epoch = int(epoch_str) if epoch_str is not None else 0
    release = tuple(int(part) for part in release_str.split("."))

    pre = None
    if pre_tag is not None:
        pre = (_PRE_TAG_ALIASES[pre_tag], int(pre_num))

    post = int(post_str) if post_str is not None else None
    dev = int(dev_str) if dev_str is not None else None
    local = local_str

    return Version(
        epoch=epoch,
        release=release,
        pre=pre,
        post=post,
        dev=dev,
        local=local,
    )


def format_version(version: Version) -> str:
    """
    Format a Version object as a PEP 440 string.

    Args:
        version: Version object

    Returns:
        PEP 440 version string (e.g., "1.0.0a212", "1.0.0", "2.1.0rc3")
    """
    parts: list[str] = []

    if version.epoch:
        parts.append(f"{version.epoch}!")

    parts.append(".".join(str(part) for part in version.release))

    if version.pre is not None:
        tag, number = version.pre
        parts.append(f"{tag}{number}")

    if version.post is not None:
        parts.append(f".post{version.post}")

    if version.dev is not None:
        parts.append(f".dev{version.dev}")

    if version.local is not None:
        parts.append(f"+{version.local}")

    return "".join(parts)


def compare_versions(version1: str, version2: str) -> int:
    """
    Compare two PEP 440 version strings.

    Comparison follows PEP 440 ordering: epoch, release, pre-release,
    post-release, dev-release, then local segment.

    Args:
        version1: First version string
        version2: Second version string

    Returns:
        -1 if version1 < version2
         0 if version1 == version2
         1 if version1 > version2
    """
    v1 = parse_version(version1)
    v2 = parse_version(version2)

    return compare_version_objects(v1, v2)


def compare_version_objects(v1: Version, v2: Version) -> int:
    """
    Compare two Version objects.

    Args:
        v1: First Version object
        v2: Second Version object

    Returns:
        -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2
    """
    # Epoch
    if v1.epoch != v2.epoch:
        return 1 if v1.epoch > v2.epoch else -1

    # Release (compare segment by segment, missing segments treated as 0)
    release_len = max(len(v1.release), len(v2.release))
    for index in range(release_len):
        part1 = v1.release[index] if index < len(v1.release) else 0
        part2 = v2.release[index] if index < len(v2.release) else 0
        if part1 != part2:
            return 1 if part1 > part2 else -1

    # Pre-release: absent (release) > any pre-release
    pre_cmp = _compare_pre(v1.pre, v2.pre)
    if pre_cmp != 0:
        return pre_cmp

    # Post-release: .postN > bare release
    post_cmp = _compare_optional_int(v1.post, v2.post, absent_is_lesser=True)
    if post_cmp != 0:
        return post_cmp

    # Dev-release: .devN < bare release
    dev_cmp = _compare_optional_int(v1.dev, v2.dev, absent_is_lesser=False)
    if dev_cmp != 0:
        return dev_cmp

    # Local segment
    return _compare_local(v1.local, v2.local)


def _compare_pre(
    pre1: Optional[Tuple[str, int]], pre2: Optional[Tuple[str, int]]
) -> int:
    if pre1 is None and pre2 is None:
        return 0
    if pre1 is None:
        return 1  # bare release is greater than a pre-release
    if pre2 is None:
        return -1  # a pre-release is less than a bare release
    tag1, num1 = pre1
    tag2, num2 = pre2
    rank1 = _PRE_RANK[tag1]
    rank2 = _PRE_RANK[tag2]
    if rank1 != rank2:
        return 1 if rank1 > rank2 else -1
    if num1 != num2:
        return 1 if num1 > num2 else -1
    return 0


def _compare_optional_int(
    value1: Optional[int], value2: Optional[int], absent_is_lesser: bool
) -> int:
    if value1 is None and value2 is None:
        return 0
    if value1 is None:
        return -1 if absent_is_lesser else 1
    if value2 is None:
        return 1 if absent_is_lesser else -1
    if value1 != value2:
        return 1 if value1 > value2 else -1
    return 0


def _compare_local(local1: Optional[str], local2: Optional[str]) -> int:
    if local1 is None and local2 is None:
        return 0
    if local1 is None:
        return -1  # a local segment makes a version greater
    if local2 is None:
        return 1
    segments1 = local1.split(".")
    segments2 = local2.split(".")
    for segment1, segment2 in zip(segments1, segments2):
        if segment1.isdigit() and segment2.isdigit():
            int1, int2 = int(segment1), int(segment2)
            if int1 != int2:
                return 1 if int1 > int2 else -1
        else:
            if segment1 != segment2:
                return 1 if segment1 > segment2 else -1
    if len(segments1) != len(segments2):
        return 1 if len(segments1) > len(segments2) else -1
    return 0


def is_compatible(client_version: str, min_version: str) -> bool:
    """
    Check if a client version meets the minimum version requirement.

    Args:
        client_version: Client's version string
        min_version: Minimum required version string

    Returns:
        True if client_version >= min_version
    """
    return compare_versions(client_version, min_version) >= 0


def is_same_release_line(version1: str, version2: str) -> bool:
    """
    Check if two versions are on the same release line (same epoch, major,
    and pre-release presence).

    Args:
        version1: First version string
        version2: Second version string

    Returns:
        True if same epoch, same major, and both have (or both lack) a
        pre-release segment
    """
    v1 = parse_version(version1)
    v2 = parse_version(version2)

    return (
        v1.epoch == v2.epoch
        and v1.release[0] == v2.release[0]
        and (v1.pre is None) == (v2.pre is None)
    )


def increment_build(version: Version) -> Version:
    """
    Create a new version with the build (pre-release counter) incremented.

    If the version has a pre-release segment, its number is incremented
    (e.g. ``1.0.0a210`` → ``1.0.0a212``). If the version is a plain release,
    a ``.post1`` segment is added.

    Args:
        version: Current version

    Returns:
        New Version with the build counter incremented
    """
    if version.pre is not None:
        tag, number = version.pre
        return Version(
            epoch=version.epoch,
            release=version.release,
            pre=(tag, number + 1),
            post=None,
            dev=None,
            local=version.local,
        )
    post = (version.post or 0) + 1
    return Version(
        epoch=version.epoch,
        release=version.release,
        pre=None,
        post=post,
        dev=None,
        local=version.local,
    )


def increment_minor(version: Version) -> Version:
    """
    Create a new version with the minor release number incremented.

    The release tuple becomes ``(major, minor + 1, 0)``. If the version had a
    pre-release segment, its tag is preserved with counter reset to 1 (e.g.
    ``1.0.0a50`` → ``1.1.0a1``). If it was a plain release, no pre segment is
    added (e.g. ``5.0.0`` → ``5.1.0``). Any existing post/dev segments are
    replaced.

    Args:
        version: Current version

    Returns:
        New Version with minor + 1
    """
    release = list(version.release)
    major = release[0] if release else 0
    minor = release[1] if len(release) > 1 else 0
    new_release = (major, minor + 1, 0)
    if version.pre is not None:
        new_pre = (version.pre[0], 1)
    else:
        new_pre = None
    return Version(
        epoch=version.epoch,
        release=new_release,
        pre=new_pre,
        post=None,
        dev=None,
        local=version.local,
    )


def increment_major(version: Version) -> Version:
    """
    Create a new version with the major release number incremented.

    The release tuple becomes ``(major + 1, 0, 0)``. Any existing pre-release
    tag type is preserved with its counter reset to 1 (e.g. ``3.7.0b100`` →
    ``4.0.0b1``). Any existing post/dev segments are replaced.

    Args:
        version: Current version

    Returns:
        New Version with major + 1, minor = 0, patch = 0
    """
    major = version.release[0] if version.release else 0
    new_release = (major + 1, 0, 0)
    if version.pre is not None:
        new_pre = (version.pre[0], 1)
    else:
        new_pre = ("a", 1)
    return Version(
        epoch=version.epoch,
        release=new_release,
        pre=new_pre,
        post=None,
        dev=None,
        local=version.local,
    )


def change_stage(version: Version, new_stage: Optional[str]) -> Version:
    """
    Create a new version with a different pre-release stage.

    ``new_stage`` is one of ``"a"``, ``"b"``, ``"rc"`` (or their aliases
    ``"alpha"``, ``"beta"``, ``"candidate"``). The pre-release counter is
    reset to 1. If ``new_stage`` is ``None`` or ``"release"``, the
    pre-release segment is dropped (producing a plain release).

    Args:
        version: Current version
        new_stage: New pre-release stage string, or None/"release" to drop it

    Returns:
        New Version with updated pre-release stage and counter reset to 1
    """
    if new_stage is None or new_stage == "release":
        pre = None
    else:
        tag = str(new_stage).lower()
        if tag not in _PRE_TAG_ALIASES:
            raise InvalidVersionError(
                f"Invalid stage '{new_stage}'. Expected one of "
                f"{sorted(_PRE_TAG_ALIASES)} or None/'release'"
            )
        pre = (_PRE_TAG_ALIASES[tag], 1)

    return Version(
        epoch=version.epoch,
        release=version.release,
        pre=pre,
        post=None,
        dev=None,
        local=version.local,
    )
