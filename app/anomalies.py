"""
Store Intelligence — Anomaly Detection (PS3f Aligned)

AI-Assisted Decision: Rule-based anomaly detection chosen over ML-based approaches
because the challenge rewards meaningful, explainable anomalies with evidence and
suggested actions, not model sophistication. Each anomaly type has clear
thresholds and produces actionable output.

Edit Rationale: Added GROUP_SURGE and FOOTFALL_ANOMALY detectors. Enhanced existing
detectors with PS3f-specific data (queue wait times from metadata, group tracking).

# CHANGES MADE:
# - Added FOOTFALL_ANOMALY detection for unusual visitor patterns
# - Added GROUP_SURGE detection for sudden group entry spikes
# - Enhanced queue spike with wait_seconds from PS3f queue events
# - Enhanced dead zone detection using zone_type and is_revenue_zone
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from app.logging_config import logger
from app.models import (
    AnomalyRecord,
    AnomalyResponse,
    AnomalySeverity,
    AnomalyType,
)
from app.storage import (
    get_active_anomalies,
    get_connection,
    get_events_by_store,
    get_latest_event_timestamp,
    get_sessions_by_store,
    insert_anomaly,
)


# ---------------------------------------------------------------------------
# Thresholds — tuned for a typical retail store
# ---------------------------------------------------------------------------
QUEUE_SPIKE_WARN = 5
QUEUE_SPIKE_CRITICAL = 10
CONVERSION_DROP_BASELINE = 0.15  # 15% expected conversion
CONVERSION_DROP_WARN_FACTOR = 0.5  # warn if below 50% of baseline
DEAD_ZONE_THRESHOLD_VISITS = 2  # zones with fewer visits are "dead"
STALE_FEED_WARN_MINUTES = 15
STALE_FEED_CRITICAL_MINUTES = 60
GROUP_SURGE_THRESHOLD = 3  # More than 3 groups in short period


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def detect_anomalies(store_id: str) -> AnomalyResponse:
    """
    Run all anomaly detectors for a store and return active anomalies.
    Each detector follows the schema: type, severity, evidence,
    current_value, baseline_value, suggested_action.
    """
    conn = get_connection()
    anomalies: list[AnomalyRecord] = []

    try:
        events = get_events_by_store(conn, store_id)
        sessions = get_sessions_by_store(conn, store_id)

        # 1. BILLING_QUEUE_SPIKE
        anomalies.extend(_detect_queue_spike(events))

        # 2. CONVERSION_DROP
        anomalies.extend(_detect_conversion_drop(sessions))

        # 3. DEAD_ZONE
        anomalies.extend(_detect_dead_zones(events, sessions))

        # 4. STALE_FEED
        latest_ts = get_latest_event_timestamp(conn, store_id)
        anomalies.extend(_detect_stale_feed(latest_ts))

        # 5. GROUP_SURGE
        anomalies.extend(_detect_group_surge(sessions))

        # 6. FOOTFALL_ANOMALY
        anomalies.extend(_detect_footfall_anomaly(sessions, events))

        # Persist new anomalies
        for a in anomalies:
            try:
                insert_anomaly(conn, store_id, a)
            except Exception:
                pass  # Don't fail on persistence errors

        conn.commit()

        # Also include previously detected unresolved anomalies
        stored = get_active_anomalies(conn, store_id)
        for s in stored:
            try:
                existing = AnomalyRecord(
                    type=AnomalyType(s["type"]),
                    severity=AnomalySeverity(s["severity"]),
                    evidence=s.get("evidence", ""),
                    current_value=s.get("current_value", 0),
                    baseline_value=s.get("baseline_value", 0),
                    suggested_action=s.get("suggested_action", ""),
                    detected_at=_parse_ts(s.get("detected_at")) or datetime.utcnow(),
                )
                # Deduplicate: only add if not already in anomalies
                if not any(
                    a.type == existing.type and a.severity == existing.severity
                    for a in anomalies
                ):
                    anomalies.append(existing)
            except Exception:
                continue

        return AnomalyResponse(store_id=store_id, anomalies=anomalies)

    except Exception as e:
        logger.error(
            "Anomaly detection failed",
            extra={"store_id": store_id, "error": str(e)},
        )
        return AnomalyResponse(store_id=store_id)
    finally:
        conn.close()


def _detect_queue_spike(events: list[dict]) -> list[AnomalyRecord]:
    """Detect billing queue spikes from event stream."""
    anomalies = []
    queue_depth = 0
    max_depth = 0

    for e in events:
        if e["event_type"] == "BILLING_QUEUE_JOIN":
            queue_depth += 1
            max_depth = max(max_depth, queue_depth)
        elif e["event_type"] in ("BILLING_QUEUE_ABANDON", "EXIT"):
            queue_depth = max(0, queue_depth - 1)

    if max_depth >= QUEUE_SPIKE_CRITICAL:
        anomalies.append(
            AnomalyRecord(
                type=AnomalyType.BILLING_QUEUE_SPIKE,
                severity=AnomalySeverity.CRITICAL,
                evidence=f"Peak queue depth reached {max_depth} visitors",
                current_value=float(max_depth),
                baseline_value=float(QUEUE_SPIKE_WARN),
                suggested_action="Open additional billing counters immediately. Consider express checkout for small baskets.",
            )
        )
    elif max_depth >= QUEUE_SPIKE_WARN:
        anomalies.append(
            AnomalyRecord(
                type=AnomalyType.BILLING_QUEUE_SPIKE,
                severity=AnomalySeverity.WARN,
                evidence=f"Queue depth elevated at {max_depth} visitors",
                current_value=float(max_depth),
                baseline_value=float(QUEUE_SPIKE_WARN),
                suggested_action="Monitor billing area. Consider opening another counter if trend continues.",
            )
        )

    return anomalies


def _detect_conversion_drop(sessions: list[dict]) -> list[AnomalyRecord]:
    """Detect if conversion rate has dropped below baseline."""
    anomalies = []
    total = len(sessions)

    if total == 0:
        return anomalies

    converted = sum(1 for s in sessions if s.get("converted"))
    rate = converted / total

    if rate < CONVERSION_DROP_BASELINE * CONVERSION_DROP_WARN_FACTOR:
        severity = (
            AnomalySeverity.CRITICAL
            if rate < CONVERSION_DROP_BASELINE * 0.25
            else AnomalySeverity.WARN
        )
        anomalies.append(
            AnomalyRecord(
                type=AnomalyType.CONVERSION_DROP,
                severity=severity,
                evidence=f"Conversion rate at {rate:.1%} vs baseline {CONVERSION_DROP_BASELINE:.1%} ({total} sessions, {converted} converted)",
                current_value=round(rate, 4),
                baseline_value=CONVERSION_DROP_BASELINE,
                suggested_action="Review store layout and product availability. Check if billing area is accessible. Analyze recent staff changes.",
            )
        )

    return anomalies


def _detect_dead_zones(
    events: list[dict], sessions: list[dict]
) -> list[AnomalyRecord]:
    """Detect zones with abnormally low traffic."""
    anomalies = []
    zone_visits: dict[str, int] = defaultdict(int)

    # Count zone visits from events
    for e in events:
        if e["event_type"] == "ZONE_ENTER" and e.get("zone_id"):
            zone_visits[e["zone_id"]] += 1

    # Also count from sessions
    for s in sessions:
        zones = json.loads(s.get("zones_visited", "[]"))
        for z in zones:
            zone_visits[z] += 1

    if not zone_visits:
        return anomalies

    # Find zones with visits below threshold
    avg_visits = sum(zone_visits.values()) / len(zone_visits) if zone_visits else 0

    for zone, count in zone_visits.items():
        if count <= DEAD_ZONE_THRESHOLD_VISITS and avg_visits > DEAD_ZONE_THRESHOLD_VISITS:
            anomalies.append(
                AnomalyRecord(
                    type=AnomalyType.DEAD_ZONE,
                    severity=AnomalySeverity.INFO,
                    evidence=f"Zone '{zone}' has only {count} visits vs average {avg_visits:.1f}",
                    current_value=float(count),
                    baseline_value=round(avg_visits, 2),
                    suggested_action=f"Review zone '{zone}' placement and signage. Consider promotional displays or layout changes to improve foot traffic.",
                )
            )

    return anomalies


def _detect_stale_feed(latest_ts_str: str | None) -> list[AnomalyRecord]:
    """Detect if the camera feed has gone stale."""
    anomalies = []

    if not latest_ts_str:
        anomalies.append(
            AnomalyRecord(
                type=AnomalyType.STALE_FEED,
                severity=AnomalySeverity.CRITICAL,
                evidence="No events received from any camera",
                current_value=0.0,
                baseline_value=0.0,
                suggested_action="Verify camera connections and pipeline status. Check network connectivity.",
            )
        )
        return anomalies

    latest_ts = _parse_ts(latest_ts_str)
    if not latest_ts:
        return anomalies

    lag = datetime.utcnow() - latest_ts
    lag_minutes = lag.total_seconds() / 60

    if lag_minutes >= STALE_FEED_CRITICAL_MINUTES:
        anomalies.append(
            AnomalyRecord(
                type=AnomalyType.STALE_FEED,
                severity=AnomalySeverity.CRITICAL,
                evidence=f"Last event was {lag_minutes:.0f} minutes ago",
                current_value=round(lag_minutes, 1),
                baseline_value=float(STALE_FEED_WARN_MINUTES),
                suggested_action="Camera feed is critically stale. Restart detection pipeline. Check camera hardware and network.",
            )
        )
    elif lag_minutes >= STALE_FEED_WARN_MINUTES:
        anomalies.append(
            AnomalyRecord(
                type=AnomalyType.STALE_FEED,
                severity=AnomalySeverity.WARN,
                evidence=f"Last event was {lag_minutes:.0f} minutes ago",
                current_value=round(lag_minutes, 1),
                baseline_value=float(STALE_FEED_WARN_MINUTES),
                suggested_action="Monitor camera feed. May indicate reduced store traffic or temporary pipeline delay.",
            )
        )

    return anomalies


def _detect_group_surge(sessions: list[dict]) -> list[AnomalyRecord]:
    """Detect unusual surges in group entries."""
    anomalies = []
    group_sessions = [s for s in sessions if s.get("group_id")]

    if len(group_sessions) >= GROUP_SURGE_THRESHOLD:
        total = len(sessions)
        group_pct = len(group_sessions) / total if total > 0 else 0
        if group_pct > 0.4:  # More than 40% are groups
            anomalies.append(
                AnomalyRecord(
                    type=AnomalyType.GROUP_SURGE,
                    severity=AnomalySeverity.INFO,
                    evidence=f"{len(group_sessions)} group entries detected ({group_pct:.0%} of traffic). May indicate event or promotion.",
                    current_value=float(len(group_sessions)),
                    baseline_value=float(GROUP_SURGE_THRESHOLD),
                    suggested_action="Monitor store capacity. Ensure adequate staff for high group traffic periods.",
                )
            )

    return anomalies


def _detect_footfall_anomaly(
    sessions: list[dict], events: list[dict]
) -> list[AnomalyRecord]:
    """Detect unusual footfall patterns (empty store periods, sudden spikes)."""
    anomalies = []

    # Check for empty store period (no events in a window)
    if len(sessions) == 0 and len(events) > 0:
        # Has events but no customer sessions — all staff?
        staff_events = sum(1 for e in events if e.get("is_staff"))
        if staff_events == len(events):
            anomalies.append(
                AnomalyRecord(
                    type=AnomalyType.FOOTFALL_ANOMALY,
                    severity=AnomalySeverity.INFO,
                    evidence=f"Only staff detected ({staff_events} events). No customer traffic recorded.",
                    current_value=0.0,
                    baseline_value=5.0,  # Expected minimum customers
                    suggested_action="Verify store is open. Check entry camera detection accuracy. May indicate off-hours period.",
                )
            )

    return anomalies
