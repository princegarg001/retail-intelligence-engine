"""
Store Intelligence — Metrics Computation (PS3f Aligned)

AI-Assisted Decision: Metrics computed from session-based aggregation, not raw event
counts, because the challenge explicitly penalises counting raw ENTRY events only.
Conversion rate uses the North Star formula: converted_unique_visitors / total_unique_visitors.

Edit Rationale: Enhanced with PS3f-specific metrics:
- Gender/age distribution from session demographics
- Group entry counting
- Re-entry tracking
- Queue wait time averaging
- POS transaction count and revenue correlation
- Staff count tracking

# CHANGES MADE:
# - Added gender_distribution and age_distribution from session metadata
# - Added group_entries count from group_id tracking
# - Added reentry_count from is_reentry flag
# - Added avg_queue_wait_seconds from queue events
# - Added staff_count from staff sessions
# - Added total_footfall (raw entry count including groups)
# - Enhanced POS correlation with new CSV schema
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

from app.logging_config import logger
from app.models import StoreMetrics
from app.storage import (
    get_connection,
    get_events_by_store,
    get_latest_event_timestamp,
    get_sessions_by_store,
    get_all_sessions_by_store,
    get_transactions_by_store,
    get_transaction_count_by_store,
)

# Freshness threshold
STALE_THRESHOLD_MINUTES = 30
# POS correlation window
POS_CORRELATION_WINDOW_MINUTES = 5


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp string to datetime and strip timezone info for naive comparison."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def compute_store_metrics(store_id: str) -> StoreMetrics:
    """
    Compute comprehensive store metrics from sessions and events.
    Excludes staff sessions from customer metrics.

    PS3f enhancements:
    - gender/age distribution
    - group entry counting
    - re-entry detection count
    - average queue wait time
    - staff count from staff-flagged sessions
    """
    conn = get_connection()
    try:
        # Get customer sessions (staff excluded)
        sessions = get_sessions_by_store(conn, store_id)
        all_sessions = get_all_sessions_by_store(conn, store_id)
        events = get_events_by_store(conn, store_id)
        transactions = get_transactions_by_store(conn, store_id)

        # Unique visitors: count distinct visitor sessions
        unique_visitors = set()
        billing_visitors = set()
        converted_visitors = set()
        abandoned_visitors = set()
        zone_dwell: dict[str, list[int]] = defaultdict(list)
        current_queue_depth = 0
        gender_dist: dict[str, int] = defaultdict(int)
        age_dist: dict[str, int] = defaultdict(int)
        group_entries = 0
        reentry_count = 0
        queue_wait_times: list[int] = []
        staff_count = 0

        # Count staff sessions
        for sess in all_sessions:
            if sess.get("is_staff"):
                staff_count += 1

        for sess in sessions:
            vid = sess["visitor_id"]
            unique_visitors.add(vid)

            # Zone dwell aggregation
            zones = json.loads(sess.get("zones_visited", "[]"))
            for zone in zones:
                zone_dwell[zone].append(sess.get("total_dwell_ms", 0))

            # Billing and conversion tracking
            if sess.get("entered_billing"):
                billing_visitors.add(vid)

            if sess.get("converted"):
                converted_visitors.add(vid)

            # Gender distribution
            gender = sess.get("gender_pred")
            if gender:
                gender_dist[gender] += 1

            # Age distribution
            age_bucket = sess.get("age_bucket")
            if age_bucket:
                age_dist[age_bucket] += 1

            # Group entry detection
            if sess.get("group_id"):
                group_entries += 1

            # Re-entry count
            if sess.get("is_reentry"):
                reentry_count += 1

            # Queue wait times
            wait = sess.get("queue_wait_seconds")
            if wait is not None and wait > 0:
                queue_wait_times.append(wait)

        # POS correlation: match visitors in billing zone within 5-min window
        billing_events = [
            e for e in events
            if e["event_type"] == "BILLING_QUEUE_JOIN" and e.get("is_staff") == 0
        ]

        for txn in transactions:
            txn_ts = _parse_ts(txn.get("timestamp"))
            if not txn_ts:
                continue

            for be in billing_events:
                be_ts = _parse_ts(be.get("timestamp"))
                if not be_ts:
                    continue

                if timedelta(0) <= (txn_ts - be_ts) <= timedelta(
                    minutes=POS_CORRELATION_WINDOW_MINUTES
                ):
                    converted_visitors.add(be["visitor_id"])

        # Abandonment: entered billing but not converted
        for vid in billing_visitors:
            if vid not in converted_visitors:
                abandoned_visitors.add(vid)

        # Queue depth: count active billing zone occupants from events
        for e in events:
            if e["event_type"] == "BILLING_QUEUE_JOIN":
                current_queue_depth += 1
            elif e["event_type"] in ("BILLING_QUEUE_ABANDON", "EXIT"):
                current_queue_depth = max(0, current_queue_depth - 1)

        # Compute rates
        total = len(unique_visitors)
        conversion_rate = (
            len(converted_visitors) / total if total > 0 else 0.0
        )
        abandonment_rate = (
            len(abandoned_visitors) / len(billing_visitors)
            if len(billing_visitors) > 0
            else 0.0
        )

        # Average dwell per zone
        avg_dwell_per_zone = {}
        for zone, dwells in zone_dwell.items():
            avg_dwell_per_zone[zone] = (
                sum(dwells) / len(dwells) if dwells else 0.0
            )

        # Average queue wait
        avg_queue_wait = (
            sum(queue_wait_times) / len(queue_wait_times)
            if queue_wait_times
            else 0.0
        )

        # Total footfall (counting group members)
        total_footfall = total
        for sess in sessions:
            gs = sess.get("group_size")
            if gs and gs > 1:
                total_footfall += gs - 1  # Add extra group members

        # Data freshness
        latest_ts_str = get_latest_event_timestamp(conn, store_id)
        latest_ts = _parse_ts(latest_ts_str)
        if latest_ts:
            lag = datetime.utcnow() - latest_ts
            freshness = (
                "fresh"
                if lag < timedelta(minutes=STALE_THRESHOLD_MINUTES)
                else "stale"
            )
        else:
            freshness = "unknown"

        return StoreMetrics(
            store_id=store_id,
            unique_visitors_today=total,
            total_footfall=total_footfall,
            staff_count=staff_count,
            conversion_rate=round(conversion_rate, 4),
            avg_dwell_per_zone=avg_dwell_per_zone,
            queue_depth=max(0, current_queue_depth),
            avg_queue_wait_seconds=round(avg_queue_wait, 1),
            abandonment_rate=round(abandonment_rate, 4),
            group_entries=group_entries,
            reentry_count=reentry_count,
            last_event_timestamp=latest_ts,
            data_freshness=freshness,
            gender_distribution=dict(gender_dist),
            age_distribution=dict(age_dist),
        )

    except Exception as e:
        logger.error(
            "Metrics computation failed",
            extra={"store_id": store_id, "error": str(e)},
        )
        return StoreMetrics(store_id=store_id, data_freshness="error")
    finally:
        conn.close()
