"""
Webhook manager constants.
"""

WEBHOOK_NAME_MAX_LENGTH = 80
USERNAME_OVERRIDE_MAX_LENGTH = 80
MAX_EMBEDS_PER_MESSAGE = 10
TOKEN_BYTES = 48
SIGNATURE_HEADER = "X-Plexichat-Signature"
SIGNATURE_TIMESTAMP_HEADER = "X-Plexichat-Timestamp"
SIGNATURE_VERSION = "v1"

# Bound the signature/timestamp header values accepted from an untrusted
# caller before any decoding happens. An Ed25519 signature is 64 bytes (88
# base64 chars) and a millisecond timestamp is ~13 digits, so these leave
# generous headroom while preventing multi-megabyte headers from reaching the
# verifier.
MAX_SIGNATURE_HEADER_LENGTH = 512
MAX_TIMESTAMP_HEADER_LENGTH = 32
