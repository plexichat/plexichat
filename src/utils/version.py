"""version utility - proxy to common_utils so ``utils.version`` resolves in the app."""

from .common_utils.utils.version import (
    Version as Version,
    InvalidVersionError as InvalidVersionError,
    setup as setup,
    current as current,
    current_string as current_string,
    min_supported as min_supported,
    parse as parse,
    compare as compare,
    is_client_compatible as is_client_compatible,
    to_dict as to_dict,
    parse_version as parse_version,
    compare_versions as compare_versions,
    compare_version_objects as compare_version_objects,
    is_compatible as is_compatible,
    format_version as format_version,
    increment_build as increment_build,
    increment_minor as increment_minor,
    increment_major as increment_major,
    change_stage as change_stage,
    is_same_release_line as is_same_release_line,
)
