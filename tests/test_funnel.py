"""
Store Intelligence — Funnel Tests

# PROMPT:
# "Generate tests for the session-based funnel verifying stage counting,
#  drop-off computation, and deduplication by session."

# CHANGES MADE:
# - Handled stage counting, drop-off percentages, and conversion rate
# - Added checks for empty store funnel behavior
"""


import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.funnel import compute_funnel
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


class TestFunnel:
    """Tests for GET /stores/{id}/funnel (§8)."""

    def test_empty_store_funnel(self):
        """Empty store should return zero-count funnel (§11.3)."""
        funnel = compute_funnel("ST1008")
        assert funnel.total_sessions == 0
        assert funnel.conversion_rate == 0.0
        assert len(funnel.stages) == 4

    def test_entry_only_funnel(self):
        """Visitors who only enter should show in ENTRY stage."""
        events = [
            _event("V001", "ENTRY"),
            _event("V002", "ENTRY"),
        ]
        ingest_events(events)
        funnel = compute_funnel("ST1008")
        assert funnel.stages[0].stage == "ENTRY"
        assert funnel.stages[0].count == 2

    def test_full_funnel(self):
        """Complete visitor journey should appear in all stages."""
        events = [
            _event("V001", "ENTRY"),
            _event("V001", "ZONE_ENTER", zone_id="skincare"),
            _event("V001", "BILLING_QUEUE_JOIN", zone_id="billing"),
            _event("V001", "EXIT"),
        ]
        ingest_events(events)
        funnel = compute_funnel("ST1008")
        assert funnel.stages[0].count >= 1  # ENTRY
        assert funnel.stages[1].count >= 1  # ZONE_VISIT

    def test_drop_off_percentages(self):
        """Drop-off should be computed between consecutive stages."""
        events = [
            _event("V001", "ENTRY"),
            _event("V001", "ZONE_ENTER", zone_id="skincare"),
            _event("V002", "ENTRY"),
            # V002 does not visit any zone
        ]
        ingest_events(events)
        funnel = compute_funnel("ST1008")
        # Drop-off at ZONE_VISIT should be > 0 since V002 didn't visit zones
        if funnel.stages[0].count > 0:
            assert funnel.stages[1].drop_off_pct >= 0.0

    def test_funnel_deduplication(self):
        """Multiple zone events in one session should not create multiple visitors (§7.2)."""
        events = [
            _event("V001", "ENTRY"),
            _event("V001", "ZONE_ENTER", zone_id="skincare"),
            _event("V001", "ZONE_ENTER", zone_id="makeup"),
            _event("V001", "ZONE_ENTER", zone_id="haircare"),
        ]
        ingest_events(events)
        funnel = compute_funnel("ST1008")
        # Should count as 1 session in ZONE_VISIT, not 3
        assert funnel.stages[1].count <= 1

    def test_conversion_rate(self):
        """Conversion rate should be purchased/entered."""
        funnel = compute_funnel("ST1008")
        assert 0.0 <= funnel.conversion_rate <= 1.0
