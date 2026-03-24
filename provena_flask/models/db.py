"""
Database connection pool and migration runner — T-028 through T-040.

Provides:
  - PostgreSQL connection pool (psycopg2)
  - Redis client for caching
  - Migration runner
  - Hamming-distance search via pgvector bit_count operator
  - SQLite fallback for local development without PostgreSQL
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detect backend: PostgreSQL or SQLite
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "")  # e.g. postgresql://...
USE_POSTGRES  = DATABASE_URL.startswith("postgresql") or DATABASE_URL.startswith("postgres")

if USE_POSTGRES:
    try:
        import psycopg2
        import psycopg2.pool
        import psycopg2.extras
        _PG_AVAILABLE = True
    except ImportError:
        _PG_AVAILABLE = False
        logger.warning("psycopg2 not installed; falling back to SQLite")
        USE_POSTGRES = False
else:
    _PG_AVAILABLE = False


# ---------------------------------------------------------------------------
# Redis client
# ---------------------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

try:
    import redis as _redis_lib
    _redis_client = _redis_lib.from_url(REDIS_URL, decode_responses=False)
    # Ping to verify it's actually alive
    _redis_client.ping()
    _REDIS_AVAILABLE = True
except Exception:
    _redis_client = None
    _REDIS_AVAILABLE = False
    logger.info("Redis not available; caching disabled")


# ---------------------------------------------------------------------------
# Connection pool (PostgreSQL)
# ---------------------------------------------------------------------------
_pg_pool: Optional[object] = None     # psycopg2 ThreadedConnectionPool


def _init_pg_pool() -> None:
    global _pg_pool
    if _pg_pool is None and USE_POSTGRES and _PG_AVAILABLE:
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=20,
            dsn=DATABASE_URL,
        )
        logger.info("PostgreSQL connection pool initialised (min=2, max=20)")


@contextmanager
def get_pg_connection() -> Generator:
    """Context manager: acquire / release a connection from the pool."""
    _init_pg_pool()
    conn = _pg_pool.getconn()   # type: ignore[union-attr]
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pg_pool.putconn(conn)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# SQLite fallback
# ---------------------------------------------------------------------------
SQLITE_PATH = os.environ.get("SQLITE_PATH", "data/provenance.db")


@contextmanager
def get_sqlite_connection() -> Generator:
    """Context manager: SQLite connection."""
    Path(SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Unified connection context manager
# ---------------------------------------------------------------------------

@contextmanager
def get_connection() -> Generator:
    """Return the appropriate DB connection based on configuration."""
    if USE_POSTGRES and _pg_pool is not None:
        with get_pg_connection() as conn:
            yield conn
    else:
        with get_sqlite_connection() as conn:
            yield conn


# ---------------------------------------------------------------------------
# Hamming-distance search (PostgreSQL pgvector / SQLite fallback)
# ---------------------------------------------------------------------------

def find_by_hamming(phash_int: int, max_distance: int = 10) -> list[dict]:
    """
    Find provenance records whose pHash is within max_distance Hamming bits.

    Uses pgvector's bit_count(a # b) operator on PostgreSQL for O(log N)
    lookups via IVFFlat index. Falls back to a Python loop on SQLite.

    Args:
        phash_int:    64-bit integer perceptual hash.
        max_distance: Maximum Hamming distance (default 10; never set > 10).

    Returns:
        List of matching record dicts, ordered by hamming_dist ASC. Max 5 results.
    """
    if max_distance > 10:
        logger.warning(
            "find_by_hamming called with max_distance=%d > 10; clamping to 10 to avoid false positives",
            max_distance,
        )
        max_distance = 10

    # --- Redis cache ---
    if _REDIS_AVAILABLE and _redis_client:
        import pickle
        cache_key = f"phash:{phash_int}:{max_distance}"
        try:
            cached = _redis_client.get(cache_key)    # type: ignore[union-attr]
            if cached:
                logger.debug("pHash cache hit for %d", phash_int)
                return pickle.loads(cached)
        except Exception:
            pass # ignore cache errors

    if USE_POSTGRES and _pg_pool is not None:
        results = _pg_find_by_hamming(phash_int, max_distance)
    else:
        results = _sqlite_find_by_hamming(phash_int, max_distance)

    # Cache results for 60 s
    if _REDIS_AVAILABLE and _redis_client:
        import pickle
        try:
            _redis_client.setex(cache_key, 60, pickle.dumps(results))  # type: ignore[union-attr]
        except Exception:
            pass  # fail soft if Redis goes down after init

    return results


def _pg_find_by_hamming(phash_int: int, max_distance: int) -> list[dict]:
    """PostgreSQL implementation using bit_count XOR operator."""
    phash_bits = format(phash_int, "064b")
    with get_pg_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, image_phash::text AS image_phash_hex,
                       model_id, creator_did, registered_at,
                       payload_hex, manifest_id,
                       bit_count(image_phash # %s::bit(64)) AS hamming_dist
                FROM provenance_records
                WHERE bit_count(image_phash # %s::bit(64)) <= %s
                ORDER BY hamming_dist ASC
                LIMIT 5
                """,
                (phash_bits, phash_bits, max_distance),
            )
            return [dict(row) for row in cur.fetchall()]


def _sqlite_find_by_hamming(phash_int: int, max_distance: int) -> list[dict]:
    """SQLite fallback: full table scan with Python Hamming computation."""
    results = []
    with get_sqlite_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM provenance_records ORDER BY created_at DESC LIMIT 10000"
        )
        for row in cur.fetchall():
            try:
                stored_phash = int(row["image_phash_int"])
            except (ValueError, TypeError):
                continue
            xor = phash_int ^ stored_phash
            dist = bin(xor).count("1")
            if dist <= max_distance:
                record = dict(row)
                record["hamming_dist"] = dist
                results.append(record)

    results.sort(key=lambda r: r["hamming_dist"])
    return results[:5]


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

def run_migrations() -> None:
    """
    Run all SQL migration files from the migrations/ directory.

    On PostgreSQL: executes migrations/*.sql in filename order.
    On SQLite: creates the minimal equivalent schema inline.
    """
    if USE_POSTGRES and _PG_AVAILABLE:
        _run_pg_migrations()
    else:
        _init_sqlite_schema()


def _run_pg_migrations() -> None:
    """Execute SQL files from migrations/ directory."""
    migrations_dir = Path("migrations")
    if not migrations_dir.exists():
        logger.warning("migrations/ directory not found; skipping")
        return
    sql_files = sorted(migrations_dir.glob("*.sql"))
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            # Ensure migrations tracking table exists
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS applied_migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
            for sql_file in sql_files:
                cur.execute(
                    "SELECT 1 FROM applied_migrations WHERE filename = %s",
                    (sql_file.name,),
                )
                if cur.fetchone():
                    continue  # already applied
                logger.info("Applying migration: %s", sql_file.name)
                cur.execute(sql_file.read_text())
                cur.execute(
                    "INSERT INTO applied_migrations (filename) VALUES (%s)",
                    (sql_file.name,),
                )


def _init_sqlite_schema() -> None:
    """Create or update SQLite schema for local development."""
    with get_sqlite_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS provenance_records (
                id              TEXT PRIMARY KEY,
                image_phash_int TEXT,
                image_phash_hex TEXT NOT NULL,
                model_id        TEXT NOT NULL,
                creator_did     TEXT,
                registered_at   TEXT DEFAULT '',
                payload_hex     TEXT DEFAULT '',
                manifest_id     TEXT,
                api_key_id      TEXT,
                custom_fields   TEXT DEFAULT '{}',
                created_at      TEXT NOT NULL
            );
            """
        )

        # --- Schema Evolution (Legacy Cleanup) ---
        cursor = conn.cursor()
        existing_cols = [row[1] for row in cursor.execute("PRAGMA table_info(provenance_records)").fetchall()]
        
        if "image_id" in existing_cols and "id" not in existing_cols:
            logger.info("Migrating SQLite: image_id -> id")
            try:
                conn.executescript(
                    """
                    ALTER TABLE provenance_records RENAME TO old_provenance_records;
                    CREATE TABLE provenance_records (
                        id              TEXT PRIMARY KEY,
                        image_phash_int TEXT,
                        image_phash_hex TEXT NOT NULL,
                        model_id        TEXT NOT NULL,
                        creator_did     TEXT,
                        registered_at   TEXT DEFAULT '',
                        payload_hex     TEXT DEFAULT '',
                        manifest_id     TEXT,
                        api_key_id      TEXT,
                        custom_fields   TEXT DEFAULT '{}',
                        created_at      TEXT NOT NULL
                    );
                    INSERT INTO provenance_records (id, model_id, creator_did, registered_at, created_at, image_phash_hex, custom_fields)
                    SELECT image_id, model_id, creator_did, timestamp, created_at, perceptual_hash, '{}'
                    FROM old_provenance_records;
                    DROP TABLE old_provenance_records;
                    """
                )
                existing_cols = ["id", "image_phash_int", "image_phash_hex", "model_id", "creator_did", "registered_at", "payload_hex", "manifest_id", "api_key_id", "custom_fields", "created_at"]
            except Exception as exc:
                logger.warning("Migration failed: %s", exc)

        required_evolutions = {
            "image_phash_int": "TEXT",
            "registered_at":   "TEXT",
            "payload_hex":     "TEXT",
            "api_key_id":      "TEXT",
            "custom_fields":   "TEXT DEFAULT '{}'",
        }
        
        for col, col_type in required_evolutions.items():
            if col not in existing_cols:
                logger.info("Evolving SQLite schema: adding column %s", col)
                try:
                    conn.execute(f"ALTER TABLE provenance_records ADD COLUMN {col} {col_type}")
                except Exception as exc:
                    logger.warning("Failed to add column %s: %s", col, exc)

        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_prov_phash
                ON provenance_records(image_phash_int);

            CREATE TABLE IF NOT EXISTS manifests (
                id          TEXT PRIMARY KEY,
                c2pa_json   TEXT NOT NULL,
                signature   BLOB,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS api_keys (
                id           TEXT PRIMARY KEY,
                key_hash     TEXT UNIQUE NOT NULL,
                org_name     TEXT,
                tier         TEXT DEFAULT 'free',
                is_active    INTEGER DEFAULT 1,
                daily_register_limit  INTEGER DEFAULT 100,
                daily_verify_limit    INTEGER DEFAULT 500,
                created_at   TEXT NOT NULL,
                last_used_at TEXT
            );

            CREATE TABLE IF NOT EXISTS verification_log (
                id              TEXT PRIMARY KEY,
                record_id       TEXT,
                api_key_id      TEXT,
                queried_at      TEXT NOT NULL,
                hamming_distance INTEGER,
                status_returned TEXT NOT NULL,
                confidence      REAL,
                request_id      TEXT
            );
            """
        )
    logger.info("SQLite schema initialised at %s", SQLITE_PATH)


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------

def cache_invalidate_phash(phash_int: int) -> None:
    """Invalidate cached Hamming search results when a new record is inserted."""
    if not (_REDIS_AVAILABLE and _redis_client):
        return
    # Scan for matching keys and delete (small list; pattern scan acceptable)
    pattern = f"phash:{phash_int}:*"
    try:
        for key in _redis_client.scan_iter(pattern):  # type: ignore[union-attr]
            _redis_client.delete(key)                  # type: ignore[union-attr]
    except Exception as exc:
        logger.warning("Cache invalidation failed: %s", exc)
