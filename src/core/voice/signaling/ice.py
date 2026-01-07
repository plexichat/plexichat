"""
ICE handling - ICE candidate parsing, validation, and relay.

Implements ICE candidate handling per RFC 5245.
"""

import re
from typing import Dict, List, Optional, Any

from .models import ICECandidate
from .exceptions import ICECandidateError


# ICE candidate patterns
ICE_CANDIDATE_PATTERN = re.compile(
    r"^(?:a=)?candidate:"
    r"(\S+)\s+"  # foundation
    r"(\d+)\s+"  # component
    r"(udp|tcp|UDP|TCP)\s+"  # transport
    r"(\d+)\s+"  # priority
    r"(\S+)\s+"  # connection-address
    r"(\d+)\s+"  # port
    r"typ\s+(host|srflx|prflx|relay)"  # candidate-type
    r"(?:\s+raddr\s+(\S+))?"  # rel-addr (optional)
    r"(?:\s+rport\s+(\d+))?"  # rel-port (optional)
    r"(?:\s+tcptype\s+(active|passive|so))?"  # tcp-type (optional)
    r"(?:\s+generation\s+(\d+))?"  # generation (optional)
    r"(?:\s+ufrag\s+(\S+))?"  # ufrag (optional)
    r"(?:\s+network-id\s+(\d+))?"  # network-id (optional)
    r"(?:\s+network-cost\s+(\d+))?"  # network-cost (optional)
)

# Valid candidate types
CANDIDATE_TYPES = {"host", "srflx", "prflx", "relay"}

# Valid transports
VALID_TRANSPORTS = {"udp", "tcp", "UDP", "TCP"}


class ICECandidateParser:
    """Parser for ICE candidates."""

    def parse(self, candidate_str: str) -> Dict[str, Any]:
        """
        Parse an ICE candidate string.

        Args:
            candidate_str: Raw ICE candidate string

        Returns:
            Parsed candidate as dictionary

        Raises:
            ICECandidateError: If candidate is malformed
        """
        candidate_str = candidate_str.strip()

        if not candidate_str:
            raise ICECandidateError("Empty candidate string", candidate=candidate_str)

        # Handle end-of-candidates
        if candidate_str == "" or "end-of-candidates" in candidate_str.lower():
            return {"type": "end-of-candidates"}

        match = ICE_CANDIDATE_PATTERN.match(candidate_str)
        if not match:
            raise ICECandidateError(
                "Invalid ICE candidate format", candidate=candidate_str[:100]
            )

        parsed = {
            "foundation": match.group(1),
            "component": int(match.group(2)),
            "transport": match.group(3).lower(),
            "priority": int(match.group(4)),
            "address": match.group(5),
            "port": int(match.group(6)),
            "type": match.group(7),
        }

        # Optional fields
        if match.group(8):
            parsed["related_address"] = match.group(8)
        if match.group(9):
            parsed["related_port"] = int(match.group(9))
        if match.group(10):
            parsed["tcp_type"] = match.group(10)
        if match.group(11):
            parsed["generation"] = int(match.group(11))
        if match.group(12):
            parsed["ufrag"] = match.group(12)
        if match.group(13):
            parsed["network_id"] = int(match.group(13))
        if match.group(14):
            parsed["network_cost"] = int(match.group(14))

        return parsed

    def to_candidate(
        self,
        candidate_str: str,
        sdp_mid: Optional[str] = None,
        sdp_mline_index: Optional[int] = None,
    ) -> ICECandidate:
        """
        Parse string and create ICECandidate object.

        Args:
            candidate_str: Raw ICE candidate string
            sdp_mid: Media stream ID
            sdp_mline_index: Media line index

        Returns:
            ICECandidate object
        """
        parsed = self.parse(candidate_str)

        if parsed.get("type") == "end-of-candidates":
            return ICECandidate(
                candidate="",
                sdp_mid=sdp_mid,
                sdp_mline_index=sdp_mline_index,
            )

        return ICECandidate(
            candidate=candidate_str,
            sdp_mid=sdp_mid,
            sdp_mline_index=sdp_mline_index,
            username_fragment=parsed.get("ufrag"),
        )


class ICECandidateValidator:
    """Validator for ICE candidates."""

    def validate(self, parsed: Dict[str, Any]) -> None:
        """
        Validate a parsed ICE candidate.

        Args:
            parsed: Parsed candidate dictionary

        Raises:
            ICECandidateError: If validation fails
        """
        if parsed.get("type") == "end-of-candidates":
            return

        self._validate_transport(parsed)
        self._validate_type(parsed)
        self._validate_address(parsed)
        self._validate_port(parsed)
        self._validate_priority(parsed)

    def _validate_transport(self, parsed: Dict[str, Any]) -> None:
        """Validate transport protocol."""
        transport = parsed.get("transport", "").lower()
        if transport not in {"udp", "tcp"}:
            raise ICECandidateError(
                f"Invalid transport: {transport}", candidate=str(parsed)
            )

    def _validate_type(self, parsed: Dict[str, Any]) -> None:
        """Validate candidate type."""
        ctype = parsed.get("type")
        if ctype not in CANDIDATE_TYPES:
            raise ICECandidateError(
                f"Invalid candidate type: {ctype}", candidate=str(parsed)
            )

    def _validate_address(self, parsed: Dict[str, Any]) -> None:
        """Validate IP address."""
        address = parsed.get("address", "")
        if not address:
            raise ICECandidateError("Missing address", candidate=str(parsed))

        # Basic IP validation (IPv4 or IPv6)
        if not self._is_valid_ip(address):
            raise ICECandidateError(
                f"Invalid IP address: {address}", candidate=str(parsed)
            )

    def _validate_port(self, parsed: Dict[str, Any]) -> None:
        """Validate port number."""
        port = parsed.get("port", 0)
        if not (0 < port <= 65535):
            raise ICECandidateError(f"Invalid port: {port}", candidate=str(parsed))

    def _validate_priority(self, parsed: Dict[str, Any]) -> None:
        """Validate priority value."""
        priority = parsed.get("priority", 0)
        if not (0 <= priority <= 2**31 - 1):
            raise ICECandidateError(
                f"Invalid priority: {priority}", candidate=str(parsed)
            )

    def _is_valid_ip(self, address: str) -> bool:
        """Check if address is valid IPv4 or IPv6."""
        # IPv4
        ipv4_pattern = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
        if ipv4_pattern.match(address):
            parts = address.split(".")
            return all(0 <= int(p) <= 255 for p in parts)

        # IPv6 (simplified check)
        if ":" in address:
            return True

        # Hostname (for relay candidates)
        hostname_pattern = re.compile(
            r"^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*$"
        )
        return bool(hostname_pattern.match(address))


class ICECandidateManager:
    """Manages ICE candidates for a connection."""

    def __init__(self):
        self._candidates: Dict[str, List[ICECandidate]] = {}
        self._parser = ICECandidateParser()
        self._validator = ICECandidateValidator()

    def add_candidate(
        self,
        session_id: str,
        candidate_str: str,
        sdp_mid: Optional[str] = None,
        sdp_mline_index: Optional[int] = None,
    ) -> ICECandidate:
        """
        Add an ICE candidate for a session.

        Args:
            session_id: Session identifier
            candidate_str: Raw ICE candidate string
            sdp_mid: Media stream ID
            sdp_mline_index: Media line index

        Returns:
            Parsed ICECandidate
        """
        parsed = self._parser.parse(candidate_str)
        self._validator.validate(parsed)

        candidate = self._parser.to_candidate(candidate_str, sdp_mid, sdp_mline_index)

        if session_id not in self._candidates:
            self._candidates[session_id] = []

        self._candidates[session_id].append(candidate)
        return candidate

    def get_candidates(self, session_id: str) -> List[ICECandidate]:
        """Get all candidates for a session."""
        return self._candidates.get(session_id, [])

    def clear_candidates(self, session_id: str) -> None:
        """Clear all candidates for a session."""
        if session_id in self._candidates:
            del self._candidates[session_id]

    def get_best_candidate(self, session_id: str) -> Optional[ICECandidate]:
        """
        Get the best candidate for a session based on priority.

        Prefers relay > srflx > host for NAT traversal.
        """
        candidates = self._candidates.get(session_id, [])
        if not candidates:
            return None

        # Sort by type preference then priority
        type_priority = {"relay": 0, "srflx": 1, "prflx": 2, "host": 3}

        def sort_key(c: ICECandidate) -> tuple:
            parsed = (
                self._parser.parse(c.candidate)
                if c.candidate
                else {"type": "host", "priority": 0}
            )
            return (
                type_priority.get(parsed.get("type", "host"), 4),
                -parsed.get("priority", 0),
            )

        sorted_candidates = sorted(candidates, key=sort_key)
        return sorted_candidates[0] if sorted_candidates else None


def parse_ice_candidate(
    candidate_str: str,
    sdp_mid: Optional[str] = None,
    sdp_mline_index: Optional[int] = None,
) -> ICECandidate:
    """Parse an ICE candidate string."""
    parser = ICECandidateParser()
    validator = ICECandidateValidator()

    parsed = parser.parse(candidate_str)
    validator.validate(parsed)

    return parser.to_candidate(candidate_str, sdp_mid, sdp_mline_index)


def validate_ice_candidate(candidate_str: str) -> bool:
    """Validate an ICE candidate string."""
    try:
        parser = ICECandidateParser()
        validator = ICECandidateValidator()
        parsed = parser.parse(candidate_str)
        validator.validate(parsed)
        return True
    except ICECandidateError:
        return False
