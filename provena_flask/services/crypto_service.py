"""
Cryptographic Service Module for Provena.

Provides Ed25519 digital signature operations for provenance binding.
This module handles:
- Key pair generation
- Metadata signing
- Signature verification

All operations are independently verifiable and use industry-standard cryptography.
"""

import json
from typing import Dict, Tuple, Any
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature


class CryptoService:
    """
    Ed25519 cryptographic service for provenance signatures.
    
    This service provides cryptographically secure operations for binding
    AI-generated images to their provenance metadata.
    """
    
    @staticmethod
    def generate_keys() -> Tuple[bytes, bytes]:
        """
        Generate a new Ed25519 key pair.
        
        Returns:
            Tuple[bytes, bytes]: (private_key_bytes, public_key_bytes)
                - private_key_bytes: PEM-encoded private key
                - public_key_bytes: PEM-encoded public key
        
        Example:
            >>> private_key, public_key = CryptoService.generate_keys()
            >>> print(f"Generated key pair: {len(private_key)} bytes private, {len(public_key)} bytes public")
        """
        # Generate new Ed25519 private key
        private_key = Ed25519PrivateKey.generate()
        
        # Serialize private key to PEM format
        private_key_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        
        # Extract public key and serialize to PEM format
        public_key = private_key.public_key()
        public_key_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        return private_key_bytes, public_key_bytes
    
    @staticmethod
    def sign(data: Dict[str, Any], private_key_bytes: bytes) -> bytes:
        """
        Sign arbitrary metadata using Ed25519 private key.
        
        Args:
            data: Dictionary containing metadata to sign (will be JSON-serialized)
            private_key_bytes: PEM-encoded private key
        
        Returns:
            bytes: Digital signature (64 bytes for Ed25519)
        
        Raises:
            ValueError: If private key is invalid or data cannot be serialized
        
        Example:
            >>> metadata = {"image_id": "abc123", "model_id": "gpt-vision", "timestamp": "2026-01-26"}
            >>> signature = CryptoService.sign(metadata, private_key_bytes)
            >>> print(f"Signature: {signature.hex()[:32]}...")
        """
        try:
            # Serialize data to canonical JSON (sorted keys for deterministic signing)
            message = json.dumps(data, sort_keys=True, separators=(',', ':')).encode('utf-8')
            
            # Load private key from PEM bytes
            private_key = serialization.load_pem_private_key(
                private_key_bytes,
                password=None
            )
            
            # Ensure it's an Ed25519 key
            if not isinstance(private_key, Ed25519PrivateKey):
                raise ValueError("Provided key is not an Ed25519 private key")
            
            # Sign the message
            signature = private_key.sign(message)
            
            return signature
            
        except Exception as e:
            raise ValueError(f"Failed to sign data: {str(e)}")
    
    @staticmethod
    def verify(data: Dict[str, Any], signature: bytes, public_key_bytes: bytes) -> bool:
        """
        Verify a signature using Ed25519 public key.
        
        Args:
            data: Dictionary containing metadata that was signed
            signature: Digital signature to verify
            public_key_bytes: PEM-encoded public key
        
        Returns:
            bool: True if signature is valid, False otherwise
        
        Example:
            >>> is_valid = CryptoService.verify(metadata, signature, public_key_bytes)
            >>> print(f"Signature valid: {is_valid}")
        """
        try:
            # Serialize data to canonical JSON (must match signing format)
            message = json.dumps(data, sort_keys=True, separators=(',', ':')).encode('utf-8')
            
            # Load public key from PEM bytes
            public_key = serialization.load_pem_public_key(public_key_bytes)
            
            # Ensure it's an Ed25519 key
            if not isinstance(public_key, Ed25519PublicKey):
                raise ValueError("Provided key is not an Ed25519 public key")
            
            # Verify signature
            public_key.verify(signature, message)
            
            # If no exception was raised, signature is valid
            return True
            
        except InvalidSignature:
            # Signature verification failed
            return False
        except Exception as e:
            # Other errors (invalid key, malformed data, etc.)
            raise ValueError(f"Failed to verify signature: {str(e)}")
    
    @staticmethod
    def get_key_id(public_key_bytes: bytes) -> str:
        """
        Generate a unique identifier for a public key.
        
        This is useful for key rotation and identifying which key signed a record.
        
        Args:
            public_key_bytes: PEM-encoded public key
        
        Returns:
            str: Hex-encoded SHA-256 hash of the public key (first 16 bytes)
        
        Example:
            >>> key_id = CryptoService.get_key_id(public_key_bytes)
            >>> print(f"Key ID: {key_id}")
        """
        import hashlib
        
        # Hash the public key
        key_hash = hashlib.sha256(public_key_bytes).digest()
        
        # Return first 16 bytes as hex (32 characters)
        return key_hash[:16].hex()
    
    @staticmethod
    def validate_signature_format(signature: bytes) -> bool:
        """
        Validate that a signature has the correct format for Ed25519.
        
        Args:
            signature: Signature bytes to validate
        
        Returns:
            bool: True if signature is 64 bytes (Ed25519 standard), False otherwise
        """
        return len(signature) == 64


# Convenience functions for module-level access
def generate_keys() -> Tuple[bytes, bytes]:
    """Generate Ed25519 key pair. See CryptoService.generate_keys() for details."""
    return CryptoService.generate_keys()


def sign(data: Dict[str, Any], private_key_bytes: bytes) -> bytes:
    """Sign data with Ed25519. See CryptoService.sign() for details."""
    return CryptoService.sign(data, private_key_bytes)


def verify(data: Dict[str, Any], signature: bytes, public_key_bytes: bytes) -> bool:
    """Verify Ed25519 signature. See CryptoService.verify() for details."""
    return CryptoService.verify(data, signature, public_key_bytes)


def get_key_id(public_key_bytes: bytes) -> str:
    """Get key identifier. See CryptoService.get_key_id() for details."""
    return CryptoService.get_key_id(public_key_bytes)
