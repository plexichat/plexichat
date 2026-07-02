"""
Digital signature utilities using Ed25519.

Provides key pair generation, signing, and verification
using the Ed25519 signature scheme.
"""

from typing import Tuple


def generate_key_pair() -> Tuple[bytes, bytes]:
    """Generate an Ed25519 key pair for digital signatures.

    Returns:
        Tuple[bytes, bytes]: (private_key, public_key)
    """
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization

    private_key = ed25519.Ed25519PrivateKey.generate()
    return (
        private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        ),
        private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    )


def sign_data(data: bytes, private_key_bytes: bytes) -> bytes:
    """Sign data using Ed25519 private key."""
    from cryptography.hazmat.primitives.asymmetric import ed25519

    return ed25519.Ed25519PrivateKey.from_private_bytes(private_key_bytes).sign(data)


def verify_signature(data: bytes, signature: bytes, public_key_bytes: bytes) -> bool:
    """Verify an Ed25519 signature."""
    from cryptography.hazmat.primitives.asymmetric import ed25519

    try:
        ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            signature, data
        )
        return True
    except Exception:
        return False
