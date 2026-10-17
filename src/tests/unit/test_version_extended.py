"""Extended targeted tests for the Version utility."""

import pytest

from utils.version import (
    Version,
    VersionStage,
    InvalidVersionError,
    parse_version,
    format_version,
    compare_versions,
    is_compatible,
    is_same_release_line,
    increment_build,
    increment_minor,
    increment_major,
    change_stage,
)
from utils.version.core import compare_version_objects, STAGE_ORDER


# ---------------------------------------------------------------------------
# parse_version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "stage", "major", "minor", "build"),
    [
        ("a.1.0-1", VersionStage.ALPHA, 1, 0, 1),
        ("b.2.3-15", VersionStage.BETA, 2, 3, 15),
        ("c.5.9-100", VersionStage.CANDIDATE, 5, 9, 100),
        ("r.10.20-300", VersionStage.RELEASE, 10, 20, 300),
        ("A.1.0-1", VersionStage.ALPHA, 1, 0, 1),  # uppercase lowered
        ("R.3.4-5", VersionStage.RELEASE, 3, 4, 5),
        ("  b.1.2-3  ", VersionStage.BETA, 1, 2, 3),  # stripped
        ("a.1.0-1", VersionStage.ALPHA, 1, 0, 1),
        ("b.1.0-1", VersionStage.BETA, 1, 0, 1),
        ("r.1.999-1", VersionStage.RELEASE, 1, 999, 1),
        ("a.999.0-999", VersionStage.ALPHA, 999, 0, 999),
    ],
)
def test_parse_valid(raw, stage, major, minor, build):
    v = parse_version(raw)
    assert v.stage is stage
    assert v.major == major
    assert v.minor == minor
    assert v.build == build


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "1.0-1",
        "x.1.0-1",
        "a.0.0-1",  # major must be >= 1
        "a.1.-1-1",
        "a.1.0",
        "a.1.0-",
        "a.1.0-0",  # build must be >= 1
        "a.1.0-1\n",
        "a.1.0-1\r",
        "a.1.0-1\x00",
        "a.1.0-1 extra",
        "ab.1.0-1",
        "a.1.0.5-1",
        "-.1.0-1",
        "a.-1.0-1",
        "a.1.0--1",
    ],
)
def test_parse_invalid(raw):
    with pytest.raises(InvalidVersionError):
        parse_version(raw)


def test_parse_rejects_newline_even_after_strip_attempt():
    with pytest.raises(InvalidVersionError):
        parse_version("a.1.0-1\n")


# ---------------------------------------------------------------------------
# Version dataclass validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("major", "minor", "build"),
    [
        (0, 0, 1),
        (0, 5, 1),
        (1, -1, 1),
        (1, 0, 0),
        (1, 0, -3),
    ],
)
def test_version_post_init_invalid(major, minor, build):
    with pytest.raises(InvalidVersionError):
        Version(VersionStage.ALPHA, major, minor, build)


@pytest.mark.parametrize(
    ("major", "minor", "build"),
    [(1, 0, 1), (2, 7, 42), (100, 100, 100)],
)
def test_version_post_init_valid(major, minor, build):
    v = Version(VersionStage.BETA, major, minor, build)
    assert v.major == major
    assert v.minor == minor
    assert v.build == build


def test_version_immutable():
    import dataclasses

    v = Version(VersionStage.RELEASE, 1, 2, 3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.major = 9  # frozen dataclass


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("v1", "v2", "expected"),
    [
        ("a.1.0-1", "a.1.0-1", 0),
        ("b.1.0-1", "a.1.0-1", 1),
        ("a.1.0-1", "b.1.0-1", -1),
        ("c.1.0-1", "b.9.9-999", 1),  # stage dominates numbers
        ("r.1.0-1", "c.9.9-9", 1),
        ("a.1.0-1", "a.2.0-1", -1),
        ("a.2.0-1", "a.1.0-1", 1),
        ("a.2.5-1", "a.2.6-1", -1),
        ("a.2.6-1", "a.2.5-1", 1),
        ("a.2.5-1", "a.2.5-2", -1),
        ("a.2.5-2", "a.2.5-1", 1),
        ("a.1.999-1", "a.2.0-1", -1),  # minor dominates build
        ("r.1.0-1", "r.1.0-1", 0),
    ],
)
def test_compare_versions(v1, v2, expected):
    assert compare_versions(v1, v2) == expected


def test_compare_version_objects_same_object():
    v = parse_version("a.1.0-1")
    assert compare_version_objects(v, v) == 0


def test_stage_order_mapping():
    assert STAGE_ORDER[VersionStage.ALPHA] == 0
    assert STAGE_ORDER[VersionStage.BETA] == 1
    assert STAGE_ORDER[VersionStage.CANDIDATE] == 2
    assert STAGE_ORDER[VersionStage.RELEASE] == 3


def test_compare_returns_minus_one_zero_one_only():
    # The contract is exactly {-1, 0, 1}.
    for a, b in [
        ("a.1.0-1", "r.9.9-99"),
        ("r.9.9-99", "a.1.0-1"),
        ("b.2.3-4", "b.2.3-4"),
    ]:
        assert compare_versions(a, b) in (-1, 0, 1)


# ---------------------------------------------------------------------------
# format / round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["a.1.0-1", "b.2.3-15", "c.5.9-100", "r.10.20-300"])
def test_format_round_trip(raw):
    assert format_version(parse_version(raw)) == raw.lower()


# ---------------------------------------------------------------------------
# is_compatible / is_same_release_line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("client", "minimum", "expected"),
    [
        ("r.1.0-1", "r.1.0-1", True),
        ("r.1.0-5", "r.1.0-1", True),
        ("r.1.1-1", "r.1.0-1", True),
        ("r.2.0-1", "r.1.0-1", True),
        ("r.1.0-1", "r.1.0-5", False),
        ("r.1.0-1", "r.1.1-1", False),
        ("r.1.0-1", "r.2.0-1", False),
        ("a.2.0-1", "b.1.0-1", False),  # stage order matters
        ("b.1.0-1", "a.9.9-9", True),
    ],
)
def test_is_compatible(client, minimum, expected):
    assert is_compatible(client, minimum) is expected


@pytest.mark.parametrize(
    ("v1", "v2", "expected"),
    [
        ("a.1.0-1", "a.1.99-5", True),
        ("a.1.0-1", "b.1.0-1", False),
        ("r.2.0-1", "r.2.9-1", True),
        ("r.2.0-1", "r.3.0-1", False),
        ("c.1.0-1", "c.1.0-2", True),
    ],
)
def test_is_same_release_line(v1, v2, expected):
    assert is_same_release_line(v1, v2) is expected


# ---------------------------------------------------------------------------
# increments / stage changes
# ---------------------------------------------------------------------------


def test_increment_build():
    v = increment_build(parse_version("a.1.0-1"))
    assert (v.stage, v.major, v.minor, v.build) == (VersionStage.ALPHA, 1, 0, 2)


def test_increment_minor_resets_build():
    v = increment_minor(parse_version("a.1.4-99"))
    assert (v.stage, v.major, v.minor, v.build) == (VersionStage.ALPHA, 1, 5, 1)


def test_increment_major_resets_minor_and_build():
    v = increment_major(parse_version("a.2.7-42"))
    assert (v.stage, v.major, v.minor, v.build) == (VersionStage.ALPHA, 3, 0, 1)


@pytest.mark.parametrize("new_stage", list(VersionStage))
def test_change_stage_resets_build(new_stage):
    v = change_stage(parse_version("a.2.7-42"), new_stage)
    assert v.stage is new_stage
    assert v.build == 1
    assert v.major == 2
    assert v.minor == 7


def test_increment_chain_is_monotonic():
    v = parse_version("a.1.0-1")
    for _ in range(5):
        n = increment_build(v)
        assert compare_version_objects(n, v) > 0
        v = n
