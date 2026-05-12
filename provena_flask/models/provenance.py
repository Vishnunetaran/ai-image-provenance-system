"""
Provenance Data Model for Provena.

Defines the SQLite schema for the append-only provenance registry.
This model stores cryptographically signed metadata for AI-generated images.
"""

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class ProvenanceDatabase:
    """
    SQLite database for append-only provenance records.
    
    Design Principles:
    - Append-only: No UPDATE or DELETE operations allowed
    - Tamper-evident: All writes are logged
    - Indexed: Fast lookups by image_id
    - Cryptographically bound: Each record includes signature
    """
    
    SCHEMA_VERSION = 1
    
    # SQL schema definition
    CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS provenance_records (
        -- Primary identifier
        image_id TEXT PRIMARY KEY NOT NULL,
        
        -- Generation metadata
        model_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        prompt_hash TEXT,
        
        -- Watermark data
        watermark_payload BLOB NOT NULL,
        
        -- Perceptual hash for tolerant matching
        perceptual_hash TEXT NOT NULL,
        
        -- Cryptographic binding
        signature BLOB NOT NULL,
        public_key BLOB NOT NULL,
        key_id TEXT NOT NULL,
        
        -- Audit fields
        created_at TEXT NOT NULL,
        
        -- Ensure no duplicate timestamps for same model
        UNIQUE(model_id, timestamp)
    );
    """
    
    CREATE_INDEX_SQL = """
    CREATE INDEX IF NOT EXISTS idx_image_id ON provenance_records(image_id);
    CREATE INDEX IF NOT EXISTS idx_model_id ON provenance_records(model_id);
    CREATE INDEX IF NOT EXISTS idx_timestamp ON provenance_records(timestamp);
    CREATE INDEX IF NOT EXISTS idx_key_id ON provenance_records(key_id);
    CREATE INDEX IF NOT EXISTS idx_perceptual_hash ON provenance_records(perceptual_hash);
    """
    
    # Audit log table
    CREATE_AUDIT_LOG_SQL = """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation TEXT NOT NULL,
        image_id TEXT,
        timestamp TEXT NOT NULL,
        details TEXT
    );
    """
    
    # Schema version tracking
    CREATE_METADATA_SQL = """
    CREATE TABLE IF NOT EXISTS metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """
    
    def __init__(self, db_path: str):
        """
        Initialize provenance database.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize database
        self._init_database()
        
        logger.info(f"Provenance database initialized at: {self.db_path}")
    
    def _init_database(self):
        """Initialize database schema if not exists."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create tables
            cursor.execute(self.CREATE_TABLE_SQL)
            cursor.execute(self.CREATE_AUDIT_LOG_SQL)
            cursor.execute(self.CREATE_METADATA_SQL)
            
            # Create indices
            for index_sql in self.CREATE_INDEX_SQL.split(';'):
                if index_sql.strip():
                    cursor.execute(index_sql)
            
            # Set schema version
            cursor.execute(
                "INSERT OR IGNORE INTO metadata (key, value) VALUES (?, ?)",
                ("schema_version", str(self.SCHEMA_VERSION))
            )
            
            conn.commit()
            
            logger.info("Database schema initialized successfully")
    
    def get_connection(self) -> sqlite3.Connection:
        """
        Get database connection.
        
        Returns:
            sqlite3.Connection: Database connection
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        return conn
    
    def _log_audit(self, conn: sqlite3.Connection, operation: str, 
                   image_id: Optional[str] = None, details: Optional[str] = None):
        """
        Log operation to audit trail.
        
        Args:
            conn: Database connection
            operation: Operation type (INSERT, SELECT, etc.)
            image_id: Optional image ID
            details: Optional operation details
        """
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audit_log (operation, image_id, timestamp, details)
            VALUES (?, ?, ?, ?)
            """,
            (operation, image_id, datetime.utcnow().isoformat(), details)
        )
    
    def insert_record(self, record: dict) -> bool:
        """
        Insert a new provenance record.
        
        CRITICAL: This is the ONLY write operation allowed.
        No UPDATE or DELETE operations are permitted.
        
        Args:
            record: Dictionary containing all required fields:
                - image_id: Unique image identifier (UUID)
                - model_id: AI model identifier
                - timestamp: ISO 8601 timestamp
                - prompt_hash: Hash of generation prompt (optional)
                - watermark_payload: Binary watermark data
                - perceptual_hash: Perceptual hash string
                - signature: Cryptographic signature (binary)
                - public_key: Public key for verification (binary)
                - key_id: Key identifier
        
        Returns:
            bool: True if inserted successfully, False if duplicate
        
        Raises:
            ValueError: If required fields are missing
            sqlite3.IntegrityError: If constraints violated
        """
        # Validate required fields
        required_fields = [
            'image_id', 'model_id', 'timestamp', 'watermark_payload',
            'perceptual_hash', 'signature', 'public_key', 'key_id'
        ]
        
        for field in required_fields:
            if field not in record:
                raise ValueError(f"Missing required field: {field}")
        
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Insert record
                cursor.execute(
                    """
                    INSERT INTO provenance_records (
                        image_id, model_id, timestamp, prompt_hash,
                        watermark_payload, perceptual_hash,
                        signature, public_key, key_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record['image_id'],
                        record['model_id'],
                        record['timestamp'],
                        record.get('prompt_hash'),
                        record['watermark_payload'],
                        record['perceptual_hash'],
                        record['signature'],
                        record['public_key'],
                        record['key_id'],
                        datetime.utcnow().isoformat()
                    )
                )
                
                # Log to audit trail
                self._log_audit(
                    conn, 
                    'INSERT', 
                    record['image_id'],
                    f"model={record['model_id']}, key_id={record['key_id']}"
                )
                
                conn.commit()
                
                logger.info(f"Inserted provenance record: {record['image_id']}")
                return True
                
        except sqlite3.IntegrityError as e:
            logger.warning(f"Duplicate record or constraint violation: {e}")
            return False
    
    def get_record_by_image_id(self, image_id: str) -> Optional[dict]:
        """
        Retrieve provenance record by image ID.
        
        Args:
            image_id: Unique image identifier
        
        Returns:
            dict: Provenance record or None if not found
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT * FROM provenance_records WHERE image_id = ?",
                (image_id,)
            )
            
            row = cursor.fetchone()
            
            # Log audit
            self._log_audit(conn, 'SELECT', image_id)
            conn.commit()
            
            if row:
                return dict(row)
            return None
    
    def list_records(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """
        List provenance records with pagination.
        
        Args:
            limit: Maximum number of records to return
            offset: Number of records to skip
        
        Returns:
            list[dict]: List of provenance records
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT * FROM provenance_records 
                ORDER BY created_at DESC 
                LIMIT ? OFFSET ?
                """,
                (limit, offset)
            )
            
            rows = cursor.fetchall()
            
            # Log audit
            self._log_audit(conn, 'SELECT_LIST', details=f"limit={limit}, offset={offset}")
            conn.commit()
            
            return [dict(row) for row in rows]
    
    def count_records(self) -> int:
        """
        Count total number of provenance records.
        
        Returns:
            int: Total record count
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM provenance_records")
            count = cursor.fetchone()[0]
            return count
    
    def search_by_model_id(self, model_id: str, limit: int = 100) -> list[dict]:
        """
        Search records by model ID.
        
        Args:
            model_id: AI model identifier
            limit: Maximum number of records
        
        Returns:
            list[dict]: Matching records
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT * FROM provenance_records 
                WHERE model_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
                """,
                (model_id, limit)
            )
            
            rows = cursor.fetchall()
            
            self._log_audit(conn, 'SEARCH', details=f"model_id={model_id}")
            conn.commit()
            
            return [dict(row) for row in rows]
    
    def search_by_watermark_payload(self, payload: bytes) -> Optional[dict]:
        """
        Find a single record whose watermark_payload matches exactly.

        Used by the verify path to resolve a payload extracted from the
        HydraWatermark stack back to its registry record.

        Args:
            payload: 6-byte codec-encoded payload (or any length).

        Returns:
            dict | None: First matching record, or None.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM provenance_records WHERE watermark_payload = ? LIMIT 1",
                (payload,)
            )
            row = cursor.fetchone()
            self._log_audit(conn, 'SEARCH', details=f"watermark_payload[{len(payload)}b]")
            conn.commit()
            return dict(row) if row else None

    def search_by_perceptual_hash(self, perceptual_hash: str, max_distance: int = 0) -> list[dict]:
        """
        Search records by perceptual hash.

        Perceptual hashes are designed for tolerant matching — two visually
        similar images produce nearby (but not equal) hashes. A `max_distance`
        of 0 falls back to an exact-equality SQL lookup; any positive value
        scans the table and filters by Hamming distance on the hex hash.

        Args:
            perceptual_hash: Perceptual hash string (hex)
            max_distance: Maximum Hamming distance (in bits) to accept.
                          0 = exact match (fast path).

        Returns:
            list[dict]: Matching records, sorted by ascending Hamming distance
                        when max_distance > 0.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            if max_distance <= 0:
                cursor.execute(
                    "SELECT * FROM provenance_records WHERE perceptual_hash = ?",
                    (perceptual_hash,)
                )
            else:
                cursor.execute("SELECT * FROM provenance_records")

            rows = cursor.fetchall()
            self._log_audit(conn, 'SEARCH', details=f"perceptual_hash={perceptual_hash[:16]}...")
            conn.commit()

        records = [dict(row) for row in rows]

        if max_distance > 0:
            try:
                target = int(perceptual_hash, 16)
            except ValueError:
                return []
            scored = []
            for r in records:
                try:
                    other = int(r['perceptual_hash'], 16)
                except (ValueError, TypeError):
                    continue
                distance = bin(target ^ other).count('1')
                if distance <= max_distance:
                    scored.append((distance, r))
            scored.sort(key=lambda t: t[0])
            return [r for _, r in scored]

        return records
    
    def get_audit_log(self, limit: int = 100) -> list[dict]:
        """
        Retrieve audit log entries.
        
        Args:
            limit: Maximum number of entries
        
        Returns:
            list[dict]: Audit log entries
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT * FROM audit_log 
                ORDER BY timestamp DESC 
                LIMIT ?
                """,
                (limit,)
            )
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def verify_integrity(self) -> dict:
        """
        Verify database integrity.
        
        Returns:
            dict: Integrity check results
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check for duplicate image_ids
            cursor.execute(
                """
                SELECT image_id, COUNT(*) as count 
                FROM provenance_records 
                GROUP BY image_id 
                HAVING count > 1
                """
            )
            duplicates = cursor.fetchall()
            
            # Check for null required fields
            cursor.execute(
                """
                SELECT COUNT(*) FROM provenance_records 
                WHERE image_id IS NULL 
                   OR model_id IS NULL 
                   OR signature IS NULL
                """
            )
            null_count = cursor.fetchone()[0]
            
            # Get total records
            total_records = self.count_records()
            
            return {
                'total_records': total_records,
                'duplicates': len(duplicates),
                'null_required_fields': null_count,
                'is_valid': len(duplicates) == 0 and null_count == 0
            }


def get_database(db_path: Optional[str] = None) -> ProvenanceDatabase:
    """
    Factory function to get database instance.
    
    Args:
        db_path: Optional database path. If None, uses config default.
    
    Returns:
        ProvenanceDatabase: Database instance
    """
    if db_path is None:
        from flask import current_app
        db_path = current_app.config.get('DATABASE_PATH', './data/provenance.db')
    
    return ProvenanceDatabase(db_path)
