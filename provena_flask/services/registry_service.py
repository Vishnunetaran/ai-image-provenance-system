"""
Registry Service Layer for Provena.

Provides high-level interface for provenance registry operations.
Enforces append-only semantics and integrates with cryptographic services.
"""

import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
import logging

from provena_flask.models.provenance import ProvenanceDatabase

logger = logging.getLogger(__name__)


class RegistryService:
    """
    High-level service for provenance registry operations.
    
    This service layer:
    - Enforces append-only semantics
    - Validates data before insertion
    - Provides business logic for record management
    - Integrates with cryptographic services
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize registry service.
        
        Args:
            db_path: Optional path to database file
        """
        self.db = ProvenanceDatabase(db_path) if db_path else None
        logger.info("Registry service initialized")
    
    def _get_db(self) -> ProvenanceDatabase:
        """Get database instance (lazy initialization for Flask context)."""
        if self.db is None:
            from provena_flask.models.provenance import get_database
            self.db = get_database()
        return self.db
    
    def register_provenance(
        self,
        image_id: str,
        model_id: str,
        timestamp: str,
        watermark_payload: bytes,
        perceptual_hash: str,
        signature: bytes,
        public_key: bytes,
        key_id: str,
        prompt_hash: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Register a new provenance record.
        
        This is the primary method for adding records to the registry.
        
        Args:
            image_id: Unique image identifier (UUID recommended)
            model_id: AI model identifier
            timestamp: ISO 8601 timestamp
            watermark_payload: Binary watermark data
            perceptual_hash: Perceptual hash string
            signature: Cryptographic signature
            public_key: Public key for verification
            key_id: Key identifier
            prompt_hash: Optional hash of generation prompt
        
        Returns:
            dict: Registration result with status and details
        
        Example:
            >>> result = registry.register_provenance(
            ...     image_id="img-abc123",
            ...     model_id="gpt-vision-v1",
            ...     timestamp="2026-01-26T13:00:00Z",
            ...     watermark_payload=b"...",
            ...     perceptual_hash="abc123...",
            ...     signature=b"...",
            ...     public_key=b"...",
            ...     key_id="key-xyz"
            ... )
            >>> print(result['status'])  # 'success'
        """
        # Validate inputs
        validation_result = self._validate_registration_data(
            image_id, model_id, timestamp, watermark_payload,
            perceptual_hash, signature, public_key, key_id
        )
        
        if not validation_result['valid']:
            return {
                'status': 'error',
                'message': validation_result['error'],
                'image_id': image_id
            }
        
        # Prepare record
        record = {
            'image_id': image_id,
            'model_id': model_id,
            'timestamp': timestamp,
            'prompt_hash': prompt_hash,
            'watermark_payload': watermark_payload,
            'perceptual_hash': perceptual_hash,
            'signature': signature,
            'public_key': public_key,
            'key_id': key_id
        }
        
        # Insert into database
        db = self._get_db()
        success = db.insert_record(record)
        
        if success:
            logger.info(f"Successfully registered provenance: {image_id}")
            return {
                'status': 'success',
                'message': 'Provenance record registered',
                'image_id': image_id,
                'timestamp': datetime.utcnow().isoformat()
            }
        else:
            logger.warning(f"Failed to register provenance (duplicate?): {image_id}")
            return {
                'status': 'error',
                'message': 'Record already exists or constraint violation',
                'image_id': image_id
            }
    
    def _validate_registration_data(
        self,
        image_id: str,
        model_id: str,
        timestamp: str,
        watermark_payload: bytes,
        perceptual_hash: str,
        signature: bytes,
        public_key: bytes,
        key_id: str
    ) -> Dict[str, Any]:
        """
        Validate registration data.
        
        Returns:
            dict: {'valid': bool, 'error': str}
        """
        # Validate image_id format (should be UUID-like)
        if not image_id or len(image_id) < 8:
            return {'valid': False, 'error': 'Invalid image_id format'}
        
        # Validate model_id
        if not model_id or len(model_id) < 3:
            return {'valid': False, 'error': 'Invalid model_id'}
        
        # Validate timestamp format
        try:
            datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return {'valid': False, 'error': 'Invalid timestamp format (use ISO 8601)'}
        
        # Validate watermark payload
        # Hybrid watermark uses a 6-byte codec-encoded payload (4-byte
        # record_id + 2-byte Reed-Solomon ECC). Older callers used a 16-byte
        # raw payload; both are valid.
        if not isinstance(watermark_payload, bytes) or len(watermark_payload) < 6:
            return {'valid': False, 'error': 'Invalid watermark payload (must be bytes, ≥6 bytes)'}
        
        # Validate perceptual hash
        if not perceptual_hash or len(perceptual_hash) < 8:
            return {'valid': False, 'error': 'Invalid perceptual hash'}
        
        # Validate signature (Ed25519 = 64 bytes)
        if not isinstance(signature, bytes) or len(signature) != 64:
            return {'valid': False, 'error': 'Invalid signature (must be 64 bytes for Ed25519)'}
        
        # Validate public key
        if not isinstance(public_key, bytes) or len(public_key) < 50:
            return {'valid': False, 'error': 'Invalid public key'}
        
        # Validate key_id
        if not key_id or len(key_id) < 8:
            return {'valid': False, 'error': 'Invalid key_id'}
        
        return {'valid': True}
    
    def get_provenance(self, image_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve provenance record by image ID.
        
        Args:
            image_id: Unique image identifier
        
        Returns:
            dict: Provenance record or None if not found
        """
        db = self._get_db()
        record = db.get_record_by_image_id(image_id)
        
        if record:
            logger.debug(f"Retrieved provenance: {image_id}")
        else:
            logger.debug(f"Provenance not found: {image_id}")
        
        return record
    
    def list_all_records(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """
        List provenance records with pagination.
        
        Args:
            limit: Maximum number of records (default: 100, max: 1000)
            offset: Number of records to skip
        
        Returns:
            list[dict]: List of provenance records
        """
        # Enforce maximum limit
        limit = min(limit, 1000)
        
        db = self._get_db()
        records = db.list_records(limit=limit, offset=offset)
        
        logger.debug(f"Listed {len(records)} records (limit={limit}, offset={offset})")
        
        return records
    
    def search_by_model(self, model_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Search provenance records by model ID.
        
        Args:
            model_id: AI model identifier
            limit: Maximum number of records
        
        Returns:
            list[dict]: Matching records
        """
        db = self._get_db()
        records = db.search_by_model_id(model_id, limit=limit)
        
        logger.debug(f"Found {len(records)} records for model: {model_id}")
        
        return records
    
    def search_by_watermark_payload(self, payload: bytes) -> Optional[Dict[str, Any]]:
        """Find a single record whose watermark_payload matches exactly."""
        db = self._get_db()
        record = db.search_by_watermark_payload(payload)
        if record:
            logger.debug(f"Found record by watermark_payload: {record.get('image_id')}")
        return record

    def search_by_perceptual_hash(self, perceptual_hash: str, max_distance: int = 0) -> List[Dict[str, Any]]:
        """
        Search records by perceptual hash with optional Hamming-distance tolerance.

        Args:
            perceptual_hash: Perceptual hash string (hex)
            max_distance: Maximum Hamming distance (bits). 0 = exact match.

        Returns:
            list[dict]: Matching records (sorted ascending by distance when tolerant).
        """
        db = self._get_db()
        records = db.search_by_perceptual_hash(perceptual_hash, max_distance=max_distance)

        logger.debug(f"Found {len(records)} records for perceptual hash (max_distance={max_distance})")

        return records
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get registry statistics.
        
        Returns:
            dict: Statistics including total records, models, etc.
        """
        db = self._get_db()
        
        total_records = db.count_records()
        integrity = db.verify_integrity()
        
        return {
            'total_records': total_records,
            'integrity_valid': integrity['is_valid'],
            'duplicates': integrity['duplicates'],
            'null_fields': integrity['null_required_fields']
        }
    
    def verify_registry_integrity(self) -> Dict[str, Any]:
        """
        Verify registry integrity.
        
        Returns:
            dict: Integrity check results
        """
        db = self._get_db()
        return db.verify_integrity()
    
    def get_audit_trail(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Retrieve audit log entries.
        
        Args:
            limit: Maximum number of entries
        
        Returns:
            list[dict]: Audit log entries
        """
        db = self._get_db()
        return db.get_audit_log(limit=limit)
    
    def generate_image_id(self) -> str:
        """
        Generate a unique image ID.
        
        Returns:
            str: UUID-based image identifier
        """
        return f"img-{uuid.uuid4()}"
    
    def record_exists(self, image_id: str) -> bool:
        """
        Check if a record exists for given image ID.
        
        Args:
            image_id: Image identifier
        
        Returns:
            bool: True if record exists
        """
        record = self.get_provenance(image_id)
        return record is not None


def get_registry_service(db_path: Optional[str] = None) -> RegistryService:
    """
    Factory function to get registry service instance.
    
    Args:
        db_path: Optional database path
    
    Returns:
        RegistryService: Registry service instance
    """
    return RegistryService(db_path)
