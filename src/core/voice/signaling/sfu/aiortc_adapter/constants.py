"""Constants and resource bounds for the aiortc SFU adapter."""

from __future__ import annotations

# SDP/ICE payload bounds (bytes/chars). Generous for real WebRTC, blocks abuse.
MAX_SDP_BYTES = 256 * 1024
MAX_ICE_CANDIDATE_BYTES = 4096
MAX_ICE_UFRAG_LEN = 256
MAX_ICE_PWD_LEN = 256

# Identifier bounds.
MAX_ID_LEN = 128
MAX_ROOM_ID_LEN = 128
MAX_OUTPUT_DIR_LEN = 4096

# Resource bounds.
DEFAULT_MAX_INCOMING_TRACKS = 4
MAX_PEERS_PER_ROOM = 512
MAX_PRODUCERS_PER_PEER = 64
MAX_CONSUMERS_PER_PEER = 512
MAX_TRANSPORTS_PER_PEER = 32
MAX_TRACKS_PER_KIND = 16
MAX_TRACK_KIND_LEN = 16
ICE_GATHER_TIMEOUT_SEC = 5.0

# Control-payload bounds.
MAX_PAYLOAD_KEYS = 256
MAX_PAYLOAD_DEPTH = 8
MAX_PAYLOAD_STRING = 64 * 1024
MAX_DTLS_FINGERPRINTS = 16

# Media/quality constants.
DEGRADED_VIDEO_FPS = 5.0
_CLOCK_RATES = {"audio": 48000, "video": 90000}
_DEFAULT_CLOCK_RATE = 48000

# Allowed identifier characters (UUIDs, snowflakes, slugs).
_ID_RE_SRC = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
