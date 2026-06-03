"""
Store Intelligence — Zone Classification (PS3f Multi-Store)

AI-Assisted Decision: Layout-based geometric zone classification used because the
challenge provides store layout coordinates and camera placements. Rule-based zones
mapped from camera views are more reliable than learned zone classifiers for fixed
retail layouts.

Edit Rationale: Updated for PS3f multi-store support:
- Store 1: 4 cameras (CAM 1 zone, CAM 2 zone, CAM 3 entry, CAM 5 billing)
- Store 2: 4 cameras (entry 1, entry 2, zone, billing_area)
- Each store has 3 camera angles: Entry, Main Floor, Billing Area
- Zone definitions enriched with zone_type (SHELF, DISPLAY, BILLING, ENTRY)
  and is_revenue_zone flag matching PS3f sample events

# CHANGES MADE:
# - Added Store 1 and Store 2 zone definitions based on actual camera names
# - Added zone_type and is_revenue_zone to Zone dataclass
# - Added dynamic store layout loading from JSON
# - Mapped cameras to PS3f spec: Entry Camera, Main Floor Camera, Billing Area Camera
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Zone:
    """A defined zone within the store."""
    zone_id: str
    name: str
    x_min: float  # Normalised 0-1
    y_min: float
    x_max: float
    y_max: float
    is_entry: bool = False
    is_exit: bool = False
    is_billing: bool = False
    zone_type: str = "GENERAL"  # SHELF, DISPLAY, BILLING, ENTRY
    is_revenue_zone: bool = False


# ---------------------------------------------------------------------------
# Multi-store layout definitions
# Store 1 cameras: CAM 1 - zone.mp4, CAM 2 - zone.mp4, CAM 3 - entry.mp4, CAM 5 - billing.mp4
# Store 2 cameras: entry 1.mp4, entry 2.mp4, zone.mp4, billing_area.mp4
# CCTV Footage: CAM 1-5.mp4 (legacy, maps to ST1008)
# ---------------------------------------------------------------------------

STORE_ZONES: dict[str, dict[str, list[Zone]]] = {
    # Legacy store (CCTV Footage directory)
    "ST1008": {
        "CAM_1": [
            Zone(
                zone_id="entrance",
                name="Main Entrance",
                x_min=0.0, y_min=0.0, x_max=1.0, y_max=0.4,
                is_entry=True, is_exit=True,
                zone_type="ENTRY", is_revenue_zone=False,
            ),
            Zone(
                zone_id="entrance_lobby",
                name="Entrance Lobby",
                x_min=0.0, y_min=0.4, x_max=1.0, y_max=1.0,
                zone_type="GENERAL", is_revenue_zone=False,
            ),
        ],
        "CAM_2": [
            Zone(
                zone_id="skincare",
                name="Skincare & Makeup Aisle",
                x_min=0.0, y_min=0.0, x_max=0.5, y_max=1.0,
                zone_type="SHELF", is_revenue_zone=True,
            ),
            Zone(
                zone_id="makeup",
                name="Makeup Display",
                x_min=0.5, y_min=0.0, x_max=1.0, y_max=1.0,
                zone_type="DISPLAY", is_revenue_zone=True,
            ),
        ],
        "CAM_3": [
            Zone(
                zone_id="haircare",
                name="Hair Care Aisle",
                x_min=0.0, y_min=0.0, x_max=0.5, y_max=1.0,
                zone_type="SHELF", is_revenue_zone=True,
            ),
            Zone(
                zone_id="bodycare",
                name="Body Care Section",
                x_min=0.5, y_min=0.0, x_max=1.0, y_max=1.0,
                zone_type="SHELF", is_revenue_zone=True,
            ),
        ],
        "CAM_4": [
            Zone(
                zone_id="billing",
                name="Billing Counter",
                x_min=0.0, y_min=0.0, x_max=1.0, y_max=0.6,
                is_billing=True,
                zone_type="BILLING", is_revenue_zone=True,
            ),
            Zone(
                zone_id="billing_queue",
                name="Billing Queue Area",
                x_min=0.0, y_min=0.6, x_max=1.0, y_max=1.0,
                zone_type="BILLING", is_revenue_zone=True,
            ),
        ],
        "CAM_5": [
            Zone(
                zone_id="fragrance",
                name="Fragrance Section",
                x_min=0.0, y_min=0.0, x_max=0.5, y_max=1.0,
                zone_type="SHELF", is_revenue_zone=True,
            ),
            Zone(
                zone_id="personal_care",
                name="Personal Care",
                x_min=0.5, y_min=0.0, x_max=1.0, y_max=1.0,
                zone_type="SHELF", is_revenue_zone=True,
            ),
        ],
    },

    # Store 1 (PS3f new dataset)
    "STORE_1": {
        # CAM 3 - entry.mp4 → Entry Camera
        "CAM_3_ENTRY": [
            Zone(
                zone_id="store1_entrance",
                name="Store 1 Main Entrance",
                x_min=0.0, y_min=0.0, x_max=1.0, y_max=0.4,
                is_entry=True, is_exit=True,
                zone_type="ENTRY", is_revenue_zone=False,
            ),
            Zone(
                zone_id="store1_entrance_lobby",
                name="Store 1 Entrance Lobby",
                x_min=0.0, y_min=0.4, x_max=1.0, y_max=1.0,
                zone_type="GENERAL", is_revenue_zone=False,
            ),
        ],
        # CAM 1 - zone.mp4 → Main Floor Camera (Left)
        "CAM_1_ZONE": [
            Zone(
                zone_id="store1_left_shelf",
                name="Left Shelf",
                x_min=0.0, y_min=0.0, x_max=0.5, y_max=1.0,
                zone_type="SHELF", is_revenue_zone=True,
            ),
            Zone(
                zone_id="store1_center_display",
                name="Center Display",
                x_min=0.5, y_min=0.0, x_max=1.0, y_max=1.0,
                zone_type="DISPLAY", is_revenue_zone=True,
            ),
        ],
        # CAM 2 - zone.mp4 → Main Floor Camera (Right)
        "CAM_2_ZONE": [
            Zone(
                zone_id="store1_right_shelf",
                name="Right Shelf",
                x_min=0.0, y_min=0.0, x_max=0.5, y_max=1.0,
                zone_type="SHELF", is_revenue_zone=True,
            ),
            Zone(
                zone_id="store1_lipstick_aisle",
                name="Lipstick Aisle",
                x_min=0.5, y_min=0.0, x_max=1.0, y_max=1.0,
                zone_type="SHELF", is_revenue_zone=True,
            ),
        ],
        # CAM 5 - billing.mp4 → Billing Area Camera
        "CAM_5_BILLING": [
            Zone(
                zone_id="store1_billing",
                name="Billing Counter Queue",
                x_min=0.0, y_min=0.0, x_max=1.0, y_max=0.6,
                is_billing=True,
                zone_type="BILLING", is_revenue_zone=True,
            ),
            Zone(
                zone_id="store1_billing_area",
                name="Billing Area",
                x_min=0.0, y_min=0.6, x_max=1.0, y_max=1.0,
                zone_type="BILLING", is_revenue_zone=True,
            ),
        ],
    },

    # Store 2 (PS3f new dataset)
    "STORE_2": {
        # entry 1.mp4 → Entry Camera 1
        "ENTRY_1": [
            Zone(
                zone_id="store2_entrance_1",
                name="Store 2 Entrance 1",
                x_min=0.0, y_min=0.0, x_max=1.0, y_max=0.4,
                is_entry=True, is_exit=True,
                zone_type="ENTRY", is_revenue_zone=False,
            ),
            Zone(
                zone_id="store2_lobby_1",
                name="Store 2 Lobby 1",
                x_min=0.0, y_min=0.4, x_max=1.0, y_max=1.0,
                zone_type="GENERAL", is_revenue_zone=False,
            ),
        ],
        # entry 2.mp4 → Entry Camera 2
        "ENTRY_2": [
            Zone(
                zone_id="store2_entrance_2",
                name="Store 2 Entrance 2",
                x_min=0.0, y_min=0.0, x_max=1.0, y_max=0.4,
                is_entry=True, is_exit=True,
                zone_type="ENTRY", is_revenue_zone=False,
            ),
            Zone(
                zone_id="store2_lobby_2",
                name="Store 2 Lobby 2",
                x_min=0.0, y_min=0.4, x_max=1.0, y_max=1.0,
                zone_type="GENERAL", is_revenue_zone=False,
            ),
        ],
        # zone.mp4 → Main Floor Camera
        "ZONE_MAIN": [
            Zone(
                zone_id="store2_shelf_left",
                name="Left Product Shelf",
                x_min=0.0, y_min=0.0, x_max=0.33, y_max=1.0,
                zone_type="SHELF", is_revenue_zone=True,
            ),
            Zone(
                zone_id="store2_display_center",
                name="Center Display Area",
                x_min=0.33, y_min=0.0, x_max=0.66, y_max=1.0,
                zone_type="DISPLAY", is_revenue_zone=True,
            ),
            Zone(
                zone_id="store2_shelf_right",
                name="Right Product Shelf",
                x_min=0.66, y_min=0.0, x_max=1.0, y_max=1.0,
                zone_type="SHELF", is_revenue_zone=True,
            ),
        ],
        # billing_area.mp4 → Billing Area Camera
        "BILLING_AREA": [
            Zone(
                zone_id="store2_billing",
                name="Billing Counter",
                x_min=0.0, y_min=0.0, x_max=1.0, y_max=0.6,
                is_billing=True,
                zone_type="BILLING", is_revenue_zone=True,
            ),
            Zone(
                zone_id="store2_billing_queue",
                name="Billing Queue",
                x_min=0.0, y_min=0.6, x_max=1.0, y_max=1.0,
                zone_type="BILLING", is_revenue_zone=True,
            ),
        ],
    },

    # Generic fallback for sample events store (store_1076)
    "store_1076": {
        "cam1": [
            Zone(zone_id="entry_zone", name="Entry/Exit", x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0,
                 is_entry=True, is_exit=True, zone_type="ENTRY"),
        ],
        "CAM2": [
            Zone(zone_id="PURPLLE_MUM_1076_Z01", name="Left Shelf", x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0,
                 zone_type="SHELF", is_revenue_zone=True),
        ],
        "CAM3": [
            Zone(zone_id="PURPLLE_MUM_1076_Z02", name="Center Display", x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0,
                 zone_type="DISPLAY", is_revenue_zone=True),
        ],
        "CAM4": [
            Zone(zone_id="PURPLLE_MUM_1076_Z03", name="Lipstick Aisle", x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0,
                 zone_type="SHELF", is_revenue_zone=True),
        ],
        "PURPLLE_MUM_1076_CAM6": [
            Zone(zone_id="PURPLLE_MUM_1076_Z_BILLING_01", name="Billing Counter Queue", x_min=0.0, y_min=0.0,
                 x_max=1.0, y_max=1.0, is_billing=True, zone_type="BILLING", is_revenue_zone=True),
        ],
    },

    # ST1076 alias
    "ST1076": {
        "cam1": [
            Zone(zone_id="entry_zone", name="Entry/Exit", x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0,
                 is_entry=True, is_exit=True, zone_type="ENTRY"),
        ],
        "CAM2": [
            Zone(zone_id="PURPLLE_MUM_1076_Z01", name="Left Shelf", x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0,
                 zone_type="SHELF", is_revenue_zone=True),
        ],
        "CAM3": [
            Zone(zone_id="PURPLLE_MUM_1076_Z02", name="Center Display", x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0,
                 zone_type="DISPLAY", is_revenue_zone=True),
        ],
        "CAM4": [
            Zone(zone_id="PURPLLE_MUM_1076_Z03", name="Lipstick Aisle", x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0,
                 zone_type="SHELF", is_revenue_zone=True),
        ],
        "PURPLLE_MUM_1076_CAM6": [
            Zone(zone_id="PURPLLE_MUM_1076_Z_BILLING_01", name="Billing Counter Queue", x_min=0.0, y_min=0.0,
                 x_max=1.0, y_max=1.0, is_billing=True, zone_type="BILLING", is_revenue_zone=True),
        ],
    },
}


class ZoneClassifier:
    """
    Classifies person positions into store zones.
    Uses camera-specific zone maps with normalised coordinates.
    Supports dynamic zone loading from store_layout.json.
    """

    def __init__(self, store_id: str = "ST1008"):
        self.store_id = store_id
        self.zones = STORE_ZONES.get(store_id, {})

        # Try to load dynamic layout
        self._load_dynamic_layout()

    def _load_dynamic_layout(self) -> None:
        """Load zone definitions from store_layout.json if available."""
        layout_path = os.environ.get("STORE_LAYOUT_PATH", "data/store_layout.json")
        if not os.path.exists(layout_path):
            return

        try:
            with open(layout_path, "r") as f:
                data = json.load(f)

            if isinstance(data, dict) and data.get("store_id") == self.store_id:
                self._parse_layout(data)
            elif isinstance(data, list):
                for layout in data:
                    if layout.get("store_id") == self.store_id:
                        self._parse_layout(layout)
                        break
        except Exception:
            pass  # Fall back to hardcoded zones

    def _parse_layout(self, layout: dict) -> None:
        """Parse a layout JSON into zone definitions."""
        zones_data = layout.get("zones", [])
        for zone_data in zones_data:
            camera_id = zone_data.get("camera_id", "DEFAULT")
            if camera_id not in self.zones:
                self.zones[camera_id] = []

            coords = zone_data.get("coordinates", {})
            self.zones[camera_id].append(
                Zone(
                    zone_id=zone_data.get("zone_id", "unknown"),
                    name=zone_data.get("zone_name", "Unknown Zone"),
                    x_min=coords.get("x_min", 0.0),
                    y_min=coords.get("y_min", 0.0),
                    x_max=coords.get("x_max", 1.0),
                    y_max=coords.get("y_max", 1.0),
                    is_entry=zone_data.get("zone_type") == "ENTRY",
                    is_exit=zone_data.get("zone_type") == "ENTRY",
                    is_billing=zone_data.get("zone_type") == "BILLING",
                    zone_type=zone_data.get("zone_type", "GENERAL"),
                    is_revenue_zone=zone_data.get("is_revenue_zone", False),
                )
            )

    def classify(
        self,
        camera_id: str,
        center_x: float,
        center_y: float,
        frame_width: int,
        frame_height: int,
    ) -> Optional[Zone]:
        """
        Classify a person's position into a zone.

        Args:
            camera_id: Camera identifier
            center_x: Person center X in pixel coordinates
            center_y: Person center Y in pixel coordinates
            frame_width: Frame width in pixels
            frame_height: Frame height in pixels

        Returns:
            Zone object if person is in a defined zone, None otherwise.
        """
        # Normalise to 0-1
        nx = center_x / frame_width if frame_width > 0 else 0
        ny = center_y / frame_height if frame_height > 0 else 0

        camera_zones = self.zones.get(camera_id, [])
        for zone in camera_zones:
            if (
                zone.x_min <= nx <= zone.x_max
                and zone.y_min <= ny <= zone.y_max
            ):
                return zone

        return None

    def is_entry_zone(self, camera_id: str, center_x: float, center_y: float,
                      frame_width: int, frame_height: int) -> bool:
        """Check if a person is in the entry zone."""
        zone = self.classify(camera_id, center_x, center_y, frame_width, frame_height)
        return zone is not None and zone.is_entry

    def is_exit_zone(self, camera_id: str, center_x: float, center_y: float,
                     frame_width: int, frame_height: int) -> bool:
        """Check if a person is in the exit zone."""
        zone = self.classify(camera_id, center_x, center_y, frame_width, frame_height)
        return zone is not None and zone.is_exit

    def is_billing_zone(self, camera_id: str, center_x: float, center_y: float,
                        frame_width: int, frame_height: int) -> bool:
        """Check if a person is in the billing zone."""
        zone = self.classify(camera_id, center_x, center_y, frame_width, frame_height)
        return zone is not None and zone.is_billing

    def get_all_zones(self) -> list[Zone]:
        """Get all defined zones across all cameras."""
        all_zones = []
        seen = set()
        for camera_zones in self.zones.values():
            for zone in camera_zones:
                if zone.zone_id not in seen:
                    all_zones.append(zone)
                    seen.add(zone.zone_id)
        return all_zones
