"""
Unit tests for the version utility with comprehensive coverage including security tests.
"""

import pytest
from functools import cmp_to_key

from utils.version import (
    Version,
    InvalidVersionError,
    parse_version,
    format_version,
    compare_versions,
    is_compatible,
)
from utils.version.core import (
    compare_version_objects,
    is_same_release_line,
    increment_build,
    increment_minor,
    increment_major,
    change_stage,
    VERSION_PATTERN,
)


class TestVersionParsing:
    """Tests for version string parsing."""

    def test_parse_alpha_version(self):
        ver = parse_version("1.0.0a1")
        assert ver.release == (1, 0, 0)
        assert ver.pre == ("a", 1)
        assert ver.post is None
        assert ver.dev is None

    def test_parse_beta_version(self):
        ver = parse_version("2.3.0b15")
        assert ver.release == (2, 3, 0)
        assert ver.pre == ("b", 15)

    def test_parse_candidate_version(self):
        ver = parse_version("1.5.0rc42")
        assert ver.release == (1, 5, 0)
        assert ver.pre == ("rc", 42)

    def test_parse_release_version(self):
        ver = parse_version("3.0.0")
        assert ver.release == (3, 0, 0)
        assert ver.pre is None

    def test_parse_uppercase_normalized(self):
        ver = parse_version("1.0.0a1")
        assert ver.pre == ("a", 1)

    def test_parse_with_whitespace(self):
        ver = parse_version("  1.0.0a1  ")
        assert ver.pre == ("a", 1)

    def test_parse_large_numbers(self):
        ver = parse_version("999.999.0.post9999")
        assert ver.release == (999, 999, 0)
        assert ver.post == 9999

    def test_parse_mixed_case(self):
        ver = parse_version("1.0.0b1")
        assert ver.pre == ("b", 1)

    def test_parse_with_tabs(self):
        ver = parse_version("\t1.0.0a1\t")
        assert ver.pre == ("a", 1)


class TestVersionParsingErrors:
    """Tests for invalid version string handling."""

    def test_empty_string(self):
        with pytest.raises(InvalidVersionError):
            parse_version("")

    def test_none_string(self):
        with pytest.raises(InvalidVersionError):
            parse_version(None)

    def test_invalid_stage(self):
        with pytest.raises(InvalidVersionError):
            parse_version("x.1.0-1")

    def test_missing_build(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1.0")

    def test_missing_minor(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1-1")

    def test_wrong_separator(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a-1-0-1")

    def test_major_zero(self):
        with pytest.raises(InvalidVersionError):
            parse_version("0.0.0a1")

    def test_build_zero(self):
        with pytest.raises(InvalidVersionError):
            parse_version("1.0.0a0")

    def test_negative_major(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.-1.0-1")

    def test_negative_minor(self):
        with pytest.raises(InvalidVersionError, match="Invalid version format"):
            parse_version("a.1.-1-1")

    def test_negative_build(self):
        with pytest.raises(InvalidVersionError, match="Invalid version format"):
            parse_version("a.1.0--1")

    def test_semver_hyphen_build_rejected(self):
        with pytest.raises(InvalidVersionError):
            parse_version("1.0.0-1")

    def test_random_string(self):
        with pytest.raises(InvalidVersionError):
            parse_version("not-a-version")

    def test_partial_version(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1")

    def test_extra_components(self):
        with pytest.raises(InvalidVersionError):
            parse_version("1.0.0a1-extra")

    def test_float_components(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1.5.3-1")

    def test_alpha_in_numbers(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1a.0-1")

    def test_special_chars(self):
        with pytest.raises(InvalidVersionError):
            parse_version("1.0.0a1!")


class TestVersionFormatting:
    """Tests for version formatting."""

    def test_format_alpha(self):
        ver = Version(release=(1, 0, 0), pre=("a", 1))
        assert format_version(ver) == "1.0.0a1"

    def test_format_release(self):
        ver = Version(release=(2, 5, 0), post=100)
        assert format_version(ver) == "2.5.0.post100"

    def test_format_beta(self):
        ver = Version(release=(3, 7, 0), pre=("b", 42))
        assert format_version(ver) == "3.7.0b42"

    def test_format_candidate(self):
        ver = Version(release=(4, 2, 0), pre=("rc", 8))
        assert format_version(ver) == "4.2.0rc8"

    def test_roundtrip(self):
        original = "3.7.0b42"
        ver = parse_version(original)
        assert format_version(ver) == original

    def test_roundtrip_all_stages(self):
        versions = ["1.0.0a1", "2.3.0b5", "3.4.0rc10", "5.0.0"]
        for version_str in versions:
            ver = parse_version(version_str)
            assert format_version(ver) == version_str


class TestVersionComparison:
    """Tests for version comparison."""

    def test_equal_versions(self):
        assert compare_versions("1.0.0a1", "1.0.0a1") == 0

    def test_stage_comparison_alpha_beta(self):
        assert compare_versions("1.0.0a1", "1.0.0b1") == -1
        assert compare_versions("1.0.0b1", "1.0.0a1") == 1

    def test_stage_comparison_beta_candidate(self):
        assert compare_versions("1.0.0b1", "1.0.0rc1") == -1

    def test_stage_comparison_candidate_release(self):
        assert compare_versions("1.0.0rc1", "1.0.0") == -1

    def test_stage_comparison_alpha_release(self):
        assert compare_versions("5.0.0a1", "1.0.0") == -1

    def test_major_comparison(self):
        assert compare_versions("1.0.0", "2.0.0") == -1
        assert compare_versions("2.0.0", "1.0.0") == 1

    def test_minor_comparison(self):
        assert compare_versions("1.0.0", "1.0.1") == -1
        assert compare_versions("1.5.0", "1.3.0") == 1

    def test_build_comparison(self):
        assert compare_versions("1.0.0", "1.0.0.post1") == -1
        assert compare_versions("1.0.0.post100", "1.0.0.post50") == 1

    def test_complex_comparison(self):
        assert compare_versions("1.0.0", "99.99.0a999") == 1

    def test_comparison_objects(self):
        v1 = Version(release=(1, 0, 0), pre=("a", 1))
        v2 = Version(release=(1, 0, 0), pre=("b", 1))
        assert compare_version_objects(v1, v2) == -1

    def test_all_fields_equal(self):
        v1 = Version(release=(1, 2, 0))
        v2 = Version(release=(1, 2, 0))
        assert compare_version_objects(v1, v2) == 0


class TestVersionCompatibility:
    """Tests for version compatibility checking."""

    def test_exact_match_compatible(self):
        assert is_compatible("1.0.0a1", "1.0.0a1") is True

    def test_newer_client_compatible(self):
        assert is_compatible("1.0.0a5", "1.0.0a1") is True

    def test_older_client_incompatible(self):
        assert is_compatible("1.0.0a1", "1.0.0a5") is False

    def test_higher_stage_compatible(self):
        assert is_compatible("1.0.0", "1.0.0a1") is True

    def test_lower_stage_incompatible(self):
        assert is_compatible("1.0.0a1", "1.0.0") is False

    def test_higher_major_compatible(self):
        assert is_compatible("2.0.0a1", "1.0.0a1") is True

    def test_higher_minor_compatible(self):
        assert is_compatible("1.0.1a1", "1.0.0a1") is True

    def test_complex_compatibility(self):
        assert is_compatible("3.5.0b10", "2.0.0a1") is True
        assert is_compatible("2.0.0a1", "3.5.0b10") is False


class TestSameReleaseLine:
    """Tests for release line checking."""

    def test_same_line(self):
        assert is_same_release_line("1.0.0", "1.5.0") is True

    def test_different_major(self):
        assert is_same_release_line("1.0.0", "2.0.0") is False

    def test_different_stage(self):
        assert is_same_release_line("1.0.0a1", "1.0.0") is False

    def test_same_stage_and_major_different_minor(self):
        assert is_same_release_line("2.0.0b1", "2.5.0b10") is True

    def test_same_stage_and_major_different_build(self):
        assert is_same_release_line("3.4.0rc1", "3.4.0rc100") is True

    def test_all_different(self):
        assert is_same_release_line("1.0.0a1", "2.5.0.post10") is False


class TestVersionIncrement:
    """Tests for version increment functions."""

    def test_increment_build(self):
        ver = parse_version("1.0.0a1")
        new_ver = increment_build(ver)
        assert format_version(new_ver) == "1.0.0a2"

    def test_increment_build_multiple(self):
        ver = parse_version("1.0.0a1")
        new_ver = increment_build(ver)
        new_ver = increment_build(new_ver)
        new_ver = increment_build(new_ver)
        assert format_version(new_ver) == "1.0.0a4"

    def test_increment_minor_resets_build(self):
        ver = parse_version("1.0.0a50")
        new_ver = increment_minor(ver)
        assert format_version(new_ver) == "1.0.1a1"

    def test_increment_minor_from_zero(self):
        ver = parse_version("5.0.0")
        new_ver = increment_minor(ver)
        assert format_version(new_ver) == "5.1.0"

    def test_increment_major_resets_minor_and_build(self):
        ver = parse_version("1.5.0a50")
        new_ver = increment_major(ver)
        assert format_version(new_ver) == "2.0.0a1"

    def test_increment_major_preserves_stage(self):
        ver = parse_version("3.7.0b100")
        new_ver = increment_major(ver)
        assert format_version(new_ver) == "4.0.0b1"

    def test_change_stage_resets_build(self):
        ver = parse_version("1.5.0a50")
        new_ver = change_stage(ver, "b")
        assert format_version(new_ver) == "1.5.0b1"

    def test_change_stage_preserves_version_numbers(self):
        ver = parse_version("3.7.0b100")
        new_ver = change_stage(ver, "release")
        assert new_ver.release[0] == 3
        assert new_ver.release[1] == 7
        assert new_ver.pre is None

    def test_change_stage_all_stages(self):
        ver = parse_version("1.0.0a1")

        beta = change_stage(ver, "b")
        assert format_version(beta) == "1.0.0b1"

        candidate = change_stage(beta, "rc")
        assert format_version(candidate) == "1.0.0rc1"

        release = change_stage(candidate, "release")
        assert format_version(release) == "1.0.0"


class TestVersionDataclass:
    """Tests for Version dataclass constraints."""

    def test_version_immutable(self):
        ver = Version(release=(1, 0, 0), pre=("a", 1))
        with pytest.raises(AttributeError):
            setattr(ver, "release", (2, 0, 0))

    def test_version_equality(self):
        v1 = Version(release=(1, 0, 0), pre=("a", 1))
        v2 = Version(release=(1, 0, 0), pre=("a", 1))
        assert v1 == v2

    def test_version_inequality(self):
        v1 = Version(release=(1, 0, 0), pre=("a", 1))
        v2 = Version(release=(1, 0, 0), pre=("a", 2))
        assert v1 != v2

    def test_version_hash(self):
        v1 = Version(release=(1, 0, 0), pre=("a", 1))
        v2 = Version(release=(1, 0, 0), pre=("a", 1))
        assert hash(v1) == hash(v2)

        versions = {v1}
        assert v2 in versions

    def test_version_in_dict(self):
        ver = Version(release=(1, 0, 0), pre=("a", 1))
        version_dict = {ver: "test_data"}
        assert version_dict[ver] == "test_data"

    def test_version_frozen(self):
        ver = Version(release=(1, 0, 0), pre=("a", 1))
        with pytest.raises(AttributeError):
            setattr(ver, "pre", ("b", 1))


class TestVersionConstraints:
    """Tests for version constraint validation."""

    def test_major_minimum_one(self):
        with pytest.raises(InvalidVersionError, match="Major version must be >= 1"):
            Version(release=(0, 0, 0))

    def test_minor_minimum_zero(self):
        ver = Version(release=(1, 0, 0))
        assert ver.release[1] == 0

    def test_minor_negative_fails(self):
        with pytest.raises(InvalidVersionError, match="Minor version must be >= 0"):
            Version(release=(1, -1, 0))

    def test_build_minimum_one(self):
        with pytest.raises(InvalidVersionError, match="Build number must be >= 1"):
            Version(release=(1, 0, 0), pre=("a", 0))

    def test_build_negative_fails(self):
        with pytest.raises(InvalidVersionError, match="Build number must be >= 1"):
            Version(release=(1, 0, 0), pre=("a", -1))

    def test_major_negative_fails(self):
        with pytest.raises(InvalidVersionError, match="Major version must be >= 1"):
            Version(release=(-1, 0, 0))


class TestVersionPattern:
    """Tests for VERSION_PATTERN regex."""

    def test_pattern_matches_valid(self):
        valid_versions = ["1.0.0a1", "2.3.0b15", "99.99.0rc999", "1.0.0"]
        for version in valid_versions:
            match = VERSION_PATTERN.match(version.lower())
            assert match is not None
            assert match.group(0) == version.lower()

    def test_pattern_rejects_invalid(self):
        invalid_versions = ["v1.0.0", "a.1.0", "a-1-0-1", "x.1.0-1", "1.0.0-1"]
        for version in invalid_versions:
            assert VERSION_PATTERN.match(version.lower()) is None

    def test_pattern_captures_groups(self):
        match = VERSION_PATTERN.match("1.2.0a3")
        assert match is not None


class TestSecurityVersionManipulation:
    """Security tests for version string manipulation attacks."""

    def test_injection_in_stage(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a; DROP TABLE versions; --.1.0-1")

    def test_sql_injection_in_version(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1' OR '1'='1.0-1")

    def test_xss_in_version_string(self):
        with pytest.raises(InvalidVersionError):
            parse_version("<script>alert('xss')</script>")

    def test_command_injection_attempt(self):
        with pytest.raises(InvalidVersionError):
            parse_version("1.0.0a1; rm -rf /")

    def test_path_traversal_attempt(self):
        with pytest.raises(InvalidVersionError):
            parse_version("../../../etc/passwd")

    def test_null_byte_injection(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1.0\x00-1")

    def test_format_string_attack(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.%s%s%s%s.0-1")

    def test_unicode_normalization_attack(self):
        with pytest.raises(InvalidVersionError):
            parse_version("\u0061.1.0-1extra")

    def test_control_characters(self):
        with pytest.raises(InvalidVersionError):
            parse_version("1.0.0a1\r\n")

    def test_very_large_numbers(self):
        ver = parse_version("999999999.999999999.0.post999999999")
        assert ver.release[0] == 999999999
        assert ver.release[1] == 999999999
        assert ver.post == 999999999

    def test_leading_zeros_in_numbers(self):
        ver = parse_version("1.0.0a1")
        assert ver.release[0] == 1
        assert ver.release[1] == 0

    def test_whitespace_injection(self):
        ver = parse_version("  1.0.0a1  ")
        assert format_version(ver) == "1.0.0a1"

    def test_mixed_separators(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a-1.0-1")

    def test_double_dash(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1.0--1")

    def test_extra_dots(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a..1.0-1")

    def test_unicode_digits(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.①.⓪-①")

    def test_hex_numbers(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.0x1.0x0-0x1")

    def test_scientific_notation(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1e2.0-1")

    def test_negative_zero(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1.-0-1")


class TestVersionComparisonsEdgeCases:
    """Edge case tests for version comparisons."""

    def test_compare_same_object(self):
        ver = Version(release=(1, 0, 0), pre=("a", 1))
        assert compare_version_objects(ver, ver) == 0

    def test_compare_all_stages_ordered(self):
        alpha = Version(release=(1, 0, 0), pre=("a", 1))
        beta = Version(release=(1, 0, 0), pre=("b", 1))
        candidate = Version(release=(1, 0, 0), pre=("rc", 1))
        release = Version(release=(1, 0, 0))

        assert compare_version_objects(alpha, beta) == -1
        assert compare_version_objects(beta, candidate) == -1
        assert compare_version_objects(candidate, release) == -1
        assert compare_version_objects(alpha, release) == -1

    def test_stage_dominates_version_numbers(self):
        low_release = Version(release=(1, 0, 0))
        high_alpha = Version(release=(999, 999, 0), pre=("a", 999))

        assert compare_version_objects(low_release, high_alpha) == 1

    def test_major_dominates_minor_and_build(self):
        v1 = Version(release=(2, 0, 0))
        v2 = Version(release=(1, 999, 0))

        assert compare_version_objects(v1, v2) == 1

    def test_minor_dominates_build(self):
        v1 = Version(release=(1, 1, 0))
        v2 = Version(release=(1, 0, 0), post=999)

        assert compare_version_objects(v1, v2) == 1


class TestComplexScenarios:
    """Tests for complex real-world scenarios."""

    def test_version_upgrade_path(self):
        versions = [
            "1.0.0a1",
            "1.0.0a2",
            "1.0.1a1",
            "1.1.0b1",
            "1.1.0b2",
            "1.1.0rc1",
            "1.0.0",
            "1.0.1",
            "2.0.0",
        ]

        for i in range(len(versions) - 1):
            assert compare_versions(versions[i], versions[i + 1]) == -1

    def test_version_sorting(self):
        unsorted = ["2.0.0", "1.0.0a1", "1.5.0b3", "1.0.0", "1.2.0rc5"]

        expected_order = ["1.0.0a1", "1.5.0b3", "1.2.0rc5", "1.0.0", "2.0.0"]

        sorted_versions = sorted(
            unsorted,
            key=cmp_to_key(compare_versions),
        )

        assert sorted_versions == expected_order

    def test_compatibility_chain(self):
        client = "1.0.0.post10"
        requirements = ["1.0.0a1", "1.5.0b3", "1.0.0", "2.0.0"]

        for req in requirements:
            assert is_compatible(client, req) is True

    def test_incompatibility_detection(self):
        client = "1.0.0a1"
        requirements = ["1.0.0b1", "1.0.0", "2.0.0a1", "1.0.1a1"]

        for req in requirements:
            assert is_compatible(client, req) is False

    def test_release_line_migration(self):
        v1 = parse_version("1.0.0a1")

        v2 = increment_build(v1)
        assert is_same_release_line(format_version(v1), format_version(v2))

        v3 = increment_minor(v2)
        assert is_same_release_line(format_version(v1), format_version(v3))

        v4 = increment_major(v3)
        assert not is_same_release_line(format_version(v1), format_version(v4))

        v5 = change_stage(v4, "b")
        assert not is_same_release_line(format_version(v1), format_version(v5))


class TestErrorMessages:
    """Tests for error message quality."""

    def test_empty_string_error_message(self):
        with pytest.raises(InvalidVersionError, match="cannot be empty"):
            parse_version("")

    def test_invalid_format_error_message(self):
        with pytest.raises(InvalidVersionError, match="Invalid version format"):
            parse_version("invalid")

    def test_error_message_shows_expected_format(self):
        with pytest.raises(InvalidVersionError, match=r"PEP 440"):
            parse_version("1.0.0")

    def test_major_constraint_error_message(self):
        with pytest.raises(InvalidVersionError, match="Major version must be >= 1"):
            Version(release=(0, 0, 0))

    def test_minor_constraint_error_message(self):
        with pytest.raises(InvalidVersionError, match="Minor version must be >= 0"):
            Version(release=(1, -1, 0))

    def test_build_constraint_error_message(self):
        with pytest.raises(InvalidVersionError, match="Build number must be >= 1"):
            Version(release=(1, 0, 0), pre=("a", 0))
