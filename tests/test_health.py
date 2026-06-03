"""
Store Intelligence — Health Check Tests

# PROMPT:
# "Generate tests for the health endpoint verifying system status,
#  per-store freshness, and stale feed warning logic."

# CHANGES MADE:
# - Added checks for GET /health service status response and keys
# - Added per-store freshness validation and stale feed trigger thresholds
"""


import os
import sys
import uuid
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.health import check_health
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


class TestHealth:
    """Tests for GET /health (§8)."""

    def test_no_data_status(self):
        """No events should return 'no_data' status."""
        health = check_health()
        assert health.status == "no_data"

    def test_healthy_with_recent_events(self):
        """Recent events should show 'healthy' status."""
        events = [{
            "event_id": str(uuid.uuid4()),
            "store_id": "ST1008",
            "camera_id": "CAM_1",
            "visitor_id": "V001",
            "event_type": "ENTRY",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "confidence": 0.85,
            "metadata": {},
        }]
        ingest_events(events)
        health = check_health()
        assert health.status == "healthy"
        assert len(health.stores) == 1
        assert health.stores[0].store_id == "ST1008"

    def test_stale_feed_warning(self):
        """Old events should trigger stale feed warning."""
        events = [{
            "event_id": str(uuid.uuid4()),
            "store_id": "ST1008",
            "camera_id": "CAM_1",
            "visitor_id": "V001",
            "event_type": "ENTRY",
            "timestamp": "2025-01-01T00:00:00Z",
            "confidence": 0.85,
            "metadata": {},
        }]
        ingest_events(events)
        health = check_health()
        assert health.status == "degraded"
        assert health.stores[0].stale_feed_warning is True

    def test_multiple_stores(self):
        """Should track health per store."""
        events = [
            {
                "event_id": str(uuid.uuid4()),
                "store_id": "ST1008",
                "camera_id": "CAM_1",
                "visitor_id": "V001",
                "event_type": "ENTRY",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "confidence": 0.85,
                "metadata": {},
            },
            {
                "event_id": str(uuid.uuid4()),
                "store_id": "ST2000",
                "camera_id": "CAM_1",
                "visitor_id": "V002",
                "event_type": "ENTRY",
                "timestamp": "2025-01-01T00:00:00Z",
                "confidence": 0.85,
                "metadata": {},
            },
        ]
        ingest_events(events)
        health = check_health()
        assert len(health.stores) == 2
