"""
Store Intelligence — Health Check (PS3f Aligned)

AI-Assisted Decision: Health endpoint designed to give reviewers immediate feedback
on system liveness, per-store freshness, and pipeline status. Returns
structured JSON that can be consumed by monitoring systems.

Edit Rationale: Enhanced with per-camera health monitoring, event counts,
uptime tracking, and version reporting.

# CHANGES MADE:
# - Added per-camera health within each store
# - Added event count per store and camera
# - Added uptime_seconds tracking
# - Added version reporting
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from app.logging_config import logger
from app.models import CameraHealth, HealthResponse, StoreHealth
from app.storage import (
    get_all_store_ids,
    get_camera_health,
    get_connection,
    get_latest_event_timestamp,
)

STALE_THRESHOLD_MINUTES = 30
_START_TIME = time.time()


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def check_health() -> HealthResponse:
    """
    Check overall system health including per-store and per-camera freshness.
    A store is marked stale if its latest event exceeds the threshold.
    """
    conn = get_connection()
    try:
        store_ids = get_all_store_ids(conn)
        stores: list[StoreHealth] = []
        any_stale = False

        for sid in store_ids:
            latest_ts_str = get_latest_event_timestamp(conn, sid)
            latest_ts = _parse_ts(latest_ts_str)
            stale = False

            if latest_ts:
                lag = datetime.utcnow() - latest_ts
                stale = lag > timedelta(minutes=STALE_THRESHOLD_MINUTES)
            else:
                stale = True  # No events = stale

            if stale:
                any_stale = True

            # Per-camera health
            cam_records = get_camera_health(conn, sid)
            cameras = []
            total_events = 0
            for cr in cam_records:
                cam_ts = _parse_ts(cr.get("last_event_at"))
                cam_stale = False
                if cam_ts:
                    cam_lag = datetime.utcnow() - cam_ts
                    cam_stale = cam_lag > timedelta(minutes=STALE_THRESHOLD_MINUTES)
                else:
                    cam_stale = True

                evt_count = cr.get("event_count", 0)
                total_events += evt_count

                cameras.append(
                    CameraHealth(
                        camera_id=cr["camera_id"],
                        last_event_timestamp=cam_ts,
                        event_count=evt_count,
                        stale=cam_stale,
                    )
                )

            stores.append(
                StoreHealth(
                    store_id=sid,
                    last_event_timestamp=latest_ts,
                    stale_feed_warning=stale,
                    cameras=cameras,
                    total_events=total_events,
                )
            )

        status = "healthy"
        if not store_ids:
            status = "no_data"
        elif any_stale:
            status = "degraded"

        uptime = round(time.time() - _START_TIME, 1)

        return HealthResponse(
            status=status,
            stores=stores,
            uptime_seconds=uptime,
        )

    except Exception as e:
        logger.error("Health check failed", extra={"error": str(e)})
        return HealthResponse(status="unhealthy")
    finally:
        conn.close()
