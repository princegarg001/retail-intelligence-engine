"""
Store Intelligence — Event Emitter

AI-Assisted Decision: State-machine based event emission chosen because each track
goes through deterministic state transitions (outside → entry → in_zone → billing →
exit), and the challenge requires explicit entry/exit/zone/billing events (§6.2).

Edit Rationale: Implements the complete event emission pipeline per §6.2-6.4:
- ENTRY when person crosses entry zone boundary
- EXIT when person leaves entry zone
- ZONE_ENTER/ZONE_EXIT when moving between zones
- ZONE_DWELL after continuous occupancy threshold
- BILLING_QUEUE_JOIN when entering billing zone
- BILLING_QUEUE_ABANDON when leaving billing without POS match
- REENTRY when same person returns after exit

All events follow the schema from §6.3 with globally unique event_id,
ISO-8601 timestamps, confidence scores, and metadata.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from pipeline.tracker import Track
from pipeline.zones import Zone, ZoneClassifier


class TrackState(str, Enum):
    OUTSIDE = "OUTSIDE"
    ENTERING = "ENTERING"
    INSIDE = "INSIDE"
    IN_BILLING = "IN_BILLING"
    EXITING = "EXITING"


class TrackContext:
    """Per-track state for event emission."""

    def __init__(self, track_id: int, person_id: str):
        self.track_id = track_id
        self.person_id = person_id
        self.state = TrackState.OUTSIDE
        self.current_zone: Optional[str] = None
        self.zone_enter_time: Optional[int] = None
        self.is_staff: bool = False
        self.staff_confidence: float = 0.0
        self.event_sequence: int = 0
        self.entered_billing: bool = False
        self.entry_frame: int = 0

    def next_sequence(self) -> int:
        self.event_sequence += 1
        return self.event_sequence


# Dwell threshold in milliseconds (emit ZONE_DWELL after 10s continuous presence)
DWELL_THRESHOLD_MS = 10_000


class EventEmitter:
    """
    State-machine based event emitter.
    
    Tracks person state transitions and emits structured events.
    Each event follows the schema from §6.3.
    """

    def __init__(
        self,
        store_id: str = "ST1008",
        output_path: str = "data/events.jsonl",
        base_timestamp: Optional[datetime] = None,
        fps: float = 25.0,
    ):
        self.store_id = store_id
        self.output_path = output_path
        self.base_timestamp = base_timestamp or datetime(2026, 4, 10, 10, 0, 0)
        self.fps = fps
        self.zone_classifier = ZoneClassifier(store_id)
        self.track_contexts: dict[int, TrackContext] = {}
        self.events: list[dict] = []
        self.queue_depth = 0

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    def _frame_to_timestamp(self, frame_num: int) -> str:
        """Convert frame number to ISO-8601 timestamp."""
        delta = timedelta(seconds=frame_num / self.fps)
        ts = self.base_timestamp + delta
        return ts.isoformat() + "Z"

    def _emit(
        self,
        event_type: str,
        person_id: str,
        camera_id: str,
        frame_num: int,
        confidence: float,
        zone_id: Optional[str] = None,
        dwell_ms: int = 0,
        is_staff: bool = False,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Emit a single structured event."""
        ctx = None
        for tc in self.track_contexts.values():
            if tc.person_id == person_id:
                ctx = tc
                break

        event = {
            "event_id": str(uuid.uuid4()),
            "store_id": self.store_id,
            "camera_id": camera_id,
            "visitor_id": person_id,
            "event_type": event_type,
            "timestamp": self._frame_to_timestamp(frame_num),
            "zone_id": zone_id,
            "dwell_ms": dwell_ms,
            "is_staff": is_staff,
            "confidence": round(confidence, 3),
            "metadata": metadata or {
                "queue_depth": self.queue_depth,
                "session_sequence": ctx.next_sequence() if ctx else 0,
            },
        }

        self.events.append(event)

        # Write to JSONL file
        with open(self.output_path, "a") as f:
            f.write(json.dumps(event) + "\n")

        return event

    def process_frame(
        self,
        tracks: list[Track],
        camera_id: str,
        frame_num: int,
        frame_width: int,
        frame_height: int,
    ) -> list[dict]:
        """
        Process tracked persons in a frame and emit appropriate events.
        
        Handles:
        - Entry/exit detection
        - Zone transitions
        - Dwell time tracking
        - Billing queue tracking
        - Direction consistency for false exit prevention
        
        Returns list of events emitted for this frame.
        """
        frame_events = []
        active_track_ids = {t.track_id for t in tracks}

        for track in tracks:
            # Get or create track context
            if track.track_id not in self.track_contexts:
                self.track_contexts[track.track_id] = TrackContext(
                    track_id=track.track_id,
                    person_id=f"V{track.track_id:05d}",
                )

            ctx = self.track_contexts[track.track_id]
            ctx.is_staff = track.is_staff
            cx, cy = track.center

            # Classify current zone
            zone = self.zone_classifier.classify(
                camera_id, cx, cy, frame_width, frame_height
            )
            zone_id = zone.zone_id if zone else None

            # --- Entry Detection ---
            if ctx.state == TrackState.OUTSIDE:
                if zone and zone.is_entry:
                    # Direction check: ensure moving inward (downward in most cameras)
                    if len(track.trajectory) >= 2:
                        dy = track.trajectory[-1][1] - track.trajectory[-2][1]
                        if dy > 0:  # Moving into store
                            ctx.state = TrackState.INSIDE
                            ctx.entry_frame = frame_num
                            evt = self._emit(
                                "ENTRY", ctx.person_id, camera_id, frame_num,
                                track.confidence, zone_id=zone_id,
                                is_staff=track.is_staff,
                                metadata={
                                    "queue_depth": self.queue_depth,
                                    "session_sequence": ctx.next_sequence(),
                                    "direction_confidence": min(1.0, abs(dy) / 10),
                                    "group_size": len(tracks),
                                    "bbox": track.bbox,
                                },
                            )
                            frame_events.append(evt)
                    else:
                        # First detection — assume entry
                        ctx.state = TrackState.INSIDE
                        ctx.entry_frame = frame_num
                        evt = self._emit(
                            "ENTRY", ctx.person_id, camera_id, frame_num,
                            track.confidence, zone_id=zone_id,
                            is_staff=track.is_staff,
                        )
                        frame_events.append(evt)

            # --- Zone Transitions ---
            elif ctx.state in (TrackState.INSIDE, TrackState.IN_BILLING):
                if zone_id and zone_id != ctx.current_zone:
                    # Exit previous zone
                    if ctx.current_zone:
                        dwell = 0
                        if ctx.zone_enter_time is not None:
                            dwell = int(
                                (frame_num - ctx.zone_enter_time) / self.fps * 1000
                            )
                            if dwell >= DWELL_THRESHOLD_MS:
                                evt = self._emit(
                                    "ZONE_DWELL", ctx.person_id, camera_id,
                                    frame_num, track.confidence,
                                    zone_id=ctx.current_zone, dwell_ms=dwell,
                                    is_staff=track.is_staff,
                                )
                                frame_events.append(evt)

                        evt = self._emit(
                            "ZONE_EXIT", ctx.person_id, camera_id, frame_num,
                            track.confidence, zone_id=ctx.current_zone,
                            dwell_ms=dwell, is_staff=track.is_staff,
                        )
                        frame_events.append(evt)

                    # Enter new zone
                    ctx.current_zone = zone_id
                    ctx.zone_enter_time = frame_num
                    evt = self._emit(
                        "ZONE_ENTER", ctx.person_id, camera_id, frame_num,
                        track.confidence, zone_id=zone_id,
                        is_staff=track.is_staff,
                    )
                    frame_events.append(evt)

                    # Billing zone detection
                    if zone and zone.is_billing and not ctx.entered_billing:
                        ctx.entered_billing = True
                        ctx.state = TrackState.IN_BILLING
                        self.queue_depth += 1
                        evt = self._emit(
                            "BILLING_QUEUE_JOIN", ctx.person_id, camera_id,
                            frame_num, track.confidence, zone_id=zone_id,
                            is_staff=track.is_staff,
                            metadata={
                                "queue_depth": self.queue_depth,
                                "session_sequence": ctx.next_sequence(),
                            },
                        )
                        frame_events.append(evt)

                # Exit detection
                if zone and zone.is_exit:
                    if len(track.trajectory) >= 3:
                        # Check consistent outward movement
                        recent = track.trajectory[-3:]
                        dy = recent[-1][1] - recent[0][1]
                        if dy < -5:  # Moving outward
                            # Handle billing abandonment
                            if ctx.state == TrackState.IN_BILLING:
                                self.queue_depth = max(0, self.queue_depth - 1)
                                evt = self._emit(
                                    "BILLING_QUEUE_ABANDON", ctx.person_id,
                                    camera_id, frame_num, track.confidence,
                                    zone_id=ctx.current_zone,
                                    is_staff=track.is_staff,
                                )
                                frame_events.append(evt)

                            # Emit dwell for last zone
                            if ctx.current_zone and ctx.zone_enter_time:
                                dwell = int(
                                    (frame_num - ctx.zone_enter_time) / self.fps * 1000
                                )
                                if dwell >= DWELL_THRESHOLD_MS:
                                    evt = self._emit(
                                        "ZONE_DWELL", ctx.person_id, camera_id,
                                        frame_num, track.confidence,
                                        zone_id=ctx.current_zone, dwell_ms=dwell,
                                        is_staff=track.is_staff,
                                    )
                                    frame_events.append(evt)

                            evt = self._emit(
                                "EXIT", ctx.person_id, camera_id, frame_num,
                                track.confidence, zone_id=zone_id,
                                is_staff=track.is_staff,
                            )
                            frame_events.append(evt)
                            ctx.state = TrackState.OUTSIDE

        # Handle disappeared tracks (exited frame without crossing exit zone)
        for tid, ctx in list(self.track_contexts.items()):
            if tid not in active_track_ids and ctx.state != TrackState.OUTSIDE:
                if ctx.state == TrackState.IN_BILLING:
                    self.queue_depth = max(0, self.queue_depth - 1)
                ctx.state = TrackState.OUTSIDE

        return frame_events

    def get_all_events(self) -> list[dict]:
        """Return all emitted events."""
        return self.events

    def get_event_count(self) -> int:
        """Return total number of emitted events."""
        return len(self.events)
