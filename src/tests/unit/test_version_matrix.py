"""Dense matrix tests for version parsing, comparison, and mutation.

Every row in these tables pins a concrete, documented behavior of the
Version utility (release -> pre -> post -> dev precedence, strict
format validation, release-line and compatibility semantics).
"""

import pytest

from utils.version import (
    Version,
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
        "1.0.0a1",
        "1.0.0b1",
        "1.0.0rc1",
        "1.0.0",
        "9.27.0a123",
        "2.0.0.post9999",
        "3.4.0rc5",
        "10.11.0b12",
    ],
)
def test_parse_format_round_trip(text):
    assert format_version(parse_version(text)) == text


@pytest.mark.parametrize(
    ("text", "release", "pre"),
    [
        ("1.0.0a1", (1, 0, 0), ("a", 1)),
        ("2.3.0b4", (2, 3, 0), ("b", 4)),
        ("5.6.0rc7", (5, 6, 0), ("rc", 7)),
        ("8.9.0", (8, 9, 0), None),
    ],
)
def test_parse_fields(text, release, pre):
    v = parse_version(text)
    assert v.release == release
    assert v.pre == pre


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
        "1.0.0-1",
        "r.1.0",
        "r.1-0-1",
        "r.1.0.1",
        "1.0.0.post1-2",
        "x.1.0-1",
        "r.x.0-1",
        "r.1.x-1",
        "r.1.0-x",
        "1.0.0\n",
        "1.0.0\r",
        "1.0.0\x00",
        "1.0.0a1\n2.0.0.post3",
        "0.0.0",  # major must be >= 1
        "1.0.0.post0",  # post must be >= 1
        "r.1.-1-1",  # negative minor
        "-1.1.0-1",
    ],
)
def test_invalid_versions_rejected(text):
    with pytest.raises(InvalidVersionError):
        parse_version(text)


@pytest.mark.parametrize(
    ("release", "pre", "post", "dev"),
    [
        ((0, 0, 0), None, None, None),
        ((-1, 0, 0), None, None, None),
        ((1, -1, 0), None, None, None),
        ((1, 0, 0), ("a", 0), None, None),
        ((1, 0, 0), ("a", -3), None, None),
    ],
)
def test_version_post_init_rejects_bad_fields(release, pre, post, dev):
    with pytest.raises(InvalidVersionError):
        Version(release=release, pre=pre, post=post, dev=dev)


@pytest.mark.parametrize(
    ("release", "pre", "post", "dev"),
    [
        ((1, 0, 1), None, None, None),
        ((2, 15, 0), None, None, None),
        ((100, 0, 0), None, None, None),
    ],
)
def test_version_post_init_accepts_valid_fields(release, pre, post, dev):
    v = Version(release=release, pre=pre, post=post, dev=dev)
    assert v.release == release


# ---------------------------------------------------------------------------
# Comparison precedence: release > pre > post > dev > local
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        # Equality
        ("1.0.0", "1.0.0", 0),
        ("2.3.0b4", "2.3.0b4", 0),
        ("9.9.0a9", "9.9.0a9", 0),
        # Release dominates everything else
        ("1.0.0", "999.999.0a999", 1),
        ("999.999.0a999", "1.0.0", -1),
        ("1.0.0rc1", "1.0.0b1", 1),
        ("1.0.0b1", "1.0.0rc1", -1),
        ("1.0.0b1", "1.0.0a1", 1),
        # Major
        ("2.0.0", "1.999.0.post999", 1),
        ("1.0.0", "2.0.0", -1),
        # Minor
        ("1.0.1", "1.4.0.post999", 1),
        ("1.0.0.post1", "1.0.0", 1),
        ("1.0.0", "1.0.0.post1", -1),
        ("1.0.0.post10", "1.0.0.post9", 1),
    ],
)
def test_compare_versions_matrix(a, b, expected):
    assert compare_versions(a, b) == expected


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("1.0.0", "1.0.0"),
        ("1.0.0a1", "1.0.0a1"),
    ],
)
def test_compare_objects_equality(a, b):
    assert compare_version_objects(parse_version(a), parse_version(b)) == 0


def test_compare_objects_matches_string_compare():
    pairs = [("1.0.0", "1.0.0.post1"), ("1.0.0a1", "1.0.0b1"), ("2.0.0", "1.0.0")]
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
        ("1.0.0", "1.0.0", True),
        ("1.0.0.post1", "1.0.0", True),
        ("2.0.0", "1.9.0.post3", True),
        ("1.0.0", "2.0.0", False),
        ("1.0.0b1", "1.0.0", False),  # prerelease never satisfies a release minimum
        ("1.0.0", "1.0.0b1", True),
        ("1.0.0a1", "1.0.0a1", True),
        ("1.0.0b1", "1.0.0b2", False),
    ],
)
def test_is_compatible_matrix(client, minimum, expected):
    assert is_compatible(client, minimum) is expected


# ---------------------------------------------------------------------------
# is_same_release_line (same release tuple)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("1.0.0", "1.99.0.post5", True),
        ("1.0.0", "1.0.0", True),
        ("2.0.0b1", "2.7.0b1", True),
        ("1.0.0", "2.0.0", False),
        ("1.0.0", "1.0.0b1", False),
        ("1.0.0a1", "1.0.0rc1", False),
    ],
)
def test_is_same_release_line_matrix(a, b, expected):
    assert is_same_release_line(a, b) is expected


# ---------------------------------------------------------------------------
# Increment helpers
# ---------------------------------------------------------------------------


def test_increment_build():
    v = Version(release=(1, 0, 0), pre=("b", 1))
    nv = increment_build(v)
    assert nv == Version(release=(1, 0, 0), pre=("b", 2))
    assert v.pre == ("b", 1)  # immutability preserved


def test_increment_build_chained():
    v = Version(release=(2, 3, 0))
    assert increment_build(increment_build(v)).post == 2


def test_increment_minor_resets_build():
    v = Version(release=(2, 3, 0))
    nv = increment_minor(v)
    assert nv == Version(release=(2, 4, 0), pre=("a", 1))


def test_increment_major_resets_minor_and_build():
    v = Version(release=(2, 5, 0))
    nv = increment_major(v)
    assert nv == Version(release=(3, 0, 0), pre=("a", 1))


def test_change_stage_resets_build():
    v = Version(release=(2, 3, 0), pre=("b", 7))
    nv = change_stage(v, "release")
    assert nv.pre is None
    assert nv.release == (2, 3, 0)


@pytest.mark.parametrize(
    "stage",
    ["a", "b", "rc", "release"],
)
def test_change_stage_round_trips_each_stage(stage):
    v = Version(release=(1, 0, 0), pre=("a", 1))
    assert change_stage(v, stage).pre == ((stage, 1) if stage != "release" else None)
