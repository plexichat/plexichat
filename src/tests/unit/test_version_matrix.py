"""Dense matrix tests for version parsing, comparison, and mutation.

Every row in these tables pins a concrete, documented behavior of the
Version utility (stage -> major -> minor -> build precedence, strict
format validation, release-line and compatibility semantics).
"""

import pytest

from utils.version import (
    Version,
    VersionStage,
    InvalidVersionError,
    parse_version,
    format_version,
    compare_versions,
    compare_version_objects,
    is_compatible,
    is_same_release_line,
    increment_build,
    increment_minor,
    increment_major,
    change_stage,
)


# ---------------------------------------------------------------------------
# parse_version / format_version round trips
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "a.1.0-1",
        "b.1.0-1",
        "c.1.0-1",
        "r.1.0-1",
        "a.9.27-123",
        "r.2.0-9999",
        "c.3.4-5",
        "b.10.11-12",
    ],
)
def test_parse_format_round_trip(text):
    assert format_version(parse_version(text)) == text


@pytest.mark.parametrize(
    ("text", "stage", "major", "minor", "build"),
    [
        ("a.1.0-1", VersionStage.ALPHA, 1, 0, 1),
        ("b.2.3-4", VersionStage.BETA, 2, 3, 4),
        ("c.5.6-7", VersionStage.CANDIDATE, 5, 6, 7),
        ("r.8.9-10", VersionStage.RELEASE, 8, 9, 10),
    ],
)
def test_parse_fields(text, stage, major, minor, build):
    v = parse_version(text)
    assert v.stage is stage
    assert v.major == major
    assert v.minor == minor
    assert v.build == build


@pytest.mark.parametrize(
    "text",
    [
        " A.1.0-1  ",  # whitespace + case are tolerated
        "R.2.0-3",
        "B.1.1-1",
    ],
)
def test_parse_normalizes_case_and_space(text):
    assert format_version(parse_version(text)) == text.strip().lower()


# ---------------------------------------------------------------------------
# Invalid version strings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        None,
        "1.0.0",
        "1.0.0-1",
        "r.1.0",
        "r.1-0-1",
        "r.1.0.1",
        "r.1.0-1-2",
        "x.1.0-1",
        "r.x.0-1",
        "r.1.x-1",
        "r.1.0-x",
        "r.1.0-1\n",
        "r.1.0-1\r",
        "r.1.0-1\x00",
        "a.1.0-1\nr.2.0-3",
        "r.0.0-1",  # major must be >= 1
        "r.1.0-0",  # build must be >= 1
        "r.1.-1-1",  # negative minor
        "-1.1.0-1",
    ],
)
def test_invalid_versions_rejected(text):
    with pytest.raises(InvalidVersionError):
        parse_version(text)


@pytest.mark.parametrize(
    ("major", "minor", "build"),
    [
        (0, 0, 1),
        (-1, 0, 1),
        (1, -1, 1),
        (1, 0, 0),
        (1, 0, -3),
    ],
)
def test_version_post_init_rejects_bad_fields(major, minor, build):
    with pytest.raises(InvalidVersionError):
        Version(stage=VersionStage.RELEASE, major=major, minor=minor, build=build)


@pytest.mark.parametrize(
    ("major", "minor", "build"),
    [(1, 0, 1), (2, 15, 100), (100, 0, 1)],
)
def test_version_post_init_accepts_valid_fields(major, minor, build):
    v = Version(stage=VersionStage.BETA, major=major, minor=minor, build=build)
    assert v.major == major


# ---------------------------------------------------------------------------
# Comparison precedence: stage > major > minor > build
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        # Equality
        ("r.1.0-1", "r.1.0-1", 0),
        ("b.2.3-4", "b.2.3-4", 0),
        ("a.9.9-9", "a.9.9-9", 0),
        # Stage beats everything else
        ("r.1.0-1", "a.999.999-999", 1),
        ("a.999.999-999", "r.1.0-1", -1),
        ("c.1.0-1", "b.1.0-1", 1),
        ("b.1.0-1", "c.1.0-1", -1),
        ("b.1.0-1", "a.1.0-1", 1),
        # Major
        ("r.2.0-1", "r.1.999-999", 1),
        ("r.1.0-1", "r.2.0-1", -1),
        # Minor
        ("r.1.5-1", "r.1.4-999", 1),
        ("r.1.3-1", "r.1.4-1", -1),
        # Build
        ("r.1.0-2", "r.1.0-1", 1),
        ("r.1.0-1", "r.1.0-2", -1),
        ("r.1.0-10", "r.1.0-9", 1),
    ],
)
def test_compare_versions_matrix(a, b, expected):
    assert compare_versions(a, b) == expected


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("r.1.0-1", "r.1.0-1"),
        ("a.1.0-1", "a.1.0-1"),
    ],
)
def test_compare_objects_equality(a, b):
    assert compare_version_objects(parse_version(a), parse_version(b)) == 0


def test_compare_objects_matches_string_compare():
    pairs = [("r.1.0-1", "r.1.0-2"), ("a.1.0-1", "b.1.0-1"), ("r.2.0-1", "r.1.0-1")]
    for a, b in pairs:
        assert compare_version_objects(
            parse_version(a), parse_version(b)
        ) == compare_versions(a, b)


# ---------------------------------------------------------------------------
# is_compatible (client >= minimum)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("client", "minimum", "expected"),
    [
        ("r.1.0-1", "r.1.0-1", True),
        ("r.1.0-2", "r.1.0-1", True),
        ("r.2.0-1", "r.1.9-3", True),
        ("r.1.0-1", "r.2.0-1", False),
        ("b.1.0-1", "r.1.0-1", False),  # prerelease never satisfies a release minimum
        ("r.1.0-1", "b.1.0-1", True),
        ("a.1.0-1", "a.1.0-1", True),
        ("b.1.0-1", "b.1.0-2", False),
    ],
)
def test_is_compatible_matrix(client, minimum, expected):
    assert is_compatible(client, minimum) is expected


# ---------------------------------------------------------------------------
# is_same_release_line (same stage + same major)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("r.1.0-1", "r.1.99-5", True),
        ("r.1.0-1", "r.1.0-1", True),
        ("b.2.0-1", "b.2.7-1", True),
        ("r.1.0-1", "r.2.0-1", False),
        ("r.1.0-1", "b.1.0-1", False),
        ("a.1.0-1", "c.1.0-1", False),
    ],
)
def test_is_same_release_line_matrix(a, b, expected):
    assert is_same_release_line(a, b) is expected


# ---------------------------------------------------------------------------
# Increment helpers
# ---------------------------------------------------------------------------


def test_increment_build():
    v = Version(VersionStage.BETA, 1, 0, 1)
    nv = increment_build(v)
    assert nv == Version(VersionStage.BETA, 1, 0, 2)
    assert v.build == 1  # immutability preserved


def test_increment_build_chained():
    v = Version(VersionStage.RELEASE, 2, 3, 4)
    assert increment_build(increment_build(v)).build == 6


def test_increment_minor_resets_build():
    v = Version(VersionStage.RELEASE, 2, 3, 9)
    nv = increment_minor(v)
    assert nv == Version(VersionStage.RELEASE, 2, 4, 1)


def test_increment_major_resets_minor_and_build():
    v = Version(VersionStage.RELEASE, 2, 5, 9)
    nv = increment_major(v)
    assert nv == Version(VersionStage.RELEASE, 3, 0, 1)


def test_change_stage_resets_build():
    v = Version(VersionStage.BETA, 2, 3, 7)
    nv = change_stage(v, VersionStage.RELEASE)
    assert nv.stage is VersionStage.RELEASE
    assert nv.build == 1
    assert nv.minor == 3


@pytest.mark.parametrize(
    "stage",
    list(VersionStage),
)
def test_change_stage_round_trips_each_stage(stage):
    v = Version(VersionStage.ALPHA, 1, 0, 1)
    assert change_stage(v, stage).stage is stage
