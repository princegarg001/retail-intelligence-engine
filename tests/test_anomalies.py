"""
Store Intelligence — Anomaly Detection Tests

# PROMPT:
# "Generate tests for anomaly detection covering queue spikes,
#  conversion drops, dead zones, and stale feeds with severity levels."

# CHANGES MADE:
# - Added validation for anomaly severity classification and evidence matching
# - Handled datetime timezone warnings
"""


import os
import sys
import uuid
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.anomalies import (
    detect_anomalies,
    _detect_queue_spike,
    _detect_conversion_drop,
    _detect_dead_zones,
    _detect_stale_feed,
)
from app.storage import init_db
from app.ingestion import ingest_events


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    import app.storage as storage_mod
    original = storage_mod.DB_PATH
    storage_mod.DB_PATH = str(tmp_path / "test.db")
    init_db()
    yield
    storage_mod.DB_PATH = original


def _event(visitor_id, event_type, **kw):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": "ST1008",
        "camera_id": "CAM_1",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": kw.get("timestamp", "2026-04-10T12:00:00Z"),
        "zone_id": kw.get("zone_id"),
        "dwell_ms": kw.get("dwell_ms", 0),
        "is_staff": kw.get("is_staff", False),
        "confidence": 0.85,
        "metadata": {},
    }


class TestQueueSpike:
    """Tests for BILLING_QUEUE_SPIKE anomaly."""

    def test_no_spike(self):
        """No queue spike for low queue depth."""
        events = [
            {"event_type": "BILLING_QUEUE_JOIN", "zone_id": "billing", "is_staff": 0},
        ]
        anomalies = _detect_queue_spike(events)
        assert len(anomalies) == 0

    def test_warn_spike(self):
        """Queue depth >= 5 should trigger WARN."""
        events = [
            {"event_type": "BILLING_QUEUE_JOIN"} for _ in range(6)
        ]
        anomalies = _detect_queue_spike(events)
        assert len(anomalies) >= 1
        assert anomalies[0].severity.value == "WARN"

    def test_critical_spike(self):
        """Queue depth >= 10 should trigger CRITICAL."""
        events = [
            {"event_type": "BILLING_QUEUE_JOIN"} for _ in range(12)
        ]
        anomalies = _detect_queue_spike(events)
        assert len(anomalies) >= 1
        assert anomalies[0].severity.value == "CRITICAL"


class TestConversionDrop:
    """Tests for CONVERSION_DROP anomaly."""

    def test_no_sessions(self):
        """No sessions = no anomaly."""
        anomalies = _detect_conversion_drop([])
        assert len(anomalies) == 0

    def test_low_conversion(self):
        """Very low conversion should trigger anomaly."""
        sessions = [
            {"converted": 0} for _ in range(20)
        ]
        anomalies = _detect_conversion_drop(sessions)
        assert len(anomalies) >= 1


class TestDeadZones:
    """Tests for DEAD_ZONE anomaly."""

    def test_no_events(self):
        """No zone events = no dead zone anomaly."""
        anomalies = _detect_dead_zones([], [])
        assert len(anomalies) == 0

    def test_dead_zone_detected(self):
        """Zone with very few visits should be flagged."""
        events = [
            {"event_type": "ZONE_ENTER", "zone_id": "active_zone"} for _ in range(20)
        ] + [
            {"event_type": "ZONE_ENTER", "zone_id": "dead_zone"},
        ]
        sessions = []
        anomalies = _detect_dead_zones(events, sessions)
        # dead_zone has 1 visit vs average ~10, should be flagged
        assert any(a.type.value == "DEAD_ZONE" for a in anomalies)


class TestStaleFeed:
    """Tests for STALE_FEED anomaly."""

    def test_no_events(self):
        """No events = critical stale feed."""
        anomalies = _detect_stale_feed(None)
        assert len(anomalies) == 1
        assert anomalies[0].severity.value == "CRITICAL"

    def test_fresh_feed(self):
        """Recent events = no stale feed anomaly."""
        recent = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
        anomalies = _detect_stale_feed(recent)
        assert len(anomalies) == 0

    def test_stale_feed_warn(self):
        """Events 20 minutes ago = WARN."""
        old = (datetime.utcnow() - timedelta(minutes=20)).isoformat()
        anomalies = _detect_stale_feed(old)
        assert len(anomalies) >= 1
        assert anomalies[0].severity.value == "WARN"

    def test_stale_feed_critical(self):
        """Events 2 hours ago = CRITICAL."""
        very_old = (datetime.utcnow() - timedelta(hours=2)).isoformat()
        anomalies = _detect_stale_feed(very_old)
        assert len(anomalies) >= 1
        assert anomalies[0].severity.value == "CRITICAL"


class TestAnomalySchema:
    """Tests for anomaly schema compliance (§7.5)."""

    def test_anomaly_has_required_fields(self):
        """Each anomaly should have type, severity, evidence, current_value, baseline_value, suggested_action."""
        events = [{"event_type": "BILLING_QUEUE_JOIN"} for _ in range(12)]
        anomalies = _detect_queue_spike(events)
        for a in anomalies:
            assert a.type is not None
            assert a.severity is not None
            assert a.evidence
            assert isinstance(a.current_value, float)
            assert isinstance(a.baseline_value, float)
            assert a.suggested_action

    def test_full_anomaly_detection(self):
        """Full anomaly detection should work without errors."""
        response = detect_anomalies("ST1008")
        assert response.store_id == "ST1008"
        assert isinstance(response.anomalies, list)
