"""Extended targeted tests for the Version utility."""

import pytest

from utils.version import (
    Version,
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
    compare_version_objects,
)


# ---------------------------------------------------------------------------
# parse_version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "release", "pre", "post"),
    [
        ("1.0.0a1", (1, 0, 0), ("a", 1), None),
        ("2.3.0b15", (2, 3, 0), ("b", 15), None),
        ("5.9.0rc100", (5, 9, 0), ("rc", 100), None),
        ("10.20.0", (10, 20, 0), None, None),
        ("1.0.0", (1, 0, 0), None, None),
        ("1.0.0.post9999", (1, 0, 0), None, 9999),
        ("  1.2.0b3  ", (1, 2, 0), ("b", 3), None),
        ("999.0.0a999", (999, 0, 0), ("a", 999), None),
    ],
)
def test_parse_valid(raw, release, pre, post):
    v = parse_version(raw)
    assert v.release == release
    assert v.pre == pre
    assert v.post == post


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "1.0-1",
        "x.1.0-1",
        "0.0.0a1",  # major must be >= 1
        "a.1.-1-1",
        "a.1.0",
        "a.1.0-",
        "1.0.0a0",  # pre build must be >= 1
        "1.0.0a1\n",
        "1.0.0a1\r",
        "1.0.0a1\x00",
        "1.0.0a1 extra",
        "a1.0.0b1",
        "a.1.0.5-1",
        "-.1.0-1",
        "a.-1.0-1",
        "a.1.0--1",
        "v1.0.0",
    ],
)
def test_parse_invalid(raw):
    with pytest.raises(InvalidVersionError):
        parse_version(raw)


def test_parse_rejects_newline_even_after_strip_attempt():
    with pytest.raises(InvalidVersionError):
        parse_version("1.0.0a1\n")


# ---------------------------------------------------------------------------
# Version dataclass validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("release", "pre", "post", "dev"),
    [
        ((0, 0, 0), None, None, None),  # major must be >= 1
        ((0, 5, 0), None, None, None),
        ((1, -1, 0), None, None, None),
        ((1, 0, 0), ("a", 0), None, None),  # pre must be >= 1
        ((1, 0, 0), ("a", -3), None, None),
        ((1, 0, 0), None, 0, None),  # post must be >= 1
        ((1, 0, 0), None, -3, None),
    ],
)
def test_version_post_init_invalid(release, pre, post, dev):
    with pytest.raises(InvalidVersionError):
        Version(release=release, pre=pre, post=post, dev=dev)


@pytest.mark.parametrize(
    ("release", "pre", "post", "dev"),
    [
        ((1, 0, 1), None, None, None),
        ((2, 7, 0), None, None, None),
        ((100, 100, 0), None, None, None),
    ],
)
def test_version_post_init_valid(release, pre, post, dev):
    v = Version(release=release, pre=pre, post=post, dev=dev)
    assert v.release == release
    assert v.pre == pre
    assert v.post == post


def test_version_immutable():
    import dataclasses

    v = Version(release=(1, 2, 0))
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.release = (9, 0, 0)  # frozen dataclass


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("v1", "v2", "expected"),
    [
        ("1.0.0a1", "1.0.0a1", 0),
        ("1.0.0b1", "1.0.0a1", 1),
        ("1.0.0a1", "1.0.0b1", -1),
        ("1.0.0", "9.9.0a999", 1),  # release dominates pre-release
        ("1.0.0", "9.9.0rc9", 1),
        ("1.0.0a1", "2.0.0a1", -1),
        ("2.0.0a1", "1.0.0a1", 1),
        ("2.5.0a1", "2.6.0a1", -1),
        ("2.6.0a1", "2.5.0a1", 1),
        ("2.5.0a1", "2.5.0a2", -1),
        ("2.5.0a2", "2.5.0a1", 1),
        ("1.999.0a1", "2.0.0a1", -1),
        ("1.0.0", "1.0.0", 0),
    ],
)
def test_compare_versions(v1, v2, expected):
    assert compare_versions(v1, v2) == expected


def test_compare_version_objects_same_object():
    v = parse_version("1.0.0a1")
    assert compare_version_objects(v, v) == 0


def test_compare_returns_minus_one_zero_one_only():
    # The contract is exactly {-1, 0, 1}.
    for a, b in [
        ("1.0.0a1", "9.9.0"),
        ("9.9.0", "1.0.0a1"),
        ("2.3.0b4", "2.3.0b4"),
    ]:
        assert compare_versions(a, b) in (-1, 0, 1)


# ---------------------------------------------------------------------------
# format / round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["1.0.0a1", "2.3.0b15", "5.9.0rc100", "10.20.0"])
def test_format_round_trip(raw):
    assert format_version(parse_version(raw)) == raw.lower()


# ---------------------------------------------------------------------------
# is_compatible / is_same_release_line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("client", "minimum", "expected"),
    [
        ("1.0.0", "1.0.0", True),
        ("1.0.0.post5", "1.0.0", True),
        ("1.0.1", "1.0.0", True),
        ("2.0.0", "1.0.0", True),
        ("1.0.0", "1.0.0.post5", False),
        ("1.0.0", "1.0.1", False),
        ("1.0.0", "2.0.0", False),
        ("2.0.0a1", "1.0.0b1", False),
        ("1.0.0b1", "9.9.0a9", True),
    ],
)
def test_is_compatible(client, minimum, expected):
    assert is_compatible(client, minimum) is expected


@pytest.mark.parametrize(
    ("v1", "v2", "expected"),
    [
        ("1.0.0a1", "1.99.0a5", True),
        ("1.0.0a1", "1.0.0b1", False),
        ("2.0.0", "2.9.0", True),
        ("2.0.0", "3.0.0", False),
        ("1.0.0", "1.0.0rc2", True),
    ],
)
def test_is_same_release_line(v1, v2, expected):
    assert is_same_release_line(v1, v2) is expected


# ---------------------------------------------------------------------------
# increments / stage changes
# ---------------------------------------------------------------------------


def test_increment_build():
    v = increment_build(parse_version("1.0.0a1"))
    assert v.pre == ("a", 2)


def test_increment_minor_resets_build():
    v = increment_minor(parse_version("1.4.0a99"))
    assert v.release == (1, 5, 0)
    assert v.pre == ("a", 1)


def test_increment_major_resets_minor_and_build():
    v = increment_major(parse_version("2.7.0a42"))
    assert v.release == (3, 0, 0)
    assert v.pre == ("a", 1)


@pytest.mark.parametrize("new_stage", ["a", "b", "rc", "release"])
def test_change_stage_resets_build(new_stage):
    v = change_stage(parse_version("2.7.0a42"), new_stage)
    assert v.pre == (new_stage, 1) if new_stage != "release" else None
    assert v.release == (2, 7, 0)


def test_increment_chain_is_monotonic():
    v = parse_version("1.0.0a1")
    for _ in range(5):
        n = increment_build(v)
        assert compare_version_objects(n, v) > 0
        v = n
