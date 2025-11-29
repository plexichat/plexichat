"""
Tests for TURN credential generation.
"""

import pytest
import time
import base64
import hmac
import hashlib

from src.core.voice.signaling.turn import (
    TURNCredentialGenerator,
    ICEServerBuilder,
    generate_turn_credentials,
    verify_turn_credentials,
)
from src.core.voice.signaling.models import TURNCredentials, ICEServer
from src.core.voice.signaling.exceptions import TURNCredentialError


class TestTURNCredentialGenerator:
    """Tests for TURN credential generation."""
    
    def test_generate_credentials(self):
        """Test generating TURN credentials."""
        generator = TURNCredentialGenerator(
            secret="test_secret",
            turn_urls=["turn:turn.example.com:3478"],
            ttl=3600,
        )
        
        creds = generator.generate(user_id=123)
        
        assert creds is not None
        assert isinstance(creds, TURNCredentials)
        assert creds.username is not None
        assert creds.credential is not None
        assert len(creds.urls) == 1
        assert creds.ttl == 3600
        assert creds.expires_at > time.time()
    
    def test_username_format(self):
        """Test that username follows timestamp:user_id format."""
        generator = TURNCredentialGenerator(
            secret="test_secret",
            turn_urls=["turn:turn.example.com:3478"],
            ttl=3600,
        )
        
        creds = generator.generate(user_id=456)
        
        parts = creds.username.split(":")
        assert len(parts) == 2
        assert parts[1] == "456"
        
        # First part should be expiry timestamp
        expiry = int(parts[0])
        assert expiry > time.time()
        assert expiry <= time.time() + 3600
    
    def test_credential_is_hmac_sha1(self):
        """Test that credential is HMAC-SHA1 of username."""
        secret = "test_secret"
        generator = TURNCredentialGenerator(
            secret=secret,
            turn_urls=["turn:turn.example.com:3478"],
            ttl=3600,
        )
        
        creds = generator.generate(user_id=789)
        
        # Manually compute expected credential
        expected_digest = hmac.new(
            secret.encode("utf-8"),
            creds.username.encode("utf-8"),
            hashlib.sha1
        ).digest()
        expected_credential = base64.b64encode(expected_digest).decode("utf-8")
        
        assert creds.credential == expected_credential
    
    def test_verify_valid_credentials(self):
        """Test verifying valid credentials."""
        generator = TURNCredentialGenerator(
            secret="test_secret",
            turn_urls=["turn:turn.example.com:3478"],
            ttl=3600,
        )
        
        creds = generator.generate(user_id=123)
        
        result = generator.verify(creds.username, creds.credential)
        
        assert result is True
    
    def test_verify_invalid_credential(self):
        """Test verifying invalid credential."""
        generator = TURNCredentialGenerator(
            secret="test_secret",
            turn_urls=["turn:turn.example.com:3478"],
            ttl=3600,
        )
        
        creds = generator.generate(user_id=123)
        
        result = generator.verify(creds.username, "invalid_credential")
        
        assert result is False
    
    def test_verify_expired_credentials(self):
        """Test verifying expired credentials."""
        generator = TURNCredentialGenerator(
            secret="test_secret",
            turn_urls=["turn:turn.example.com:3478"],
            ttl=1,  # 1 second TTL
        )
        
        # Create credentials with past expiry
        expired_username = f"{int(time.time()) - 100}:123"
        digest = hmac.new(
            "test_secret".encode("utf-8"),
            expired_username.encode("utf-8"),
            hashlib.sha1
        ).digest()
        expired_credential = base64.b64encode(digest).decode("utf-8")
        
        result = generator.verify(expired_username, expired_credential)
        
        assert result is False
    
    def test_empty_secret_raises(self):
        """Test that empty secret raises error."""
        with pytest.raises(TURNCredentialError):
            TURNCredentialGenerator(
                secret="",
                turn_urls=["turn:turn.example.com:3478"],
            )
    
    def test_multiple_turn_urls(self):
        """Test with multiple TURN URLs."""
        generator = TURNCredentialGenerator(
            secret="test_secret",
            turn_urls=[
                "turn:turn1.example.com:3478",
                "turn:turn2.example.com:3478",
                "turns:turn.example.com:5349",
            ],
            ttl=3600,
        )
        
        creds = generator.generate(user_id=123)
        
        assert len(creds.urls) == 3


class TestICEServerBuilder:
    """Tests for ICE server configuration builder."""
    
    def test_build_with_stun_only(self):
        """Test building ICE servers with STUN only."""
        builder = ICEServerBuilder(
            stun_urls=["stun:stun.l.google.com:19302"],
        )
        
        servers = builder.build(user_id=123)
        
        assert len(servers) == 1
        assert isinstance(servers[0], ICEServer)
        assert "stun:" in servers[0].urls[0]
        assert servers[0].username is None
        assert servers[0].credential is None
    
    def test_build_with_turn(self):
        """Test building ICE servers with TURN."""
        builder = ICEServerBuilder(
            stun_urls=["stun:stun.l.google.com:19302"],
            turn_urls=["turn:turn.example.com:3478"],
            turn_secret="test_secret",
            turn_ttl=3600,
        )
        
        servers = builder.build(user_id=123)
        
        assert len(servers) == 2
        
        # First should be STUN
        assert "stun:" in servers[0].urls[0]
        
        # Second should be TURN with credentials
        assert "turn:" in servers[1].urls[0]
        assert servers[1].username is not None
        assert servers[1].credential is not None
    
    def test_get_turn_credentials(self):
        """Test getting TURN credentials directly."""
        builder = ICEServerBuilder(
            turn_urls=["turn:turn.example.com:3478"],
            turn_secret="test_secret",
            turn_ttl=3600,
        )
        
        creds = builder.get_turn_credentials(user_id=123)
        
        assert creds is not None
        assert isinstance(creds, TURNCredentials)
    
    def test_get_turn_credentials_not_configured(self):
        """Test getting TURN credentials when not configured."""
        builder = ICEServerBuilder(
            stun_urls=["stun:stun.l.google.com:19302"],
        )
        
        creds = builder.get_turn_credentials(user_id=123)
        
        assert creds is None
    
    def test_default_stun_servers(self):
        """Test that default STUN servers are used."""
        builder = ICEServerBuilder()
        
        servers = builder.build(user_id=123)
        
        assert len(servers) >= 1
        assert "stun:" in servers[0].urls[0]


class TestConvenienceFunctions:
    """Tests for convenience functions."""
    
    def test_generate_turn_credentials(self):
        """Test generate_turn_credentials function."""
        creds = generate_turn_credentials(
            user_id=123,
            secret="test_secret",
            turn_urls=["turn:turn.example.com:3478"],
            ttl=3600,
        )
        
        assert creds is not None
        assert isinstance(creds, TURNCredentials)
    
    def test_verify_turn_credentials_valid(self):
        """Test verify_turn_credentials with valid credentials."""
        creds = generate_turn_credentials(
            user_id=123,
            secret="test_secret",
            turn_urls=["turn:turn.example.com:3478"],
        )
        
        result = verify_turn_credentials(
            username=creds.username,
            credential=creds.credential,
            secret="test_secret",
        )
        
        assert result is True
    
    def test_verify_turn_credentials_wrong_secret(self):
        """Test verify_turn_credentials with wrong secret."""
        creds = generate_turn_credentials(
            user_id=123,
            secret="test_secret",
            turn_urls=["turn:turn.example.com:3478"],
        )
        
        result = verify_turn_credentials(
            username=creds.username,
            credential=creds.credential,
            secret="wrong_secret",
        )
        
        assert result is False
