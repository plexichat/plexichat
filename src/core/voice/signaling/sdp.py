"""
SDP handling - Parse, validate, and manipulate SDP messages.

Implements SDP parsing per RFC 4566 and WebRTC extensions.
"""

import re
from typing import Dict, List, Optional, Any

from .models import SDPType, SDPMessage
from .exceptions import SDPParseError, SDPValidationError


# SDP line patterns
SDP_LINE_PATTERN = re.compile(r"^([a-z])=(.*)$")
SDP_VERSION_PATTERN = re.compile(r"^0$")
SDP_ORIGIN_PATTERN = re.compile(r"^(\S+)\s+(\d+)\s+(\d+)\s+(IN)\s+(IP[46])\s+(\S+)$")
SDP_MEDIA_PATTERN = re.compile(
    r"^(audio|video|application)\s+(\d+)(?:/(\d+))?\s+(\S+)(?:\s+(.+))?$"
)
SDP_ATTRIBUTE_PATTERN = re.compile(r"^([^:]+)(?::(.*))?$")
SDP_CANDIDATE_PATTERN = re.compile(
    r"^candidate:(\S+)\s+(\d+)\s+(udp|tcp)\s+(\d+)\s+(\S+)\s+(\d+)\s+typ\s+(host|srflx|prflx|relay)"
)
SDP_FINGERPRINT_PATTERN = re.compile(r"^(sha-256|sha-1)\s+([A-Fa-f0-9:]+)$")
SDP_ICE_UFRAG_PATTERN = re.compile(r"^[a-zA-Z0-9+/]{4,256}$")
SDP_ICE_PWD_PATTERN = re.compile(r"^[a-zA-Z0-9+/]{22,256}$")


class SDPParser:
    """Parser for SDP (Session Description Protocol) messages."""

    def __init__(self):
        self._parsed: Dict[str, Any] = {}
        self._media_sections: List[Dict[str, Any]] = []
        self._current_media: Optional[Dict[str, Any]] = None

    def parse(self, sdp: str) -> Dict[str, Any]:
        """
        Parse an SDP string into a structured dictionary.

        Args:
            sdp: Raw SDP string

        Returns:
            Parsed SDP as dictionary

        Raises:
            SDPParseError: If SDP is malformed
        """
        self._parsed = {
            "version": None,
            "origin": None,
            "session_name": None,
            "timing": None,
            "attributes": {},
            "media": [],
        }
        self._media_sections = []
        self._current_media = None

        lines = sdp.strip().split("\n")

        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue

            match = SDP_LINE_PATTERN.match(line)
            if not match:
                raise SDPParseError(
                    "Invalid SDP line format", line=line_num, detail=line[:50]
                )

            line_type = match.group(1)
            line_value = match.group(2)

            self._parse_line(line_type, line_value, line_num)

        if self._current_media:
            self._media_sections.append(self._current_media)

        self._parsed["media"] = self._media_sections
        return self._parsed

    def _parse_line(self, line_type: str, value: str, line_num: int) -> None:
        """Parse a single SDP line."""
        if line_type == "v":
            self._parse_version(value, line_num)
        elif line_type == "o":
            self._parse_origin(value, line_num)
        elif line_type == "s":
            self._parsed["session_name"] = value
        elif line_type == "t":
            self._parse_timing(value, line_num)
        elif line_type == "m":
            self._parse_media(value, line_num)
        elif line_type == "c":
            self._parse_connection(value, line_num)
        elif line_type == "a":
            self._parse_attribute(value, line_num)
        elif line_type == "b":
            self._parse_bandwidth(value, line_num)

    def _parse_version(self, value: str, line_num: int) -> None:
        """Parse v= line."""
        if not SDP_VERSION_PATTERN.match(value):
            raise SDPParseError(
                "Invalid SDP version", line=line_num, detail=f"Expected 0, got {value}"
            )
        self._parsed["version"] = int(value)

    def _parse_origin(self, value: str, line_num: int) -> None:
        """Parse o= line."""
        match = SDP_ORIGIN_PATTERN.match(value)
        if not match:
            raise SDPParseError("Invalid origin line", line=line_num, detail=value[:50])
        self._parsed["origin"] = {
            "username": match.group(1),
            "session_id": match.group(2),
            "session_version": match.group(3),
            "net_type": match.group(4),
            "addr_type": match.group(5),
            "address": match.group(6),
        }

    def _parse_timing(self, value: str, line_num: int) -> None:
        """Parse t= line."""
        parts = value.split()
        if len(parts) != 2:
            raise SDPParseError("Invalid timing line", line=line_num, detail=value)
        self._parsed["timing"] = {
            "start": int(parts[0]),
            "stop": int(parts[1]),
        }

    def _parse_media(self, value: str, line_num: int) -> None:
        """Parse m= line (starts new media section)."""
        if self._current_media:
            self._media_sections.append(self._current_media)

        match = SDP_MEDIA_PATTERN.match(value)
        if not match:
            raise SDPParseError("Invalid media line", line=line_num, detail=value[:50])

        formats = match.group(5).split() if match.group(5) else []

        self._current_media = {
            "type": match.group(1),
            "port": int(match.group(2)),
            "num_ports": int(match.group(3)) if match.group(3) else 1,
            "protocol": match.group(4),
            "formats": formats,
            "attributes": {},
            "connection": None,
            "bandwidth": None,
        }

    def _parse_connection(self, value: str, line_num: int) -> None:
        """Parse c= line."""
        parts = value.split()
        if len(parts) < 3:
            raise SDPParseError("Invalid connection line", line=line_num, detail=value)

        connection = {
            "net_type": parts[0],
            "addr_type": parts[1],
            "address": parts[2],
        }

        if self._current_media:
            self._current_media["connection"] = connection
        else:
            self._parsed["connection"] = connection

    def _parse_attribute(self, value: str, line_num: int) -> None:
        """Parse a= line."""
        match = SDP_ATTRIBUTE_PATTERN.match(value)
        if not match:
            return

        attr_name = match.group(1)
        attr_value = match.group(2) if match.group(2) else True

        target = (
            self._current_media["attributes"]
            if self._current_media
            else self._parsed["attributes"]
        )

        if attr_name in target:
            if isinstance(target[attr_name], list):
                target[attr_name].append(attr_value)
            else:
                target[attr_name] = [target[attr_name], attr_value]
        else:
            target[attr_name] = attr_value

    def _parse_bandwidth(self, value: str, line_num: int) -> None:
        """Parse b= line."""
        parts = value.split(":")
        if len(parts) != 2:
            return

        bandwidth = {
            "type": parts[0],
            "value": int(parts[1]),
        }

        if self._current_media:
            self._current_media["bandwidth"] = bandwidth
        else:
            self._parsed["bandwidth"] = bandwidth


class SDPValidator:
    """Validator for SDP messages."""

    REQUIRED_SESSION_ATTRS = ["ice-ufrag", "ice-pwd", "fingerprint"]
    REQUIRED_MEDIA_TYPES = ["audio"]

    def validate(self, parsed_sdp: Dict[str, Any], sdp_type: SDPType) -> None:
        """
        Validate a parsed SDP message.

        Args:
            parsed_sdp: Parsed SDP dictionary
            sdp_type: Expected SDP type (offer/answer)

        Raises:
            SDPValidationError: If validation fails
        """
        self._validate_version(parsed_sdp)
        self._validate_origin(parsed_sdp)
        self._validate_ice_credentials(parsed_sdp)
        self._validate_fingerprint(parsed_sdp)
        self._validate_media(parsed_sdp, sdp_type)

    def _validate_version(self, parsed_sdp: Dict[str, Any]) -> None:
        """Validate SDP version."""
        if parsed_sdp.get("version") != 0:
            raise SDPValidationError(
                "Invalid SDP version", field="version", reason="Must be 0"
            )

    def _validate_origin(self, parsed_sdp: Dict[str, Any]) -> None:
        """Validate origin line."""
        if not parsed_sdp.get("origin"):
            raise SDPValidationError(
                "Missing origin", field="origin", reason="Origin line is required"
            )

    def _validate_ice_credentials(self, parsed_sdp: Dict[str, Any]) -> None:
        """Validate ICE credentials."""
        attrs = parsed_sdp.get("attributes", {})

        ice_ufrag = attrs.get("ice-ufrag")
        if ice_ufrag and isinstance(ice_ufrag, str):
            if not SDP_ICE_UFRAG_PATTERN.match(ice_ufrag):
                raise SDPValidationError(
                    "Invalid ICE ufrag",
                    field="ice-ufrag",
                    reason="Must be 4-256 alphanumeric characters",
                )

        ice_pwd = attrs.get("ice-pwd")
        if ice_pwd and isinstance(ice_pwd, str):
            if not SDP_ICE_PWD_PATTERN.match(ice_pwd):
                raise SDPValidationError(
                    "Invalid ICE password",
                    field="ice-pwd",
                    reason="Must be 22-256 alphanumeric characters",
                )

    def _validate_fingerprint(self, parsed_sdp: Dict[str, Any]) -> None:
        """Validate DTLS fingerprint."""
        attrs = parsed_sdp.get("attributes", {})
        fingerprint = attrs.get("fingerprint")

        if fingerprint and isinstance(fingerprint, str):
            if not SDP_FINGERPRINT_PATTERN.match(fingerprint):
                raise SDPValidationError(
                    "Invalid fingerprint format",
                    field="fingerprint",
                    reason="Must be sha-256 or sha-1 with hex digest",
                )

    def _validate_media(self, parsed_sdp: Dict[str, Any], sdp_type: SDPType) -> None:
        """Validate media sections."""
        media = parsed_sdp.get("media", [])

        if not media:
            raise SDPValidationError(
                "No media sections",
                field="media",
                reason="At least one media section required",
            )

        media_types = [m.get("type") for m in media]

        if sdp_type == SDPType.OFFER:
            if "audio" not in media_types and "video" not in media_types:
                raise SDPValidationError(
                    "No audio or video media",
                    field="media",
                    reason="Offer must include audio or video",
                )


class SDPManipulator:
    """Manipulate SDP messages for WebRTC compatibility."""

    def set_bitrate(self, sdp: str, bitrate: int, media_type: str = "audio") -> str:
        """
        Set bandwidth limit in SDP.

        Args:
            sdp: Original SDP string
            bitrate: Target bitrate in bps
            media_type: Media type to modify

        Returns:
            Modified SDP string
        """
        lines = sdp.split("\n")
        result = []
        in_target_media = False
        bandwidth_added = False

        for line in lines:
            if line.startswith("m="):
                if in_target_media and not bandwidth_added:
                    result.append(f"b=AS:{bitrate // 1000}")
                in_target_media = line.startswith(f"m={media_type}")
                bandwidth_added = False

            if in_target_media and line.startswith("b="):
                result.append(f"b=AS:{bitrate // 1000}")
                bandwidth_added = True
                continue

            result.append(line)

            if in_target_media and line.startswith("c=") and not bandwidth_added:
                result.append(f"b=AS:{bitrate // 1000}")
                bandwidth_added = True

        return "\n".join(result)

    def add_ice_candidate(self, sdp: str, candidate: str, media_index: int = 0) -> str:
        """
        Add an ICE candidate to SDP.

        Args:
            sdp: Original SDP string
            candidate: ICE candidate string
            media_index: Media section index

        Returns:
            Modified SDP string
        """
        lines = sdp.split("\n")
        result = []
        current_media = -1

        for i, line in enumerate(lines):
            result.append(line)

            if line.startswith("m="):
                current_media += 1

            if current_media == media_index and line.startswith("a="):
                next_line = lines[i + 1] if i + 1 < len(lines) else ""
                if not next_line.startswith("a=candidate"):
                    if not candidate.startswith("a="):
                        candidate = f"a={candidate}"
                    result.append(candidate)

        return "\n".join(result)

    def munge_for_simulcast(self, sdp: str, num_layers: int = 3) -> str:
        """
        Modify SDP for simulcast support.

        Args:
            sdp: Original SDP string
            num_layers: Number of simulcast layers

        Returns:
            Modified SDP string
        """
        lines = sdp.split("\n")
        result = []
        in_video = False

        for line in lines:
            if line.startswith("m=video"):
                in_video = True
            elif line.startswith("m="):
                in_video = False

            result.append(line)

            if in_video and line.startswith("a=ssrc-group:FID"):
                rids = " ".join([f"r{i}" for i in range(num_layers)])
                result.append(f"a=simulcast:send {rids}")
                for i in range(num_layers):
                    result.append(f"a=rid:r{i} send")

        return "\n".join(result)


def parse_sdp(sdp: str) -> Dict[str, Any]:
    """Parse an SDP string."""
    parser = SDPParser()
    return parser.parse(sdp)


def validate_sdp(sdp: str, sdp_type: SDPType) -> None:
    """Parse and validate an SDP string."""
    parser = SDPParser()
    parsed = parser.parse(sdp)
    validator = SDPValidator()
    validator.validate(parsed, sdp_type)


def create_sdp_message(
    sdp: str, sdp_type: SDPType, session_id: Optional[str] = None
) -> SDPMessage:
    """Create an SDPMessage from raw SDP."""
    validate_sdp(sdp, sdp_type)
    return SDPMessage(sdp_type=sdp_type, sdp=sdp, session_id=session_id)
