"""
Perceptual Hashing (pHash) for image similarity detection.

Uses the imagehash library for robust perceptual hashing.

Provides:
- pHash computation for images
- Hamming distance comparison for similarity
- Near-duplicate detection that survives cropping, resizing, compression
"""

import hashlib
from typing import Optional, Tuple, List, Dict, Any
from dataclasses import dataclass
from enum import Enum
import importlib.util
import importlib

import utils.logger as logger
import utils.config as config


class HashType(Enum):
    """Types of hashes supported."""
    SHA256 = "sha256"
    PHASH = "phash"
    DHASH = "dhash"
    AHASH = "ahash"  # Average hash
    WHASH = "whash"  # Wavelet hash


@dataclass
class ImageHash:
    """Represents an image hash with type."""
    hash_value: str
    hash_type: HashType
    
    def __str__(self):
        return f"{self.hash_type.value}:{self.hash_value}"


# Check if dependencies are available
_PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None
_IMAGEHASH_AVAILABLE = importlib.util.find_spec("imagehash") is not None


def is_available() -> bool:
    """Check if perceptual hashing is available (requires PIL and imagehash)."""
    return _PIL_AVAILABLE and _IMAGEHASH_AVAILABLE


def get_config() -> Dict[str, Any]:
    """Get pHash configuration."""
    media_config = config.get("media", {})
    phash_config = media_config.get("phash", {})
    
    return {
        "enabled": phash_config.get("enabled", True),
        "hash_size": phash_config.get("hash_size", 8),  # 8 = 64-bit hash
        "algorithm": phash_config.get("algorithm", "phash"),  # phash, dhash, ahash, whash
        "similarity_threshold": phash_config.get("similarity_threshold", 10),
        "highfreq_factor": phash_config.get("highfreq_factor", 4),  # For pHash DCT
    }


def compute_phash(image_data: bytes, hash_size: Optional[int] = None) -> Optional[str]:
    """
    Compute perceptual hash (pHash) of an image using DCT.
    
    Args:
        image_data: Raw image bytes
        hash_size: Size of hash (default from config, 8 = 64-bit hash)
        
    Returns:
        Hex string of perceptual hash, or None on error
    """
    if not is_available():
        logger.debug("imagehash library not available for pHash computation")
        return None
    
    cfg = get_config()
    if hash_size is None:
        hash_size = cfg["hash_size"]
    
    try:
        import io
        Image = importlib.import_module("PIL.Image")
        imagehash = importlib.import_module("imagehash")

        img = Image.open(io.BytesIO(image_data))
        
        # Compute pHash using imagehash library
        phash = imagehash.phash(img, hash_size=hash_size, highfreq_factor=cfg["highfreq_factor"])
        return str(phash)
        
    except Exception as e:
        logger.warning(f"Failed to compute pHash: {e}")
        return None


def compute_dhash(image_data: bytes, hash_size: Optional[int] = None) -> Optional[str]:
    """
    Compute difference hash (dHash) of an image.
    
    Args:
        image_data: Raw image bytes
        hash_size: Size of hash (default from config)
        
    Returns:
        Hex string of difference hash, or None on error
    """
    if not is_available():
        logger.debug("imagehash library not available for dHash computation")
        return None
    
    cfg = get_config()
    if hash_size is None:
        hash_size = cfg["hash_size"]
    
    try:
        import io
        Image = importlib.import_module("PIL.Image")
        imagehash = importlib.import_module("imagehash")

        img = Image.open(io.BytesIO(image_data))
        
        dhash = imagehash.dhash(img, hash_size=hash_size)
        return str(dhash)
        
    except Exception as e:
        logger.warning(f"Failed to compute dHash: {e}")
        return None


def compute_ahash(image_data: bytes, hash_size: Optional[int] = None) -> Optional[str]:
    """
    Compute average hash (aHash) of an image.
    
    Args:
        image_data: Raw image bytes
        hash_size: Size of hash (default from config)
        
    Returns:
        Hex string of average hash, or None on error
    """
    if not is_available():
        logger.debug("imagehash library not available for aHash computation")
        return None
    
    cfg = get_config()
    if hash_size is None:
        hash_size = cfg["hash_size"]
    
    try:
        import io
        Image = importlib.import_module("PIL.Image")
        imagehash = importlib.import_module("imagehash")

        img = Image.open(io.BytesIO(image_data))
        
        ahash = imagehash.average_hash(img, hash_size=hash_size)
        return str(ahash)
        
    except Exception as e:
        logger.warning(f"Failed to compute aHash: {e}")
        return None


def compute_whash(image_data: bytes, hash_size: Optional[int] = None) -> Optional[str]:
    """
    Compute wavelet hash (wHash) of an image.
    
    Args:
        image_data: Raw image bytes
        hash_size: Size of hash (default from config)
        
    Returns:
        Hex string of wavelet hash, or None on error
    """
    if not is_available():
        logger.debug("imagehash library not available for wHash computation")
        return None
    
    cfg = get_config()
    if hash_size is None:
        hash_size = cfg["hash_size"]
    
    try:
        import io
        Image = importlib.import_module("PIL.Image")
        imagehash = importlib.import_module("imagehash")

        img = Image.open(io.BytesIO(image_data))
        
        whash = imagehash.whash(img, hash_size=hash_size)
        return str(whash)
        
    except Exception as e:
        logger.warning(f"Failed to compute wHash: {e}")
        return None


def compute_image_hash(image_data: bytes, hash_type: Optional[HashType] = None) -> Optional[ImageHash]:
    """
    Compute hash of an image using specified algorithm.
    
    Args:
        image_data: Raw image bytes
        hash_type: Type of hash to compute (default from config)
        
    Returns:
        ImageHash object or None on error
    """
    cfg = get_config()
    
    if hash_type is None:
        algo = cfg["algorithm"]
        hash_type = HashType(algo) if algo in [e.value for e in HashType] else HashType.PHASH
    
    if hash_type == HashType.SHA256:
        hash_value = hashlib.sha256(image_data).hexdigest()
        return ImageHash(hash_value=hash_value, hash_type=HashType.SHA256)
    
    elif hash_type == HashType.PHASH:
        hash_value = compute_phash(image_data)
        if hash_value:
            return ImageHash(hash_value=hash_value, hash_type=HashType.PHASH)
        return None
    
    elif hash_type == HashType.DHASH:
        hash_value = compute_dhash(image_data)
        if hash_value:
            return ImageHash(hash_value=hash_value, hash_type=HashType.DHASH)
        return None
    
    elif hash_type == HashType.AHASH:
        hash_value = compute_ahash(image_data)
        if hash_value:
            return ImageHash(hash_value=hash_value, hash_type=HashType.AHASH)
        return None
    
    elif hash_type == HashType.WHASH:
        hash_value = compute_whash(image_data)
        if hash_value:
            return ImageHash(hash_value=hash_value, hash_type=HashType.WHASH)
        return None
    
    return None


def hamming_distance(hash1: str, hash2: str) -> int:
    """
    Compute Hamming distance between two hex hash strings.
    
    Args:
        hash1: First hash (hex string)
        hash2: Second hash (hex string)
        
    Returns:
        Number of differing bits, or -1 on error
    """
    if not hash1 or not hash2:
        return -1
    
    if len(hash1) != len(hash2):
        return -1
    
    try:
        # Use imagehash for proper comparison if available
        if _IMAGEHASH_AVAILABLE:
            imagehash = importlib.import_module("imagehash")
            h1 = imagehash.hex_to_hash(hash1)
            h2 = imagehash.hex_to_hash(hash2)
            return h1 - h2  # imagehash overloads subtraction for Hamming distance
        
        # Fallback to manual calculation
        xor_result = int(hash1, 16) ^ int(hash2, 16)
        return bin(xor_result).count('1')
    except (ValueError, TypeError) as e:
        logger.warning(f"Hamming distance calculation failed: {e}")
        return -1


def are_similar(hash1: str, hash2: str, threshold: Optional[int] = None) -> bool:
    """
    Check if two perceptual hashes are similar.
    
    Args:
        hash1: First hash
        hash2: Second hash
        threshold: Maximum Hamming distance (default from config)
        
    Returns:
        True if hashes are similar (within threshold)
    """
    if threshold is None:
        cfg = get_config()
        threshold = int(cfg["similarity_threshold"])
    else:
        threshold = int(threshold)
    
    distance = hamming_distance(hash1, hash2)
    return 0 <= distance <= threshold


def find_similar_hashes(
    target_hash: str,
    hash_list: List[Tuple[str, str]],  # List of (hash_value, identifier)
    threshold: Optional[int] = None
) -> List[Tuple[str, int]]:
    """
    Find all hashes similar to target from a list.
    
    Args:
        target_hash: Hash to compare against
        hash_list: List of (hash_value, identifier) tuples
        threshold: Maximum Hamming distance (default from config)
        
    Returns:
        List of (identifier, distance) tuples for similar hashes
    """
    if threshold is None:
        cfg = get_config()
        threshold = int(cfg["similarity_threshold"])
    else:
        threshold = int(threshold)
    
    similar = []
    for hash_value, identifier in hash_list:
        distance = hamming_distance(target_hash, hash_value)
        if 0 <= distance <= threshold:
            similar.append((identifier, distance))
    
    # Sort by distance (most similar first)
    similar.sort(key=lambda x: x[1])
    return similar


def compute_all_hashes(image_data: bytes) -> Dict[str, Optional[str]]:
    """
    Compute all available hashes for an image.
    
    Args:
        image_data: Raw image bytes
        
    Returns:
        Dict with hash types as keys and hash values
    """
    result: Dict[str, Optional[str]] = {"sha256": hashlib.sha256(image_data).hexdigest()}
    
    if is_available():
        result['phash'] = compute_phash(image_data)
        result['dhash'] = compute_dhash(image_data)
        result['ahash'] = compute_ahash(image_data)
        result['whash'] = compute_whash(image_data)
    
    return result


def is_image_content_type(content_type: str) -> bool:
    """Check if content type is an image."""
    if not content_type:
        return False
    return content_type.lower().startswith('image/')
