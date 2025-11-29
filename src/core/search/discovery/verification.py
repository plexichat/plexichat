"""
Verification manager - Handle server verification.
"""

from typing import Optional, Dict, Any

import utils.logger as logger

from ..models import VerificationLevel, ServerListing
from ..exceptions import VerificationError, ServerNotListedError


class VerificationManager:
    """Manage server verification status."""
    
    def __init__(self, db, servers_module=None):
        self._db = db
        self._servers = servers_module
    
    def verify_server(
        self,
        server_id: int,
        level: VerificationLevel,
        verified_by: int = None,
        reason: str = None,
    ) -> bool:
        """
        Set verification level for a server.
        
        Args:
            server_id: ID of server to verify
            level: Verification level to set
            verified_by: ID of admin who verified
            reason: Reason for verification
            
        Returns:
            True if verification updated
        """
        listing = self._get_listing(server_id)
        if not listing:
            raise ServerNotListedError(
                "Server is not listed in discovery",
                server_id=server_id
            )
        
        is_verified = level in (VerificationLevel.HIGH, VerificationLevel.VERIFIED)
        
        self._db.execute(
            """UPDATE search_server_listings 
               SET verification_level = ?, is_verified = ?
               WHERE server_id = ?""",
            (level.value, 1 if is_verified else 0, server_id)
        )
        
        logger.info(
            f"Server {server_id} verification set to {level.value} "
            f"by user {verified_by}"
        )
        
        return True
    
    def get_verification_level(self, server_id: int) -> VerificationLevel:
        """Get verification level for a server."""
        listing = self._get_listing(server_id)
        if not listing:
            return VerificationLevel.NONE
        
        return VerificationLevel(listing["verification_level"])
    
    def is_verified(self, server_id: int) -> bool:
        """Check if server is verified."""
        listing = self._get_listing(server_id)
        if not listing:
            return False
        return bool(listing["is_verified"])
    
    def check_verification_requirements(
        self,
        server_id: int,
        level: VerificationLevel,
    ) -> Dict[str, Any]:
        """
        Check if server meets requirements for verification level.
        
        Returns dict with 'eligible' bool and 'missing' list of requirements.
        """
        result = {"eligible": True, "missing": []}
        
        if not self._servers:
            return result
        
        try:
            server = self._servers.get_server(server_id, None)
            if not server:
                result["eligible"] = False
                result["missing"].append("Server not found")
                return result
        except Exception:
            result["eligible"] = False
            result["missing"].append("Could not fetch server info")
            return result
        
        member_count = self._get_member_count(server_id)
        
        requirements = {
            VerificationLevel.LOW: {"min_members": 10},
            VerificationLevel.MEDIUM: {"min_members": 100},
            VerificationLevel.HIGH: {"min_members": 500},
            VerificationLevel.VERIFIED: {"min_members": 1000},
        }
        
        reqs = requirements.get(level, {})
        min_members = reqs.get("min_members", 0)
        
        if member_count < min_members:
            result["eligible"] = False
            result["missing"].append(
                f"Requires {min_members} members (current: {member_count})"
            )
        
        return result
    
    def _get_listing(self, server_id: int) -> Optional[Dict]:
        """Get server listing from database."""
        return self._db.fetch_one(
            "SELECT * FROM search_server_listings WHERE server_id = ?",
            (server_id,)
        )
    
    def _get_member_count(self, server_id: int) -> int:
        """Get member count for a server."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM srv_members WHERE server_id = ?",
            (server_id,)
        )
        return row["count"] if row else 0
