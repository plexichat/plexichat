"""Security primitives shared by the API, core and media layers.

Import from here rather than the individual modules so the public surface is
one list: text policies, the outbound URL gate, capability decisions and the
rejection taxonomy.
"""

from .decisions import Capability, Decision, Policy, Principal, Resource
from .errors import (
    OutboundUrlRejected,
    SecurityRejection,
    TextRejected,
    TextTooLong,
    UploadRejected,
)
from .hardening import (
    HardeningViolation,
    bounded_int,
    content_disposition,
    is_identifier,
    reject_control_characters,
    require_identifier,
    safe_join,
    sanitize_header_filename,
)
from .kdf import (
    KDFParams,
    derive_key,
    pbkdf2_raw,
    verify_derived_key,
)
from .outbound import OutboundUrl, parse_outbound_url, resolve_outbound_url
from .secrets import (
    SecretBuffer,
    constant_time_compare,
    generate_token_key,
    scrub_secret,
    sign_token,
    verify_token,
)
from .policies import (
    HTML_META_CHARS,
    TEXT_POLICIES,
    Composite,
    MaxLength,
    RejectUICharacters,
    TextPolicy,
    check_text,
    is_control_character,
    text_policy,
)

__all__ = [
    "Capability",
    "Composite",
    "Decision",
    "HTML_META_CHARS",
    "HardeningViolation",
    "KDFParams",
    "MaxLength",
    "OutboundUrl",
    "OutboundUrlRejected",
    "Policy",
    "Principal",
    "RejectUICharacters",
    "Resource",
    "SecretBuffer",
    "SecurityRejection",
    "TEXT_POLICIES",
    "TextPolicy",
    "TextRejected",
    "TextTooLong",
    "UploadRejected",
    "bounded_int",
    "check_text",
    "constant_time_compare",
    "content_disposition",
    "derive_key",
    "generate_token_key",
    "is_control_character",
    "is_identifier",
    "parse_outbound_url",
    "pbkdf2_raw",
    "reject_control_characters",
    "require_identifier",
    "resolve_outbound_url",
    "safe_join",
    "sanitize_header_filename",
    "scrub_secret",
    "sign_token",
    "text_policy",
    "verify_derived_key",
    "verify_token",
]
