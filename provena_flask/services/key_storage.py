"""
Secure Key Storage Service for Provena-FLASK.

Manages cryptographic key persistence with security best practices:
- Private keys stored in restricted directory
- File permissions set to owner-only (where supported)
- Private keys NEVER exposed via API responses
- Public keys freely accessible for verification
"""

import os
import stat
from pathlib import Path
from typing import Optional, Tuple
import logging

from provena_flask.services.crypto_service import CryptoService

logger = logging.getLogger(__name__)


class KeyStorageService:
    """
    Secure file-based key storage for Ed25519 keys.
    
    This service ensures:
    - Private keys are stored securely with restricted permissions
    - Keys are persisted across application restarts
    - Private keys are never returned in API responses
    - Public keys are easily accessible for verification
    """
    
    def __init__(self, keys_directory: str):
        """
        Initialize key storage service.
        
        Args:
            keys_directory: Path to directory for storing keys
        """
        self.keys_dir = Path(keys_directory)
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        
        # Set directory permissions to owner-only (Unix-like systems)
        try:
            os.chmod(self.keys_dir, stat.S_IRWXU)  # 700 permissions
        except (OSError, AttributeError):
            # Windows doesn't support chmod in the same way
            logger.warning("Could not set directory permissions (may not be supported on this OS)")
        
        logger.info(f"Key storage initialized at: {self.keys_dir}")
    
    def generate_and_store_keys(self, key_name: str = "default") -> str:
        """
        Generate a new key pair and store it securely.
        
        Args:
            key_name: Name identifier for this key pair (default: "default")
        
        Returns:
            str: Key ID (identifier for the public key)
        
        Raises:
            FileExistsError: If keys with this name already exist
        """
        private_key_path = self.keys_dir / f"{key_name}_private.pem"
        public_key_path = self.keys_dir / f"{key_name}_public.pem"
        
        # Check if keys already exist
        if private_key_path.exists() or public_key_path.exists():
            raise FileExistsError(
                f"Keys with name '{key_name}' already exist. "
                "Use a different name or delete existing keys first."
            )
        
        # Generate new key pair
        private_key_bytes, public_key_bytes = CryptoService.generate_keys()
        
        # Store private key with restricted permissions
        private_key_path.write_bytes(private_key_bytes)
        try:
            os.chmod(private_key_path, stat.S_IRUSR | stat.S_IWUSR)  # 600 permissions
        except (OSError, AttributeError):
            logger.warning(f"Could not set file permissions for {private_key_path}")
        
        # Store public key (can be world-readable)
        public_key_path.write_bytes(public_key_bytes)
        
        # Generate key ID
        key_id = CryptoService.get_key_id(public_key_bytes)
        
        logger.info(f"Generated and stored key pair '{key_name}' with ID: {key_id}")
        
        return key_id
    
    def load_private_key(self, key_name: str = "default") -> bytes:
        """
        Load private key from storage.
        
        SECURITY WARNING: This method should ONLY be called by internal services.
        NEVER expose private keys via API endpoints.
        
        Args:
            key_name: Name identifier for the key pair
        
        Returns:
            bytes: PEM-encoded private key
        
        Raises:
            FileNotFoundError: If private key doesn't exist
        """
        private_key_path = self.keys_dir / f"{key_name}_private.pem"
        
        if not private_key_path.exists():
            raise FileNotFoundError(
                f"Private key '{key_name}' not found. "
                "Generate keys first using generate_and_store_keys()"
            )
        
        logger.debug(f"Loading private key: {key_name}")
        return private_key_path.read_bytes()
    
    def load_public_key(self, key_name: str = "default") -> bytes:
        """
        Load public key from storage.
        
        Public keys are safe to expose and can be used for signature verification.
        
        Args:
            key_name: Name identifier for the key pair
        
        Returns:
            bytes: PEM-encoded public key
        
        Raises:
            FileNotFoundError: If public key doesn't exist
        """
        public_key_path = self.keys_dir / f"{key_name}_public.pem"
        
        if not public_key_path.exists():
            raise FileNotFoundError(
                f"Public key '{key_name}' not found. "
                "Generate keys first using generate_and_store_keys()"
            )
        
        logger.debug(f"Loading public key: {key_name}")
        return public_key_path.read_bytes()
    
    def get_key_id(self, key_name: str = "default") -> str:
        """
        Get the key ID for a stored public key.
        
        Args:
            key_name: Name identifier for the key pair
        
        Returns:
            str: Key ID (hex-encoded hash)
        
        Raises:
            FileNotFoundError: If public key doesn't exist
        """
        public_key_bytes = self.load_public_key(key_name)
        return CryptoService.get_key_id(public_key_bytes)
    
    def key_exists(self, key_name: str = "default") -> bool:
        """
        Check if a key pair exists in storage.
        
        Args:
            key_name: Name identifier for the key pair
        
        Returns:
            bool: True if both private and public keys exist
        """
        private_key_path = self.keys_dir / f"{key_name}_private.pem"
        public_key_path = self.keys_dir / f"{key_name}_public.pem"
        
        return private_key_path.exists() and public_key_path.exists()
    
    def list_keys(self) -> list[str]:
        """
        List all stored key pairs.
        
        Returns:
            list[str]: List of key names
        """
        key_names = set()
        
        for file_path in self.keys_dir.glob("*_private.pem"):
            key_name = file_path.stem.replace("_private", "")
            # Only include if both private and public keys exist
            if self.key_exists(key_name):
                key_names.add(key_name)
        
        return sorted(list(key_names))
    
    def delete_keys(self, key_name: str) -> bool:
        """
        Delete a key pair from storage.
        
        CAUTION: This operation cannot be undone. Any signatures created with
        this key will still be verifiable if the public key is stored elsewhere.
        
        Args:
            key_name: Name identifier for the key pair to delete
        
        Returns:
            bool: True if keys were deleted, False if they didn't exist
        """
        private_key_path = self.keys_dir / f"{key_name}_private.pem"
        public_key_path = self.keys_dir / f"{key_name}_public.pem"
        
        deleted = False
        
        if private_key_path.exists():
            private_key_path.unlink()
            deleted = True
            logger.warning(f"Deleted private key: {key_name}")
        
        if public_key_path.exists():
            public_key_path.unlink()
            deleted = True
            logger.warning(f"Deleted public key: {key_name}")
        
        return deleted


def get_key_storage(keys_directory: Optional[str] = None) -> KeyStorageService:
    """
    Factory function to get a KeyStorageService instance.
    
    Args:
        keys_directory: Optional path to keys directory. If None, uses default from config.
    
    Returns:
        KeyStorageService: Configured key storage service
    """
    if keys_directory is None:
        # Import here to avoid circular dependency
        from flask import current_app
        keys_directory = current_app.config.get('KEYS_DIR', './keys')
    
    return KeyStorageService(keys_directory)
