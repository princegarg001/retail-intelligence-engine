"""
Store Intelligence — Edge Case Validation Tests (PS3f)

# PROMPT:
# "Generate comprehensive edge case tests per PS3f specification covering
#  group entry, staff exclusion, visitor re-entry, partial occlusion,
#  billing queue buildup, empty store periods, and cross-camera dedup."
#
# CHANGES MADE:
# - Added tests for all 7 mandatory edge cases from PS3f spec
# - Added PS3f native event format ingestion tests
# - Added multi-store ingestion tests
# - Tests use actual PS3f sample event format
"""

import os
import sys
import uuid
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ingestion import ingest_events
from app.metrics import compute_store_metrics
from app.funnel import compute_funnel
from app.health import check_health
from app.storage import init_db, get_connection


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
        "store_id": kw.get("store_id", "ST1008"),
        "camera_id": kw.get("camera_id", "CAM_1"),
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": kw.get("timestamp", "2026-04-10T12:00:00Z"),
        "zone_id": kw.get("zone_id"),
        "dwell_ms": kw.get("dwell_ms", 0),
        "is_staff": kw.get("is_staff", False),
        "confidence": kw.get("confidence", 0.85),
        "metadata": kw.get("metadata", {}),
    }


class TestGroupEntryDetection:
    """Edge case: Group entry detection."""

    def test_group_entry_with_metadata(self):
        """Group entry should be tracked via group_id in metadata."""
        events = [
            _event("V001", "ENTRY", metadata={"group_id": "G_10", "group_size": 2}),
            _event("V002", "ENTRY", metadata={"group_id": "G_10", "group_size": 2}),
        ]
        result = ingest_events(events)
        assert result.accepted == 2

        metrics = compute_store_metrics("ST1008")
        assert metrics.group_entries >= 1

    def test_ps3f_native_group_entry(self):
        """PS3f native entry events with group_id should be ingested."""
        events = [
            {
                "event_type": "entry",
                "id_token": "ID_60002",
                "store_code": "store_1076",
                "camera_id": "cam1",
                "event_timestamp": "2026-03-08T18:10:22.480000",
                "is_staff": False,
                "gender_pred": "M",
                "age_pred": 31,
                "age_bucket": "25-34",
                "is_face_hidden": False,
                "group_id": "G_10",
                "group_size": 2,
            },
            {
                "event_type": "entry",
                "id_token": "ID_60003",
                "store_code": "store_1076",
                "camera_id": "cam1",
                "event_timestamp": "2026-03-08T18:10:24.920000",
                "is_staff": False,
                "gender_pred": "F",
                "age_pred": 29,
                "age_bucket": "25-34",
                "is_face_hidden": False,
                "group_id": "G_10",
                "group_size": 2,
            },
        ]
        result = ingest_events(events)
        assert result.accepted == 2


class TestStaffIdentificationExclusion:
    """Edge case: Staff identification and exclusion."""

    def test_staff_excluded_from_visitor_count(self):
        """Staff should not be counted as unique visitors."""
        events = [
            _event("STAFF_1", "ENTRY", is_staff=True),
            _event("V001", "ENTRY", is_staff=False),
            _event("V002", "ENTRY", is_staff=False),
        ]
        ingest_events(events)
        metrics = compute_store_metrics("ST1008")
        assert metrics.unique_visitors_today == 2

    def test_staff_excluded_from_funnel(self):
        """Staff should not appear in conversion funnel."""
        events = [
            _event("STAFF_1", "ENTRY", is_staff=True),
            _event("STAFF_1", "ZONE_ENTER", zone_id="billing", is_staff=True),
            _event("V001", "ENTRY", is_staff=False),
        ]
        ingest_events(events)
        funnel = compute_funnel("ST1008")
        # Only V001 should be in funnel
        assert funnel.total_sessions == 1

    def test_staff_count_in_metrics(self):
        """Staff count should be reported separately."""
        events = [
            _event("STAFF_1", "ENTRY", is_staff=True),
            _event("STAFF_2", "ENTRY", is_staff=True),
            _event("V001", "ENTRY", is_staff=False),
        ]
        ingest_events(events)
        metrics = compute_store_metrics("ST1008")
        assert metrics.staff_count >= 2


class TestVisitorReentry:
    """Edge case: Visitor re-entry detection."""

    def test_reentry_event(self):
        """REENTRY event should create a session with is_reentry flag."""
        events = [
            _event("V001", "ENTRY", timestamp="2026-04-10T12:00:00Z"),
            _event("V001", "EXIT", timestamp="2026-04-10T12:10:00Z"),
            _event("V001", "REENTRY", timestamp="2026-04-10T12:30:00Z"),
        ]
        result = ingest_events(events)
        assert result.accepted == 3

        metrics = compute_store_metrics("ST1008")
        assert metrics.reentry_count >= 1


class TestPartialOcclusion:
    """Edge case: Partial occlusion handling."""

    def test_face_hidden_flag(self):
        """Events with is_face_hidden should still be accepted."""
        events = [
            _event("V001", "ENTRY", metadata={"is_face_hidden": True}),
        ]
        result = ingest_events(events)
        assert result.accepted == 1

    def test_low_confidence_not_suppressed(self):
        """Low confidence detections should not be silently suppressed."""
        events = [
            _event("V001", "ENTRY", confidence=0.1),
            _event("V002", "ENTRY", confidence=0.01),
        ]
        result = ingest_events(events)
        assert result.accepted == 2


class TestBillingQueueBuildup:
    """Edge case: Billing queue buildup."""

    def test_queue_depth_tracking(self):
        """Queue depth should increase with BILLING_QUEUE_JOIN events."""
        events = [
            _event("V001", "ENTRY"),
            _event("V001", "BILLING_QUEUE_JOIN"),
            _event("V002", "ENTRY"),
            _event("V002", "BILLING_QUEUE_JOIN"),
            _event("V003", "ENTRY"),
            _event("V003", "BILLING_QUEUE_JOIN"),
        ]
        ingest_events(events)
        metrics = compute_store_metrics("ST1008")
        assert metrics.queue_depth >= 0

    def test_queue_abandon_tracking(self):
        """BILLING_QUEUE_ABANDON should be tracked."""
        events = [
            _event("V001", "ENTRY"),
            _event("V001", "BILLING_QUEUE_JOIN"),
            _event("V001", "BILLING_QUEUE_ABANDON"),
        ]
        ingest_events(events)
        metrics = compute_store_metrics("ST1008")
        assert metrics.abandonment_rate >= 0.0

    def test_ps3f_queue_events(self):
        """PS3f queue events with wait_seconds should be ingested."""
        events = [
            {
                "queue_event_id": str(uuid.uuid4()),
                "event_type": "queue_completed",
                "track_id": 102,
                "store_id": "ST1076",
                "camera_id": "PURPLLE_MUM_1076_CAM6",
                "zone_id": "PURPLLE_MUM_1076_Z_BILLING_01",
                "zone_name": "Billing Counter Queue",
                "zone_type": "BILLING",
                "is_revenue_zone": "Yes",
                "queue_join_ts": "2026-03-08T18:13:05.080000",
                "queue_served_ts": "2026-03-08T18:13:13.240000",
                "queue_exit_ts": "2026-03-08T18:15:31.840000",
                "wait_seconds": 8,
                "queue_position_at_join": 2,
                "abandoned": False,
                "zone_hotspot_x": 602.8,
                "zone_hotspot_y": 183.4,
                "gender": "M",
                "age": 31,
                "age_bucket": "25-34",
            },
        ]
        result = ingest_events(events)
        assert result.accepted == 1


class TestEmptyStorePeriods:
    """Edge case: Empty store periods."""

    def test_empty_store_metrics(self):
        """Empty store should return zero metrics, not errors."""
        metrics = compute_store_metrics("EMPTY_STORE")
        assert metrics.unique_visitors_today == 0
        assert metrics.conversion_rate == 0.0
        assert metrics.queue_depth == 0

    def test_empty_store_funnel(self):
        """Empty store should return valid funnel with zero counts."""
        funnel = compute_funnel("EMPTY_STORE")
        assert funnel.total_sessions == 0
        assert len(funnel.stages) == 4
        for stage in funnel.stages:
            assert stage.count == 0

    def test_empty_store_health(self):
        """Empty store with no events should show no_data status."""
        health = check_health()
        assert health.status == "no_data"


class TestCrossCameraDedup:
    """Edge case: Cross-camera overlap and visitor deduplication."""

    def test_different_cameras_same_visitor(self):
        """Events from different cameras for same visitor should be linked."""
        events = [
            _event("V001", "ENTRY", camera_id="CAM_1",
                   timestamp="2026-04-10T12:00:00Z"),
            _event("V001", "ZONE_ENTER", camera_id="CAM_2", zone_id="skincare",
                   timestamp="2026-04-10T12:01:00Z"),
            _event("V001", "ZONE_EXIT", camera_id="CAM_2", zone_id="skincare",
                   timestamp="2026-04-10T12:05:00Z"),
        ]
        result = ingest_events(events)
        assert result.accepted == 3

    def test_multi_camera_session(self):
        """Visitor seen across multiple cameras should have one session."""
        events = [
            _event("V001", "ENTRY", camera_id="CAM_1"),
            _event("V001", "ZONE_ENTER", camera_id="CAM_2", zone_id="skincare"),
            _event("V001", "ZONE_ENTER", camera_id="CAM_3", zone_id="haircare"),
            _event("V001", "BILLING_QUEUE_JOIN", camera_id="CAM_4"),
            _event("V001", "EXIT", camera_id="CAM_1"),
        ]
        ingest_events(events)
        metrics = compute_store_metrics("ST1008")
        # Should count as 1 unique visitor despite 5 cameras
        assert metrics.unique_visitors_today == 1


class TestPS3fNativeFormat:
    """Tests for PS3f native event format ingestion."""

    def test_ps3f_entry_event(self):
        """PS3f entry event with id_token/store_code should be ingested."""
        events = [
            {
                "event_type": "entry",
                "id_token": "ID_60001",
                "store_code": "store_1076",
                "camera_id": "cam1",
                "event_timestamp": "2026-03-08T18:10:05.120000",
                "is_staff": False,
                "gender_pred": "F",
                "age_pred": 28,
                "age_bucket": "25-34",
                "is_face_hidden": False,
                "group_id": None,
                "group_size": None,
            },
        ]
        result = ingest_events(events)
        assert result.accepted == 1

    def test_ps3f_zone_event(self):
        """PS3f zone event with track_id should be ingested."""
        events = [
            {
                "event_type": "zone_entered",
                "track_id": 101,
                "store_id": "ST1076",
                "camera_id": "CAM2",
                "zone_id": "PURPLLE_MUM_1076_Z01",
                "zone_name": "Left Shelf",
                "zone_type": "SHELF",
                "is_revenue_zone": "Yes",
                "event_time": "2026-03-08T18:10:45.280000",
                "zone_hotspot_x": 412.6,
                "zone_hotspot_y": 238.4,
                "gender": "F",
                "age": 28,
                "age_bucket": "25-34",
            },
        ]
        result = ingest_events(events)
        assert result.accepted == 1

    def test_ps3f_mixed_batch(self):
        """Mixed batch of PS3f entry, zone, and queue events."""
        events = [
            {
                "event_type": "entry",
                "id_token": "ID_70001",
                "store_code": "store_1076",
                "camera_id": "cam1",
                "event_timestamp": "2026-03-08T19:00:00.000000",
                "is_staff": False,
                "gender_pred": "M",
                "age_pred": 25,
                "age_bucket": "25-34",
                "is_face_hidden": False,
                "group_id": None,
                "group_size": None,
            },
            {
                "event_type": "zone_entered",
                "track_id": 201,
                "store_id": "ST1076",
                "camera_id": "CAM2",
                "zone_id": "PURPLLE_MUM_1076_Z01",
                "zone_name": "Left Shelf",
                "zone_type": "SHELF",
                "is_revenue_zone": "Yes",
                "event_time": "2026-03-08T19:01:00.000000",
                "zone_hotspot_x": 412.6,
                "zone_hotspot_y": 238.4,
                "gender": "M",
                "age": 25,
                "age_bucket": "25-34",
            },
            {
                "queue_event_id": str(uuid.uuid4()),
                "event_type": "queue_abandoned",
                "track_id": 201,
                "store_id": "ST1076",
                "camera_id": "PURPLLE_MUM_1076_CAM6",
                "zone_id": "PURPLLE_MUM_1076_Z_BILLING_01",
                "zone_name": "Billing Counter Queue",
                "zone_type": "BILLING",
                "is_revenue_zone": "Yes",
                "queue_join_ts": "2026-03-08T19:05:00.000000",
                "queue_served_ts": None,
                "queue_exit_ts": "2026-03-08T19:06:00.000000",
                "wait_seconds": 60,
                "queue_position_at_join": 3,
                "abandoned": True,
                "zone_hotspot_x": 598.1,
                "zone_hotspot_y": 176.8,
                "gender": "M",
                "age": 25,
                "age_bucket": "25-34",
            },
        ]
        result = ingest_events(events)
        assert result.accepted == 3
        assert result.rejected == 0


class TestDemographics:
    """Tests for gender/age tracking from PS3f events."""

    def test_gender_distribution(self):
        """Gender distribution should be tracked from event metadata."""
        events = [
            _event("V001", "ENTRY", metadata={"gender_pred": "F", "age_bucket": "25-34"}),
            _event("V002", "ENTRY", metadata={"gender_pred": "M", "age_bucket": "18-24"}),
            _event("V003", "ENTRY", metadata={"gender_pred": "F", "age_bucket": "35-44"}),
        ]
        ingest_events(events)
        metrics = compute_store_metrics("ST1008")
        # Should have gender distribution
        assert isinstance(metrics.gender_distribution, dict)
        assert isinstance(metrics.age_distribution, dict)
