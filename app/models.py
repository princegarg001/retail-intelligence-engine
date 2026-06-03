"""
Store Intelligence — Data Models (PS3f Aligned)

AI-Assisted Decision: Pydantic v2 chosen for schema validation because it provides
automatic JSON serialization, strict type checking, and field-level validators that
map directly to the challenge's event schema requirements.

Edit Rationale: Models updated to exactly match the PS3f sample_events.jsonl schema
which defines three distinct event shapes:
  1. Entry/Exit events: id_token, store_code, camera_id, event_timestamp, gender_pred,
     age_pred, age_bucket, is_face_hidden, group_id, group_size
  2. Zone events: track_id, store_id, camera_id, zone_id, zone_name, zone_type,
     is_revenue_zone, event_time, zone_hotspot_x/y, gender, age, age_bucket
  3. Queue events: queue_event_id, track_id, store_id, camera_id, zone_id, zone_name,
     zone_type, queue_join_ts, queue_served_ts, queue_exit_ts, wait_seconds,
     queue_position_at_join, abandoned
POS model updated to match new CSV: order_id, order_date, order_time, store_id,
product_id, brand_name, total_amount.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Event types required by PS3f specification
# ---------------------------------------------------------------------------
class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


# Map PS3f sample event_type strings to our internal enum
PS3F_EVENT_MAP = {
    "entry": EventType.ENTRY,
    "exit": EventType.EXIT,
    "zone_entered": EventType.ZONE_ENTER,
    "zone_exited": EventType.ZONE_EXIT,
    "zone_dwell": EventType.ZONE_DWELL,
    "queue_completed": EventType.BILLING_QUEUE_JOIN,
    "queue_abandoned": EventType.BILLING_QUEUE_ABANDON,
    "reentry": EventType.REENTRY,
}


# ---------------------------------------------------------------------------
# Anomaly severity
# ---------------------------------------------------------------------------
class AnomalySeverity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class AnomalyType(str, Enum):
    BILLING_QUEUE_SPIKE = "BILLING_QUEUE_SPIKE"
    CONVERSION_DROP = "CONVERSION_DROP"
    DEAD_ZONE = "DEAD_ZONE"
    STALE_FEED = "STALE_FEED"
    FOOTFALL_ANOMALY = "FOOTFALL_ANOMALY"
    GROUP_SURGE = "GROUP_SURGE"


# ---------------------------------------------------------------------------
# Event metadata — enriched for PS3f
# ---------------------------------------------------------------------------
class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    zone_alias: Optional[str] = None
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    is_revenue_zone: Optional[bool] = None
    session_sequence: Optional[int] = None
    ambiguity: Optional[bool] = None
    direction_confidence: Optional[float] = None
    group_id: Optional[str] = None
    group_size: Optional[int] = None
    bbox: Optional[list[float]] = None
    gender_pred: Optional[str] = None
    age_pred: Optional[int] = None
    age_bucket: Optional[str] = None
    is_face_hidden: Optional[bool] = None
    zone_hotspot_x: Optional[float] = None
    zone_hotspot_y: Optional[float] = None
    queue_join_ts: Optional[str] = None
    queue_served_ts: Optional[str] = None
    queue_exit_ts: Optional[str] = None
    wait_seconds: Optional[int] = None
    queue_position_at_join: Optional[int] = None
    abandoned: Optional[bool] = None
    is_partial_occlusion: Optional[bool] = None
    cross_camera_linked: Optional[bool] = None


# ---------------------------------------------------------------------------
# Core event schema — PS3f aligned
# ---------------------------------------------------------------------------
class StoreEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: EventType
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: Optional[int] = 0
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, v: Any) -> datetime:
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    @field_validator("store_id", "camera_id", "visitor_id")
    @classmethod
    def must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field must not be empty")
        return v.strip()


# ---------------------------------------------------------------------------
# PS3f native event ingestion — maps sample_events.jsonl format
# ---------------------------------------------------------------------------
class PS3fEntryExitEvent(BaseModel):
    """Entry/Exit events in PS3f native format."""
    event_type: str
    id_token: str
    store_code: str
    camera_id: str
    event_timestamp: str
    is_staff: bool = False
    gender_pred: Optional[str] = None
    age_pred: Optional[int] = None
    age_bucket: Optional[str] = None
    is_face_hidden: Optional[bool] = None
    group_id: Optional[str] = None
    group_size: Optional[int] = None

    def to_store_event(self) -> StoreEvent:
        """Convert PS3f native event to internal StoreEvent."""
        mapped_type = PS3F_EVENT_MAP.get(self.event_type.lower(), EventType.ENTRY)
        return StoreEvent(
            store_id=self.store_code,
            camera_id=self.camera_id,
            visitor_id=self.id_token,
            event_type=mapped_type,
            timestamp=self.event_timestamp,
            is_staff=self.is_staff,
            confidence=0.9,
            metadata=EventMetadata(
                gender_pred=self.gender_pred,
                age_pred=self.age_pred,
                age_bucket=self.age_bucket,
                is_face_hidden=self.is_face_hidden,
                group_id=self.group_id,
                group_size=self.group_size,
            ),
        )


class PS3fZoneEvent(BaseModel):
    """Zone enter/exit events in PS3f native format."""
    event_type: str
    track_id: int
    store_id: str
    camera_id: str
    zone_id: str
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    is_revenue_zone: Optional[str] = None
    event_time: str
    zone_hotspot_x: Optional[float] = None
    zone_hotspot_y: Optional[float] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    age_bucket: Optional[str] = None

    def to_store_event(self) -> StoreEvent:
        mapped_type = PS3F_EVENT_MAP.get(self.event_type.lower(), EventType.ZONE_ENTER)
        return StoreEvent(
            store_id=self.store_id,
            camera_id=self.camera_id,
            visitor_id=f"TRK_{self.track_id:05d}",
            event_type=mapped_type,
            timestamp=self.event_time,
            zone_id=self.zone_id,
            confidence=0.85,
            metadata=EventMetadata(
                zone_name=self.zone_name,
                zone_type=self.zone_type,
                is_revenue_zone=self.is_revenue_zone == "Yes",
                zone_hotspot_x=self.zone_hotspot_x,
                zone_hotspot_y=self.zone_hotspot_y,
                gender_pred=self.gender,
                age_pred=self.age,
                age_bucket=self.age_bucket,
            ),
        )


class PS3fQueueEvent(BaseModel):
    """Queue events in PS3f native format."""
    queue_event_id: str
    event_type: str
    track_id: int
    store_id: str
    camera_id: str
    zone_id: str
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    is_revenue_zone: Optional[str] = None
    queue_join_ts: Optional[str] = None
    queue_served_ts: Optional[str] = None
    queue_exit_ts: Optional[str] = None
    wait_seconds: Optional[int] = None
    queue_position_at_join: Optional[int] = None
    abandoned: bool = False
    zone_hotspot_x: Optional[float] = None
    zone_hotspot_y: Optional[float] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    age_bucket: Optional[str] = None

    def to_store_event(self) -> StoreEvent:
        if self.abandoned:
            mapped_type = EventType.BILLING_QUEUE_ABANDON
        else:
            mapped_type = EventType.BILLING_QUEUE_JOIN

        # Use queue_join_ts as the event timestamp
        ts = self.queue_join_ts or self.queue_exit_ts or "2026-01-01T00:00:00"
        dwell = (self.wait_seconds or 0) * 1000  # Convert to ms

        return StoreEvent(
            event_id=self.queue_event_id,
            store_id=self.store_id,
            camera_id=self.camera_id,
            visitor_id=f"TRK_{self.track_id:05d}",
            event_type=mapped_type,
            timestamp=ts,
            zone_id=self.zone_id,
            dwell_ms=dwell,
            confidence=0.88,
            metadata=EventMetadata(
                zone_name=self.zone_name,
                zone_type=self.zone_type,
                is_revenue_zone=self.is_revenue_zone == "Yes",
                queue_join_ts=self.queue_join_ts,
                queue_served_ts=self.queue_served_ts,
                queue_exit_ts=self.queue_exit_ts,
                wait_seconds=self.wait_seconds,
                queue_position_at_join=self.queue_position_at_join,
                abandoned=self.abandoned,
                zone_hotspot_x=self.zone_hotspot_x,
                zone_hotspot_y=self.zone_hotspot_y,
                gender_pred=self.gender,
                age_pred=self.age,
                age_bucket=self.age_bucket,
            ),
        )


def parse_ps3f_event(raw: dict) -> Optional[StoreEvent]:
    """
    Auto-detect and parse a PS3f native event into a StoreEvent.
    Handles all three PS3f event shapes plus the internal format.
    """
    # Already in internal format
    if "visitor_id" in raw and "event_type" in raw:
        et = raw.get("event_type", "")
        if et in [e.value for e in EventType]:
            return StoreEvent(**raw)

    # PS3f queue event (has queue_event_id)
    if "queue_event_id" in raw:
        return PS3fQueueEvent(**raw).to_store_event()

    # PS3f zone event (has track_id + zone_id + event_time)
    if "track_id" in raw and "zone_id" in raw and "event_time" in raw:
        return PS3fZoneEvent(**raw).to_store_event()

    # PS3f entry/exit event (has id_token + store_code)
    if "id_token" in raw and "store_code" in raw:
        return PS3fEntryExitEvent(**raw).to_store_event()

    # Try raw uppercase event_type
    et = raw.get("event_type", "")
    mapped = PS3F_EVENT_MAP.get(et.lower())
    if mapped:
        raw_copy = dict(raw)
        raw_copy["event_type"] = mapped.value
        # Map common PS3f field aliases
        if "event_timestamp" in raw_copy and "timestamp" not in raw_copy:
            raw_copy["timestamp"] = raw_copy.pop("event_timestamp")
        if "id_token" in raw_copy and "visitor_id" not in raw_copy:
            raw_copy["visitor_id"] = raw_copy.pop("id_token")
        if "store_code" in raw_copy and "store_id" not in raw_copy:
            raw_copy["store_id"] = raw_copy.pop("store_code")
        if "camera_id" not in raw_copy:
            raw_copy["camera_id"] = "CAM_UNKNOWN"
        if "confidence" not in raw_copy:
            raw_copy["confidence"] = 0.8
        return StoreEvent(**raw_copy)

    return None


# ---------------------------------------------------------------------------
# Ingest response
# ---------------------------------------------------------------------------
class IngestError(BaseModel):
    index: int
    reason: str


class IngestResponse(BaseModel):
    accepted: int = 0
    rejected: int = 0
    duplicate: int = 0
    errors: list[IngestError] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Store metrics response
# ---------------------------------------------------------------------------
class StoreMetrics(BaseModel):
    store_id: str
    unique_visitors_today: int = 0
    total_footfall: int = 0
    staff_count: int = 0
    conversion_rate: float = 0.0
    avg_dwell_per_zone: dict[str, float] = Field(default_factory=dict)
    queue_depth: int = 0
    avg_queue_wait_seconds: float = 0.0
    abandonment_rate: float = 0.0
    group_entries: int = 0
    reentry_count: int = 0
    last_event_timestamp: Optional[datetime] = None
    data_freshness: str = "unknown"  # "fresh" | "stale" | "unknown"
    gender_distribution: dict[str, int] = Field(default_factory=dict)
    age_distribution: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Funnel response
# ---------------------------------------------------------------------------
class FunnelStage(BaseModel):
    stage: str
    count: int
    drop_off_pct: float = 0.0


class FunnelResponse(BaseModel):
    store_id: str
    stages: list[FunnelStage] = Field(default_factory=list)
    conversion_rate: float = 0.0
    total_sessions: int = 0
    pos_transactions: int = 0
    pos_revenue: float = 0.0


# ---------------------------------------------------------------------------
# Heatmap response
# ---------------------------------------------------------------------------
class ZoneHeatmapEntry(BaseModel):
    zone_id: str
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    is_revenue_zone: bool = False
    visit_frequency: int = 0
    avg_dwell_ms: float = 0.0
    normalised_score: float = 0.0  # 0–100
    data_confidence: str = "high"  # "high" | "medium" | "low"


class HeatmapResponse(BaseModel):
    store_id: str
    zones: list[ZoneHeatmapEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Anomaly response
# ---------------------------------------------------------------------------
class AnomalyRecord(BaseModel):
    type: AnomalyType
    severity: AnomalySeverity
    evidence: str
    current_value: float
    baseline_value: float
    suggested_action: str
    detected_at: datetime = Field(default_factory=datetime.utcnow)


class AnomalyResponse(BaseModel):
    store_id: str
    anomalies: list[AnomalyRecord] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Health response — enriched with per-camera health
# ---------------------------------------------------------------------------
class CameraHealth(BaseModel):
    camera_id: str
    last_event_timestamp: Optional[datetime] = None
    event_count: int = 0
    stale: bool = False


class StoreHealth(BaseModel):
    store_id: str
    last_event_timestamp: Optional[datetime] = None
    stale_feed_warning: bool = False
    cameras: list[CameraHealth] = Field(default_factory=list)
    total_events: int = 0


class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str = "2.0.0-ps3f"
    stores: list[StoreHealth] = Field(default_factory=list)
    uptime_seconds: Optional[float] = None


# ---------------------------------------------------------------------------
# Session model — enriched for PS3f edge cases
# ---------------------------------------------------------------------------
class VisitorSession(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id: str
    visitor_id: str
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    is_staff: bool = False
    zones_visited: list[str] = Field(default_factory=list)
    entered_billing: bool = False
    converted: bool = False
    total_dwell_ms: int = 0
    event_count: int = 0
    is_reentry: bool = False
    group_id: Optional[str] = None
    group_size: Optional[int] = None
    gender_pred: Optional[str] = None
    age_bucket: Optional[str] = None
    has_partial_occlusion: bool = False
    cross_camera_linked: bool = False
    queue_wait_seconds: Optional[int] = None
    queue_abandoned: bool = False


# ---------------------------------------------------------------------------
# POS transaction model — updated for PS3f CSV schema
# ---------------------------------------------------------------------------
class POSTransaction(BaseModel):
    order_id: str
    store_id: str
    order_date: str
    order_time: str
    product_id: Optional[str] = None
    brand_name: Optional[str] = None
    total_amount: float = 0.0
    # Legacy fields kept for backward compatibility
    store_name: Optional[str] = None
    customer_name: Optional[str] = None
    invoice_number: Optional[str] = None
    timestamp: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Store layout model
# ---------------------------------------------------------------------------
class StoreLayoutZone(BaseModel):
    zone_id: str
    zone_name: str
    zone_type: str  # SHELF, DISPLAY, BILLING, ENTRY, etc.
    is_revenue_zone: bool = False
    camera_id: Optional[str] = None
    coordinates: Optional[dict] = None


class StoreLayout(BaseModel):
    store_id: str
    store_name: Optional[str] = None
    zones: list[StoreLayoutZone] = Field(default_factory=list)
    cameras: list[str] = Field(default_factory=list)
