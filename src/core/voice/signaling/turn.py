"""
TURN credential generation - Time-limited TURN credentials using HMAC.

Implements TURN REST API credential generation per RFC 5389 and
draft-uberti-behave-turn-rest.
"""

import hmac
import hashlib
import base64
import time
from typing import List, Optional

from .models import TURNCredentials, ICEServer
from .exceptions import TURNCredentialError


class TURNCredentialGenerator:
    """
    Generates time-limited TURN credentials using HMAC-SHA1.
    
    The username format is: timestamp:user_id
    The credential is: HMAC-SHA1(secret, username)
    
    This follows the TURN REST API specification used by coturn and other
    TURN servers.
    """
    
    def __init__(
        self,
        secret: str,
        turn_urls: List[str],
        ttl: int = 86400,
    ):
        """
        Initialize the credential generator.
        
        Args:
            secret: Shared secret with TURN server
            turn_urls: List of TURN server URLs
            ttl: Credential time-to-live in seconds (default 24 hours)
        """
        if not secret:
            raise TURNCredentialError("TURN secret is required")
        
        self._secret = secret.encode("utf-8")
        self._turn_urls = turn_urls
        self._ttl = ttl
    
    def generate(self, user_id: int) -> TURNCredentials:
        """
        Generate TURN credentials for a user.
        
        Args:
            user_id: User identifier
            
        Returns:
            TURNCredentials with username, credential, and expiry
        """
        now = int(time.time())
        expires_at = now + self._ttl
        
        # Username format: expiry_timestamp:user_id
        username = f"{expires_at}:{user_id}"
        
        # Generate HMAC-SHA1 credential
        credential = self._generate_credential(username)
        
        return TURNCredentials(
            username=username,
            credential=credential,
            urls=self._turn_urls.copy(),
            ttl=self._ttl,
            expires_at=expires_at,
        )
    
    def _generate_credential(self, username: str) -> str:
        """
        Generate HMAC-SHA1 credential.
        
        Args:
            username: Username string
            
        Returns:
            Base64-encoded HMAC-SHA1 digest
        """
        digest = hmac.new(
            self._secret,
            username.encode("utf-8"),
            hashlib.sha1
        ).digest()
        
        return base64.b64encode(digest).decode("utf-8")
    
    def verify(self, username: str, credential: str) -> bool:
        """
        Verify TURN credentials.
        
        Args:
            username: Username to verify
            credential: Credential to verify
            
        Returns:
            True if credentials are valid and not expired
        """
        # Check expiry
        try:
            parts = username.split(":")
            if len(parts) < 2:
                return False
            
            expires_at = int(parts[0])
            if time.time() > expires_at:
                return False
        except (ValueError, IndexError):
            return False
        
        # Verify HMAC
        expected = self._generate_credential(username)
        return hmac.compare_digest(credential, expected)


class ICEServerBuilder:
    """Builds ICE server configurations for WebRTC."""
    
    def __init__(
        self,
        stun_urls: Optional[List[str]] = None,
        turn_urls: Optional[List[str]] = None,
        turn_secret: str = "",
        turn_ttl: int = 86400,
        turn_username: str = "",
        turn_credential: str = "",
    ):
        """
        Initialize the ICE server builder.
        
        Args:
            stun_urls: List of STUN server URLs
            turn_urls: List of TURN server URLs
            turn_secret: Shared secret for time-limited TURN credentials (coturn)
            turn_ttl: TURN credential TTL in seconds
            turn_username: Static TURN username (for services like metered.ca)
            turn_credential: Static TURN credential/password
        """
        self._stun_urls = stun_urls or ["stun:stun.l.google.com:19302"]
        self._turn_urls = turn_urls or []
        self._turn_generator = None
        self._static_username = turn_username
        self._static_credential = turn_credential
        
        # Use time-limited credentials if secret is provided, otherwise use static
        if turn_secret and turn_urls:
            self._turn_generator = TURNCredentialGenerator(
                secret=turn_secret,
                turn_urls=turn_urls,
                ttl=turn_ttl,
            )
    
    def build(self, user_id: int) -> List[ICEServer]:
        """
        Build ICE server list for a user.
        
        Args:
            user_id: User identifier
            
        Returns:
            List of ICEServer configurations
        """
        servers = []
        
        # Add STUN servers (no credentials needed)
        if self._stun_urls:
            servers.append(ICEServer(urls=self._stun_urls.copy()))
        
        # Add TURN servers with credentials
        if self._turn_urls:
            if self._turn_generator:
                # Use time-limited credentials (coturn with static-auth-secret)
                creds = self._turn_generator.generate(user_id)
                servers.append(ICEServer(
                    urls=creds.urls,
                    username=creds.username,
                    credential=creds.credential,
                ))
            elif self._static_username and self._static_credential:
                # Use static credentials (metered.ca, Twilio, etc.)
                servers.append(ICEServer(
                    urls=self._turn_urls.copy(),
                    username=self._static_username,
                    credential=self._static_credential,
                ))
        
        return servers
    
    def get_turn_credentials(self, user_id: int) -> Optional[TURNCredentials]:
        """
        Get TURN credentials for a user.
        
        Args:
            user_id: User identifier
            
        Returns:
            TURNCredentials or None if TURN not configured
        """
        if self._turn_generator:
            return self._turn_generator.generate(user_id)
        
        # Return static credentials as TURNCredentials
        if self._turn_urls and self._static_username and self._static_credential:
            return TURNCredentials(
                username=self._static_username,
                credential=self._static_credential,
                urls=self._turn_urls.copy(),
                ttl=0,  # Static credentials don't expire
                expires_at=0,
            )
        
        return None


def generate_turn_credentials(
    user_id: int,
    secret: str,
    turn_urls: List[str],
    ttl: int = 86400,
) -> TURNCredentials:
    """
    Generate TURN credentials for a user.
    
    Args:
        user_id: User identifier
        secret: Shared secret with TURN server
        turn_urls: List of TURN server URLs
        ttl: Credential TTL in seconds
        
    Returns:
        TURNCredentials
    """
    generator = TURNCredentialGenerator(secret, turn_urls, ttl)
    return generator.generate(user_id)


def verify_turn_credentials(
    username: str,
    credential: str,
    secret: str,
) -> bool:
    """
    Verify TURN credentials.
    
    Args:
        username: Username to verify
        credential: Credential to verify
        secret: Shared secret
        
    Returns:
        True if valid
    """
    generator = TURNCredentialGenerator(secret, [])
    return generator.verify(username, credential)
