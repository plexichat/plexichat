"""
Unit tests for the version utility with comprehensive coverage including security tests.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from utils.version import (
    Version,
    VersionStage,
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
    STAGE_ORDER,
    VERSION_PATTERN,
)


class TestVersionStage:
    """Tests for VersionStage enum."""

    def test_stage_values(self):
        assert VersionStage.ALPHA.value == "a"
        assert VersionStage.BETA.value == "b"
        assert VersionStage.CANDIDATE.value == "c"
        assert VersionStage.RELEASE.value == "r"

    def test_stage_enum_comparison(self):
        assert VersionStage.ALPHA == VersionStage.ALPHA
        assert VersionStage.ALPHA != VersionStage.BETA

    def test_stage_order_mapping(self):
        assert STAGE_ORDER[VersionStage.ALPHA] == 0
        assert STAGE_ORDER[VersionStage.BETA] == 1
        assert STAGE_ORDER[VersionStage.CANDIDATE] == 2
        assert STAGE_ORDER[VersionStage.RELEASE] == 3


class TestVersionParsing:
    """Tests for version string parsing."""

    def test_parse_alpha_version(self):
        ver = parse_version("a.1.0-1")
        assert ver.stage == VersionStage.ALPHA
        assert ver.major == 1
        assert ver.minor == 0
        assert ver.build == 1

    def test_parse_beta_version(self):
        ver = parse_version("b.2.3-15")
        assert ver.stage == VersionStage.BETA
        assert ver.major == 2
        assert ver.minor == 3
        assert ver.build == 15

    def test_parse_candidate_version(self):
        ver = parse_version("c.1.5-42")
        assert ver.stage == VersionStage.CANDIDATE
        assert ver.major == 1
        assert ver.minor == 5
        assert ver.build == 42

    def test_parse_release_version(self):
        ver = parse_version("r.3.0-1")
        assert ver.stage == VersionStage.RELEASE
        assert ver.major == 3
        assert ver.minor == 0
        assert ver.build == 1

    def test_parse_uppercase_normalized(self):
        ver = parse_version("A.1.0-1")
        assert ver.stage == VersionStage.ALPHA

    def test_parse_with_whitespace(self):
        ver = parse_version("  a.1.0-1  ")
        assert ver.stage == VersionStage.ALPHA
        assert ver.major == 1

    def test_parse_large_numbers(self):
        ver = parse_version("r.999.999-9999")
        assert ver.major == 999
        assert ver.minor == 999
        assert ver.build == 9999

    def test_parse_mixed_case(self):
        ver = parse_version("B.1.0-1")
        assert ver.stage == VersionStage.BETA

    def test_parse_with_tabs(self):
        ver = parse_version("\ta.1.0-1\t")
        assert ver.stage == VersionStage.ALPHA


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
            parse_version("a.0.0-1")

    def test_build_zero(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1.0-0")

    def test_negative_major(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.-1.0-1")

    def test_negative_minor(self):
        with pytest.raises(InvalidVersionError, match="Invalid version format"):
            parse_version("a.1.-1-1")

    def test_negative_build(self):
        with pytest.raises(InvalidVersionError, match="Invalid version format"):
            parse_version("a.1.0--1")

    def test_semver_format_rejected(self):
        with pytest.raises(InvalidVersionError):
            parse_version("1.0.0")

    def test_random_string(self):
        with pytest.raises(InvalidVersionError):
            parse_version("not-a-version")

    def test_partial_version(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1")

    def test_extra_components(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1.0-1-extra")

    def test_float_components(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1.5.3-1")

    def test_alpha_in_numbers(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1a.0-1")

    def test_special_chars(self):
        with pytest.raises(InvalidVersionError):
            parse_version("a.1.0-1!")


class TestVersionFormatting:
    """Tests for version formatting."""

    def test_format_alpha(self):
        ver = Version(VersionStage.ALPHA, 1, 0, 1)
        assert format_version(ver) == "a.1.0-1"

    def test_format_release(self):
        ver = Version(VersionStage.RELEASE, 2, 5, 100)
        assert format_version(ver) == "r.2.5-100"

    def test_format_beta(self):
        ver = Version(VersionStage.BETA, 3, 7, 42)
        assert format_version(ver) == "b.3.7-42"

    def test_format_candidate(self):
        ver = Version(VersionStage.CANDIDATE, 4, 2, 8)
        assert format_version(ver) == "c.4.2-8"

    def test_roundtrip(self):
        original = "b.3.7-42"
        ver = parse_version(original)
        assert format_version(ver) == original

    def test_roundtrip_all_stages(self):
        versions = ["a.1.0-1", "b.2.3-5", "c.3.4-10", "r.5.0-1"]
        for version_str in versions:
            ver = parse_version(version_str)
            assert format_version(ver) == version_str


class TestVersionComparison:
    """Tests for version comparison."""

    def test_equal_versions(self):
        assert compare_versions("a.1.0-1", "a.1.0-1") == 0

    def test_stage_comparison_alpha_beta(self):
        assert compare_versions("a.1.0-1", "b.1.0-1") == -1
        assert compare_versions("b.1.0-1", "a.1.0-1") == 1

    def test_stage_comparison_beta_candidate(self):
        assert compare_versions("b.1.0-1", "c.1.0-1") == -1

    def test_stage_comparison_candidate_release(self):
        assert compare_versions("c.1.0-1", "r.1.0-1") == -1

    def test_stage_comparison_alpha_release(self):
        assert compare_versions("a.5.0-1", "r.1.0-1") == -1

    def test_major_comparison(self):
        assert compare_versions("r.1.0-1", "r.2.0-1") == -1
        assert compare_versions("r.2.0-1", "r.1.0-1") == 1

    def test_minor_comparison(self):
        assert compare_versions("r.1.0-1", "r.1.1-1") == -1
        assert compare_versions("r.1.5-1", "r.1.3-1") == 1

    def test_build_comparison(self):
        assert compare_versions("r.1.0-1", "r.1.0-2") == -1
        assert compare_versions("r.1.0-100", "r.1.0-50") == 1

    def test_complex_comparison(self):
        assert compare_versions("r.1.0-1", "a.99.99-999") == 1

    def test_comparison_objects(self):
        v1 = Version(VersionStage.ALPHA, 1, 0, 1)
        v2 = Version(VersionStage.BETA, 1, 0, 1)
        assert compare_version_objects(v1, v2) == -1

    def test_all_fields_equal(self):
        v1 = Version(VersionStage.RELEASE, 1, 2, 3)
        v2 = Version(VersionStage.RELEASE, 1, 2, 3)
        assert compare_version_objects(v1, v2) == 0


class TestVersionCompatibility:
    """Tests for version compatibility checking."""

    def test_exact_match_compatible(self):
        assert is_compatible("a.1.0-1", "a.1.0-1") is True

    def test_newer_client_compatible(self):
        assert is_compatible("a.1.0-5", "a.1.0-1") is True

    def test_older_client_incompatible(self):
        assert is_compatible("a.1.0-1", "a.1.0-5") is False

    def test_higher_stage_compatible(self):
        assert is_compatible("r.1.0-1", "a.1.0-1") is True

    def test_lower_stage_incompatible(self):
        assert is_compatible("a.1.0-1", "r.1.0-1") is False

    def test_higher_major_compatible(self):
        assert is_compatible("a.2.0-1", "a.1.0-1") is True

    def test_higher_minor_compatible(self):
        assert is_compatible("a.1.5-1", "a.1.0-1") is True

    def test_complex_compatibility(self):
        assert is_compatible("b.3.5-10", "a.2.0-1") is True
        assert is_compatible("a.2.0-1", "b.3.5-10") is False


class TestSameReleaseLine:
    """Tests for release line checking."""

    def test_same_line(self):
        assert is_same_release_line("r.1.0-1", "r.1.5-10") is True

    def test_different_major(self):
        assert is_same_release_line("r.1.0-1", "r.2.0-1") is False

    def test_different_stage(self):
        assert is_same_release_line("a.1.0-1", "r.1.0-1") is False

    def test_same_stage_and_major_different_minor(self):
        assert is_same_release_line("b.2.0-1", "b.2.5-10") is True

    def test_same_stage_and_major_different_build(self):
        assert is_same_release_line("c.3.4-1", "c.3.4-100") is True

    def test_all_different(self):
        assert is_same_release_line("a.1.0-1", "r.2.5-10") is False


class TestVersionIncrement:
    """Tests for version increment functions."""

    def test_increment_build(self):
        ver = parse_version("a.1.0-1")
        new_ver = increment_build(ver)
        assert format_version(new_ver) == "a.1.0-2"

    def test_increment_build_multiple(self):
        ver = parse_version("a.1.0-1")
        new_ver = increment_build(ver)
        new_ver = increment_build(new_ver)
        new_ver = increment_build(new_ver)
        assert format_version(new_ver) == "a.1.0-4"

    def test_increment_minor_resets_build(self):
        ver = parse_version("a.1.0-50")
        new_ver = increment_minor(ver)
        assert format_version(new_ver) == "a.1.1-1"

    def test_increment_minor_from_zero(self):
        ver = parse_version("r.5.0-10")
        new_ver = increment_minor(ver)
        assert format_version(new_ver) == "r.5.1-1"

    def test_increment_major_resets_minor_and_build(self):
        ver = parse_version("a.1.5-50")
        new_ver = increment_major(ver)
        assert format_version(new_ver) == "a.2.0-1"

    def test_increment_major_preserves_stage(self):
        ver = parse_version("b.3.7-100")
        new_ver = increment_major(ver)
        assert format_version(new_ver) == "b.4.0-1"

    def test_change_stage_resets_build(self):
        ver = parse_version("a.1.5-50")
        new_ver = change_stage(ver, VersionStage.BETA)
        assert format_version(new_ver) == "b.1.5-1"

    def test_change_stage_preserves_version_numbers(self):
        ver = parse_version("b.3.7-100")
        new_ver = change_stage(ver, VersionStage.RELEASE)
        assert new_ver.major == 3
        assert new_ver.minor == 7
        assert new_ver.build == 1

    def test_change_stage_all_stages(self):
        ver = parse_version("a.1.0-1")

        beta = change_stage(ver, VersionStage.BETA)
        assert format_version(beta) == "b.1.0-1"

        candidate = change_stage(beta, VersionStage.CANDIDATE)
        assert format_version(candidate) == "c.1.0-1"

        release = change_stage(candidate, VersionStage.RELEASE)
        assert format_version(release) == "r.1.0-1"


class TestVersionDataclass:
    """Tests for Version dataclass constraints."""

    def test_version_immutable(self):
        ver = Version(VersionStage.ALPHA, 1, 0, 1)
        with pytest.raises(AttributeError):
            ver.major = 2  # type: ignore

    def test_version_equality(self):
        v1 = Version(VersionStage.ALPHA, 1, 0, 1)
        v2 = Version(VersionStage.ALPHA, 1, 0, 1)
        assert v1 == v2

    def test_version_inequality(self):
        v1 = Version(VersionStage.ALPHA, 1, 0, 1)
        v2 = Version(VersionStage.ALPHA, 1, 0, 2)
        assert v1 != v2

    def test_version_hash(self):
        v1 = Version(VersionStage.ALPHA, 1, 0, 1)
        v2 = Version(VersionStage.ALPHA, 1, 0, 1)
        assert hash(v1) == hash(v2)

        versions = {v1}
        assert v2 in versions

    def test_version_in_dict(self):
        ver = Version(VersionStage.ALPHA, 1, 0, 1)
        version_dict = {ver: "test_data"}
        assert version_dict[ver] == "test_data"

    def test_version_frozen(self):
        ver = Version(VersionStage.ALPHA, 1, 0, 1)
        with pytest.raises(AttributeError):
            ver.stage = VersionStage.BETA  # type: ignore


class TestVersionConstraints:
    """Tests for version constraint validation."""

    def test_major_minimum_one(self):
        with pytest.raises(InvalidVersionError, match="Major version must be >= 1"):
            Version(VersionStage.ALPHA, 0, 0, 1)

    def test_minor_minimum_zero(self):
        ver = Version(VersionStage.ALPHA, 1, 0, 1)
        assert ver.minor == 0

    def test_minor_negative_fails(self):
        with pytest.raises(InvalidVersionError, match="Minor version must be >= 0"):
            Version(VersionStage.ALPHA, 1, -1, 1)

    def test_build_minimum_one(self):
        with pytest.raises(InvalidVersionError, match="Build number must be >= 1"):
            Version(VersionStage.ALPHA, 1, 0, 0)

    def test_build_negative_fails(self):
        with pytest.raises(InvalidVersionError, match="Build number must be >= 1"):
            Version(VersionStage.ALPHA, 1, 0, -1)

    def test_major_negative_fails(self):
        with pytest.raises(InvalidVersionError, match="Major version must be >= 1"):
            Version(VersionStage.ALPHA, -1, 0, 1)


class TestVersionPattern:
    """Tests for VERSION_PATTERN regex."""

    def test_pattern_matches_valid(self):
        valid_versions = ["a.1.0-1", "b.2.3-15", "c.99.99-999", "r.1.0-1"]
        for version in valid_versions:
            assert VERSION_PATTERN.match(version.lower()) is not None

    def test_pattern_rejects_invalid(self):
        invalid_versions = ["1.0.0", "v1.0.0", "a.1.0", "a-1-0-1", "x.1.0-1"]
        for version in invalid_versions:
            assert VERSION_PATTERN.match(version.lower()) is None

    def test_pattern_captures_groups(self):
        match = VERSION_PATTERN.match("a.1.2-3")
        assert match is not None
        assert match.groups() == ("a", "1", "2", "3")


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
            parse_version("a.1.0-1; rm -rf /")

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
            parse_version("a.1.0-1\r\n")

    def test_very_large_numbers(self):
        ver = parse_version("r.999999999.999999999-999999999")
        assert ver.major == 999999999
        assert ver.minor == 999999999
        assert ver.build == 999999999

    def test_leading_zeros_in_numbers(self):
        ver = parse_version("a.01.00-01")
        assert ver.major == 1
        assert ver.minor == 0
        assert ver.build == 1

    def test_whitespace_injection(self):
        ver = parse_version("  a.1.0-1  ")
        assert format_version(ver) == "a.1.0-1"

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
        ver = Version(VersionStage.ALPHA, 1, 0, 1)
        assert compare_version_objects(ver, ver) == 0

    def test_compare_all_stages_ordered(self):
        alpha = Version(VersionStage.ALPHA, 1, 0, 1)
        beta = Version(VersionStage.BETA, 1, 0, 1)
        candidate = Version(VersionStage.CANDIDATE, 1, 0, 1)
        release = Version(VersionStage.RELEASE, 1, 0, 1)

        assert compare_version_objects(alpha, beta) == -1
        assert compare_version_objects(beta, candidate) == -1
        assert compare_version_objects(candidate, release) == -1
        assert compare_version_objects(alpha, release) == -1

    def test_stage_dominates_version_numbers(self):
        low_release = Version(VersionStage.RELEASE, 1, 0, 1)
        high_alpha = Version(VersionStage.ALPHA, 999, 999, 999)

        assert compare_version_objects(low_release, high_alpha) == 1

    def test_major_dominates_minor_and_build(self):
        v1 = Version(VersionStage.RELEASE, 2, 0, 1)
        v2 = Version(VersionStage.RELEASE, 1, 999, 999)

        assert compare_version_objects(v1, v2) == 1

    def test_minor_dominates_build(self):
        v1 = Version(VersionStage.RELEASE, 1, 1, 1)
        v2 = Version(VersionStage.RELEASE, 1, 0, 999)

        assert compare_version_objects(v1, v2) == 1


class TestComplexScenarios:
    """Tests for complex real-world scenarios."""

    def test_version_upgrade_path(self):
        versions = [
            "a.1.0-1",
            "a.1.0-2",
            "a.1.1-1",
            "b.1.1-1",
            "b.1.1-2",
            "c.1.1-1",
            "r.1.0-1",
            "r.1.1-1",
            "r.2.0-1",
        ]

        for i in range(len(versions) - 1):
            assert compare_versions(versions[i], versions[i + 1]) == -1

    def test_version_sorting(self):
        unsorted = ["r.2.0-1", "a.1.0-1", "b.1.5-3", "r.1.0-1", "c.1.2-5"]

        expected_order = ["a.1.0-1", "b.1.5-3", "c.1.2-5", "r.1.0-1", "r.2.0-1"]

        sorted_versions = sorted(
            unsorted,
            key=lambda v: (
                STAGE_ORDER[parse_version(v).stage],
                parse_version(v).major,
                parse_version(v).minor,
                parse_version(v).build,
            ),
        )

        assert sorted_versions == expected_order

    def test_compatibility_chain(self):
        client = "r.2.5-10"
        requirements = ["a.1.0-1", "b.1.5-3", "r.1.0-1", "r.2.0-1"]

        for req in requirements:
            assert is_compatible(client, req) is True

    def test_incompatibility_detection(self):
        client = "a.1.0-1"
        requirements = ["b.1.0-1", "r.1.0-1", "a.2.0-1", "a.1.1-1"]

        for req in requirements:
            assert is_compatible(client, req) is False

    def test_release_line_migration(self):
        v1 = parse_version("a.1.0-1")

        v2 = increment_build(v1)
        assert is_same_release_line(format_version(v1), format_version(v2))

        v3 = increment_minor(v2)
        assert is_same_release_line(format_version(v1), format_version(v3))

        v4 = increment_major(v3)
        assert not is_same_release_line(format_version(v1), format_version(v4))

        v5 = change_stage(v4, VersionStage.BETA)
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
        with pytest.raises(InvalidVersionError, match=r"\[a\|b\|c\|r\]"):
            parse_version("1.0.0")

    def test_major_constraint_error_message(self):
        with pytest.raises(InvalidVersionError, match="Major version must be >= 1"):
            Version(VersionStage.ALPHA, 0, 0, 1)

    def test_minor_constraint_error_message(self):
        with pytest.raises(InvalidVersionError, match="Minor version must be >= 0"):
            Version(VersionStage.ALPHA, 1, -1, 1)

    def test_build_constraint_error_message(self):
        with pytest.raises(InvalidVersionError, match="Build number must be >= 1"):
            Version(VersionStage.ALPHA, 1, 0, 0)
