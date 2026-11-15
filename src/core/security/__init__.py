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
from .outbound import OutboundUrl, parse_outbound_url, resolve_outbound_url
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
    "MaxLength",
    "OutboundUrl",
    "OutboundUrlRejected",
    "Policy",
    "Principal",
    "RejectUICharacters",
    "Resource",
    "SecurityRejection",
    "TEXT_POLICIES",
    "TextPolicy",
    "TextRejected",
    "TextTooLong",
    "UploadRejected",
    "check_text",
    "is_control_character",
    "parse_outbound_url",
    "resolve_outbound_url",
    "text_policy",
]
