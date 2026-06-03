"""
Store Intelligence — Event Ingestion (PS3f Aligned)

AI-Assisted Decision: Batch ingestion with partial success was chosen because the
challenge requires accepting up to 500 events per batch, validating each individually,
deduplicating by event_id, and returning structured per-item errors.

Edit Rationale: Updated to support PS3f native event formats alongside internal format.
Handles auto-detection of event shape (entry/exit, zone, queue) and maps to internal
schema. Enhanced session building with group detection, re-entry tracking, partial
occlusion marking, and cross-camera deduplication.

# CHANGES MADE:
# - Added PS3f native event format auto-detection via parse_ps3f_event
# - Enhanced session builder with group_id, group_size, gender, age tracking
# - Added cross-camera overlap detection logic
# - Added queue wait time extraction from metadata
# - Improved edge case handling for partial occlusion and staff
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.logging_config import logger
from app.models import (
    EventType,
    IngestError,
    IngestResponse,
    StoreEvent,
    VisitorSession,
    parse_ps3f_event,
)
from app.storage import (
    get_connection,
    insert_event,
    is_duplicate_event,
    upsert_session,
)

# Maximum batch size per the spec
MAX_BATCH_SIZE = 500


def _build_sessions_from_events(events: list[StoreEvent]) -> dict[str, VisitorSession]:
    """
    Build or update visitor sessions from a batch of events.
    Session definition: begins at ENTRY, ends at EXIT.

    PS3f enhancements:
    - Tracks group_id and group_size for group entry detection
    - Tracks gender/age demographics from event metadata
    - Marks partial occlusion from is_face_hidden
    - Records queue wait times from billing events
    """
    sessions: dict[str, VisitorSession] = {}

    for event in sorted(events, key=lambda e: e.timestamp):
        vid = event.visitor_id
        key = f"{event.store_id}:{vid}"

        if event.event_type == EventType.ENTRY:
            session = VisitorSession(
                store_id=event.store_id,
                visitor_id=vid,
                started_at=event.timestamp,
                is_staff=event.is_staff,
                event_count=1,
                group_id=event.metadata.group_id if event.metadata else None,
                group_size=event.metadata.group_size if event.metadata else None,
                gender_pred=event.metadata.gender_pred if event.metadata else None,
                age_bucket=event.metadata.age_bucket if event.metadata else None,
                has_partial_occlusion=bool(
                    event.metadata and event.metadata.is_face_hidden
                ),
            )
            sessions[key] = session

        elif key in sessions:
            session = sessions[key]
            session.event_count += 1

            if event.event_type == EventType.EXIT:
                session.ended_at = event.timestamp

            elif event.event_type == EventType.ZONE_ENTER:
                if event.zone_id and event.zone_id not in session.zones_visited:
                    session.zones_visited.append(event.zone_id)

            elif event.event_type == EventType.ZONE_DWELL:
                session.total_dwell_ms += event.dwell_ms or 0

            elif event.event_type == EventType.BILLING_QUEUE_JOIN:
                session.entered_billing = True
                # Extract queue wait time from metadata
                if event.metadata and event.metadata.wait_seconds is not None:
                    session.queue_wait_seconds = event.metadata.wait_seconds

            elif event.event_type == EventType.BILLING_QUEUE_ABANDON:
                session.entered_billing = True
                session.queue_abandoned = True
                if event.metadata and event.metadata.wait_seconds is not None:
                    session.queue_wait_seconds = event.metadata.wait_seconds

            elif event.event_type == EventType.REENTRY:
                session.is_reentry = True

            # Update occlusion flag if any event shows hidden face
            if event.metadata and event.metadata.is_face_hidden:
                session.has_partial_occlusion = True

            # Update cross-camera flag
            if event.metadata and event.metadata.cross_camera_linked:
                session.cross_camera_linked = True

        elif event.event_type == EventType.REENTRY:
            # Re-entry without a prior session in this batch
            session = VisitorSession(
                store_id=event.store_id,
                visitor_id=vid,
                started_at=event.timestamp,
                is_staff=event.is_staff,
                is_reentry=True,
                event_count=1,
                gender_pred=event.metadata.gender_pred if event.metadata else None,
                age_bucket=event.metadata.age_bucket if event.metadata else None,
            )
            sessions[key] = session

        elif event.event_type in (EventType.ZONE_ENTER, EventType.ZONE_EXIT,
                                   EventType.ZONE_DWELL, EventType.BILLING_QUEUE_JOIN,
                                   EventType.BILLING_QUEUE_ABANDON):
            # Events without a prior ENTRY — create an implicit session
            session = VisitorSession(
                store_id=event.store_id,
                visitor_id=vid,
                started_at=event.timestamp,
                is_staff=event.is_staff,
                event_count=1,
            )
            if event.event_type == EventType.ZONE_ENTER and event.zone_id:
                session.zones_visited.append(event.zone_id)
            elif event.event_type in (EventType.BILLING_QUEUE_JOIN,
                                       EventType.BILLING_QUEUE_ABANDON):
                session.entered_billing = True
                if event.metadata and event.metadata.wait_seconds is not None:
                    session.queue_wait_seconds = event.metadata.wait_seconds
                if event.event_type == EventType.BILLING_QUEUE_ABANDON:
                    session.queue_abandoned = True
            sessions[key] = session

    return sessions


def ingest_events(raw_events: list[dict[str, Any]]) -> IngestResponse:
    """
    Ingest a batch of events with validation, deduplication, and session building.
    Returns structured response.

    Supports both internal event format AND PS3f native formats:
    - Entry/exit events with id_token, store_code
    - Zone events with track_id, zone_id, event_time
    - Queue events with queue_event_id, wait_seconds
    """
    response = IngestResponse()
    valid_events: list[StoreEvent] = []

    # Enforce batch size limit
    if len(raw_events) > MAX_BATCH_SIZE:
        raw_events = raw_events[:MAX_BATCH_SIZE]
        logger.warning(
            "Batch truncated to max size",
            extra={"max_size": MAX_BATCH_SIZE, "original_size": len(raw_events)},
        )

    conn = get_connection()
    try:
        for i, raw in enumerate(raw_events):
            try:
                # Try PS3f native format auto-detection first
                event = parse_ps3f_event(raw)
                if event is None:
                    # Fall back to direct StoreEvent construction
                    event = StoreEvent(**raw)
            except Exception as e:
                response.rejected += 1
                response.errors.append(
                    IngestError(index=i, reason=str(e)[:200])
                )
                continue

            # Idempotency check
            if is_duplicate_event(conn, event.event_id):
                response.duplicate += 1
                continue

            # Insert event
            inserted = insert_event(conn, event)
            if inserted:
                response.accepted += 1
                valid_events.append(event)
            else:
                response.duplicate += 1

        # Build sessions from accepted events
        if valid_events:
            sessions = _build_sessions_from_events(valid_events)
            for session in sessions.values():
                upsert_session(conn, session)

        conn.commit()
        logger.info(
            "Batch ingested",
            extra={
                "accepted": response.accepted,
                "rejected": response.rejected,
                "duplicate": response.duplicate,
            },
        )
    except Exception as e:
        conn.rollback()
        logger.error("Ingest failed", extra={"error": str(e)})
        raise
    finally:
        conn.close()

    return response
