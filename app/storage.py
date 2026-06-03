"""
Store Intelligence — Persistent Storage Layer (PS3f Aligned)

AI-Assisted Decision: SQLite chosen over PostgreSQL/Redis for pragmatic reasons —
single-binary deployment, zero external dependencies, survives docker compose up
from a clean clone. The challenge rewards functional correctness over infrastructure
complexity. SQLite handles the data volume with zero configuration.

Edit Rationale: Updated schema to support PS3f enriched event metadata including
gender, age, zone_type, is_revenue_zone, queue wait times, group tracking, and
per-camera health monitoring. Added multi-store support and cross-camera dedup table.

# CHANGES MADE:
# - Added enriched metadata columns to events table
# - Added camera_health tracking table
# - Added cross_camera_links table for deduplication
# - Updated sessions table with group/occlusion fields
# - Updated transactions table for PS3f POS schema
# - Added store_layouts table
# - Added per-camera event queries
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Optional

from app.logging_config import logger
from app.models import (
    AnomalyRecord,
    POSTransaction,
    StoreEvent,
    VisitorSession,
)

DB_PATH = os.environ.get("SI_DB_PATH", "data/store_intelligence.db")


def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)


def get_connection() -> sqlite3.Connection:
    """Get a new SQLite connection with WAL mode for concurrency."""
    _ensure_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create all required tables if they don't exist."""
    conn = get_connection()
    try:
        conn.executescript(
            """
            -- Events table (PS3f aligned)
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                store_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                visitor_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                zone_id TEXT,
                dwell_ms INTEGER DEFAULT 0,
                is_staff INTEGER DEFAULT 0,
                confidence REAL DEFAULT 0.0,
                metadata TEXT DEFAULT '{}',
                ingested_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            -- Idempotency tracking
            CREATE TABLE IF NOT EXISTS ingest_idempotency (
                event_id TEXT PRIMARY KEY,
                ingested_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            -- Sessions table (enriched for PS3f edge cases)
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                store_id TEXT NOT NULL,
                visitor_id TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                is_staff INTEGER DEFAULT 0,
                zones_visited TEXT DEFAULT '[]',
                entered_billing INTEGER DEFAULT 0,
                converted INTEGER DEFAULT 0,
                total_dwell_ms INTEGER DEFAULT 0,
                event_count INTEGER DEFAULT 0,
                is_reentry INTEGER DEFAULT 0,
                group_id TEXT,
                group_size INTEGER,
                gender_pred TEXT,
                age_bucket TEXT,
                has_partial_occlusion INTEGER DEFAULT 0,
                cross_camera_linked INTEGER DEFAULT 0,
                queue_wait_seconds INTEGER,
                queue_abandoned INTEGER DEFAULT 0
            );

            -- Session zone state
            CREATE TABLE IF NOT EXISTS session_zone_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                zone_id TEXT NOT NULL,
                zone_name TEXT,
                zone_type TEXT,
                entered_at TEXT,
                exited_at TEXT,
                dwell_ms INTEGER DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );

            -- POS transactions (PS3f schema)
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                store_id TEXT NOT NULL,
                order_date TEXT,
                order_time TEXT,
                product_id TEXT,
                brand_name TEXT,
                total_amount REAL DEFAULT 0.0,
                store_name TEXT,
                customer_name TEXT,
                invoice_number TEXT,
                timestamp TEXT
            );

            -- Store daily metrics cache
            CREATE TABLE IF NOT EXISTS store_daily_metrics (
                store_id TEXT NOT NULL,
                date TEXT NOT NULL,
                unique_visitors INTEGER DEFAULT 0,
                converted_visitors INTEGER DEFAULT 0,
                conversion_rate REAL DEFAULT 0.0,
                avg_dwell_ms REAL DEFAULT 0.0,
                queue_depth_max INTEGER DEFAULT 0,
                abandonment_count INTEGER DEFAULT 0,
                total_sessions INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (store_id, date)
            );

            -- Anomalies
            CREATE TABLE IF NOT EXISTS anomalies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id TEXT NOT NULL,
                type TEXT NOT NULL,
                severity TEXT NOT NULL,
                evidence TEXT,
                current_value REAL,
                baseline_value REAL,
                suggested_action TEXT,
                detected_at TEXT DEFAULT CURRENT_TIMESTAMP,
                resolved INTEGER DEFAULT 0
            );

            -- Cross-camera visitor links (deduplication)
            CREATE TABLE IF NOT EXISTS cross_camera_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id TEXT NOT NULL,
                visitor_id_a TEXT NOT NULL,
                camera_id_a TEXT NOT NULL,
                visitor_id_b TEXT NOT NULL,
                camera_id_b TEXT NOT NULL,
                confidence REAL DEFAULT 0.0,
                linked_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            -- Store layouts
            CREATE TABLE IF NOT EXISTS store_layouts (
                store_id TEXT PRIMARY KEY,
                layout_json TEXT DEFAULT '{}',
                loaded_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            -- Camera health monitoring
            CREATE TABLE IF NOT EXISTS camera_health (
                store_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                last_event_at TEXT,
                event_count INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (store_id, camera_id)
            );

            -- Indexes for query performance
            CREATE INDEX IF NOT EXISTS idx_events_store ON events(store_id);
            CREATE INDEX IF NOT EXISTS idx_events_visitor ON events(visitor_id);
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_camera ON events(camera_id);
            CREATE INDEX IF NOT EXISTS idx_events_store_camera ON events(store_id, camera_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_store ON sessions(store_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_visitor ON sessions(visitor_id);
            CREATE INDEX IF NOT EXISTS idx_transactions_store ON transactions(store_id);
            CREATE INDEX IF NOT EXISTS idx_transactions_timestamp ON transactions(timestamp);
            CREATE INDEX IF NOT EXISTS idx_cross_camera_store ON cross_camera_links(store_id);
            """
        )
        conn.commit()
        logger.info("Database initialized", extra={"db_path": DB_PATH})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Event operations
# ---------------------------------------------------------------------------

def is_duplicate_event(conn: sqlite3.Connection, event_id: str) -> bool:
    """Check if an event_id already exists (idempotency check)."""
    row = conn.execute(
        "SELECT 1 FROM ingest_idempotency WHERE event_id = ?", (event_id,)
    ).fetchone()
    return row is not None


def insert_event(conn: sqlite3.Connection, event: StoreEvent) -> bool:
    """Insert an event and mark it in the idempotency table. Returns False if duplicate."""
    if is_duplicate_event(conn, event.event_id):
        return False

    metadata_json = event.metadata.model_dump_json() if event.metadata else "{}"
    conn.execute(
        """INSERT INTO events
           (event_id, store_id, camera_id, visitor_id, event_type,
            timestamp, zone_id, dwell_ms, is_staff, confidence, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event.event_id,
            event.store_id,
            event.camera_id,
            event.visitor_id,
            event.event_type.value,
            event.timestamp.isoformat(),
            event.zone_id,
            event.dwell_ms or 0,
            1 if event.is_staff else 0,
            event.confidence,
            metadata_json,
        ),
    )
    conn.execute(
        "INSERT INTO ingest_idempotency (event_id) VALUES (?)",
        (event.event_id,),
    )

    # Update camera health
    _update_camera_health(conn, event.store_id, event.camera_id, event.timestamp.isoformat())

    return True


def _update_camera_health(
    conn: sqlite3.Connection, store_id: str, camera_id: str, timestamp: str
) -> None:
    """Update the camera health tracking table."""
    conn.execute(
        """INSERT INTO camera_health (store_id, camera_id, last_event_at, event_count, updated_at)
           VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
           ON CONFLICT(store_id, camera_id) DO UPDATE SET
               last_event_at = MAX(last_event_at, excluded.last_event_at),
               event_count = event_count + 1,
               updated_at = CURRENT_TIMESTAMP""",
        (store_id, camera_id, timestamp),
    )


def get_events_by_store(
    conn: sqlite3.Connection,
    store_id: str,
    since: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Fetch events for a store, optionally filtered by timestamp."""
    if since:
        rows = conn.execute(
            "SELECT * FROM events WHERE store_id = ? AND timestamp >= ? ORDER BY timestamp",
            (store_id, since),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM events WHERE store_id = ? ORDER BY timestamp",
            (store_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_events_by_store_camera(
    conn: sqlite3.Connection, store_id: str, camera_id: str
) -> list[dict[str, Any]]:
    """Fetch events for a specific store + camera combination."""
    rows = conn.execute(
        "SELECT * FROM events WHERE store_id = ? AND camera_id = ? ORDER BY timestamp",
        (store_id, camera_id),
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_event_timestamp(
    conn: sqlite3.Connection, store_id: str
) -> Optional[str]:
    """Get the most recent event timestamp for a store."""
    row = conn.execute(
        "SELECT MAX(timestamp) as ts FROM events WHERE store_id = ?",
        (store_id,),
    ).fetchone()
    return row["ts"] if row and row["ts"] else None


def get_all_store_ids(conn: sqlite3.Connection) -> list[str]:
    """Get all distinct store IDs from events."""
    rows = conn.execute("SELECT DISTINCT store_id FROM events").fetchall()
    return [r["store_id"] for r in rows]


def get_camera_ids_for_store(conn: sqlite3.Connection, store_id: str) -> list[str]:
    """Get all camera IDs for a given store."""
    rows = conn.execute(
        "SELECT DISTINCT camera_id FROM events WHERE store_id = ?",
        (store_id,),
    ).fetchall()
    return [r["camera_id"] for r in rows]


def get_camera_health(conn: sqlite3.Connection, store_id: str) -> list[dict[str, Any]]:
    """Get camera health records for a store."""
    rows = conn.execute(
        "SELECT * FROM camera_health WHERE store_id = ?",
        (store_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Session operations
# ---------------------------------------------------------------------------

def upsert_session(conn: sqlite3.Connection, session: VisitorSession) -> None:
    """Insert or update a visitor session."""
    zones_json = json.dumps(session.zones_visited)
    conn.execute(
        """INSERT OR REPLACE INTO sessions
           (session_id, store_id, visitor_id, started_at, ended_at,
            is_staff, zones_visited, entered_billing, converted,
            total_dwell_ms, event_count, is_reentry,
            group_id, group_size, gender_pred, age_bucket,
            has_partial_occlusion, cross_camera_linked,
            queue_wait_seconds, queue_abandoned)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session.session_id,
            session.store_id,
            session.visitor_id,
            session.started_at.isoformat() if session.started_at else None,
            session.ended_at.isoformat() if session.ended_at else None,
            1 if session.is_staff else 0,
            zones_json,
            1 if session.entered_billing else 0,
            1 if session.converted else 0,
            session.total_dwell_ms,
            session.event_count,
            1 if session.is_reentry else 0,
            session.group_id,
            session.group_size,
            session.gender_pred,
            session.age_bucket,
            1 if session.has_partial_occlusion else 0,
            1 if session.cross_camera_linked else 0,
            session.queue_wait_seconds,
            1 if session.queue_abandoned else 0,
        ),
    )


def get_sessions_by_store(
    conn: sqlite3.Connection, store_id: str
) -> list[dict[str, Any]]:
    """Get all sessions for a store."""
    rows = conn.execute(
        "SELECT * FROM sessions WHERE store_id = ? AND is_staff = 0",
        (store_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_sessions_by_store(
    conn: sqlite3.Connection, store_id: str
) -> list[dict[str, Any]]:
    """Get ALL sessions for a store including staff."""
    rows = conn.execute(
        "SELECT * FROM sessions WHERE store_id = ?",
        (store_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Transaction operations
# ---------------------------------------------------------------------------

def insert_transaction(conn: sqlite3.Connection, txn: POSTransaction) -> None:
    """Insert a POS transaction."""
    conn.execute(
        """INSERT INTO transactions
           (order_id, store_id, order_date, order_time,
            product_id, brand_name, total_amount,
            store_name, customer_name, invoice_number, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            txn.order_id,
            txn.store_id,
            txn.order_date,
            txn.order_time,
            txn.product_id,
            txn.brand_name,
            txn.total_amount,
            txn.store_name,
            txn.customer_name,
            txn.invoice_number,
            txn.timestamp.isoformat() if txn.timestamp else None,
        ),
    )


def get_transactions_by_store(
    conn: sqlite3.Connection, store_id: str
) -> list[dict[str, Any]]:
    """Get all POS transactions for a store."""
    rows = conn.execute(
        "SELECT * FROM transactions WHERE store_id = ?",
        (store_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_transaction_count_by_store(conn: sqlite3.Connection, store_id: str) -> int:
    """Get count of unique orders for a store."""
    row = conn.execute(
        "SELECT COUNT(DISTINCT order_id) as cnt FROM transactions WHERE store_id = ?",
        (store_id,),
    ).fetchone()
    return row["cnt"] if row else 0


def get_transaction_revenue_by_store(conn: sqlite3.Connection, store_id: str) -> float:
    """Get total revenue for a store."""
    row = conn.execute(
        "SELECT SUM(total_amount) as total FROM transactions WHERE store_id = ?",
        (store_id,),
    ).fetchone()
    return row["total"] if row and row["total"] else 0.0


# ---------------------------------------------------------------------------
# Cross-camera link operations
# ---------------------------------------------------------------------------

def insert_cross_camera_link(
    conn: sqlite3.Connection,
    store_id: str,
    visitor_id_a: str,
    camera_id_a: str,
    visitor_id_b: str,
    camera_id_b: str,
    confidence: float,
) -> None:
    """Record a cross-camera visitor identity link."""
    conn.execute(
        """INSERT INTO cross_camera_links
           (store_id, visitor_id_a, camera_id_a, visitor_id_b, camera_id_b, confidence)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (store_id, visitor_id_a, camera_id_a, visitor_id_b, camera_id_b, confidence),
    )


def get_linked_visitors(
    conn: sqlite3.Connection, store_id: str, visitor_id: str
) -> list[str]:
    """Get all visitor IDs linked to this one via cross-camera matching."""
    rows = conn.execute(
        """SELECT visitor_id_b FROM cross_camera_links
           WHERE store_id = ? AND visitor_id_a = ?
           UNION
           SELECT visitor_id_a FROM cross_camera_links
           WHERE store_id = ? AND visitor_id_b = ?""",
        (store_id, visitor_id, store_id, visitor_id),
    ).fetchall()
    return [r["visitor_id_b"] if "visitor_id_b" in r.keys() else r["visitor_id_a"] for r in rows]


# ---------------------------------------------------------------------------
# Store layout operations
# ---------------------------------------------------------------------------

def save_store_layout(conn: sqlite3.Connection, store_id: str, layout_json: str) -> None:
    """Save or update a store layout."""
    conn.execute(
        """INSERT OR REPLACE INTO store_layouts (store_id, layout_json, loaded_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)""",
        (store_id, layout_json),
    )


def get_store_layout(conn: sqlite3.Connection, store_id: str) -> Optional[str]:
    """Get the layout JSON for a store."""
    row = conn.execute(
        "SELECT layout_json FROM store_layouts WHERE store_id = ?",
        (store_id,),
    ).fetchone()
    return row["layout_json"] if row else None


# ---------------------------------------------------------------------------
# Anomaly operations
# ---------------------------------------------------------------------------

def insert_anomaly(
    conn: sqlite3.Connection, store_id: str, anomaly: AnomalyRecord
) -> None:
    """Insert a detected anomaly."""
    conn.execute(
        """INSERT INTO anomalies
           (store_id, type, severity, evidence, current_value,
            baseline_value, suggested_action, detected_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            store_id,
            anomaly.type.value,
            anomaly.severity.value,
            anomaly.evidence,
            anomaly.current_value,
            anomaly.baseline_value,
            anomaly.suggested_action,
            anomaly.detected_at.isoformat(),
        ),
    )


def get_active_anomalies(
    conn: sqlite3.Connection, store_id: str
) -> list[dict[str, Any]]:
    """Get unresolved anomalies for a store."""
    rows = conn.execute(
        "SELECT * FROM anomalies WHERE store_id = ? AND resolved = 0 ORDER BY detected_at DESC",
        (store_id,),
    ).fetchall()
    return [dict(r) for r in rows]
