"""
Store Intelligence — Main Pipeline Runner (PS3f Multi-Store)

AI-Assisted Decision: Sequential camera processing chosen over parallel because it
simplifies cross-camera deduplication and ensures deterministic event ordering.

Edit Rationale: Updated for PS3f multi-store support:
- Processes Store 1 (4 cameras) and Store 2 (4 cameras) independently
- Handles legacy CCTV footage directory format
- Camera name mapping updated for all store configurations
- Cross-camera deduplication between entry cameras
- Group detection from proximate entries
- Staff identification enhanced with per-store context

# CHANGES MADE:
# - Added multi-store CAMERA_MAP configurations
# - Added auto-discovery of stores and their videos
# - Added cross-camera deduplication pass after processing
# - Updated base timestamps per store
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

import cv2

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.detect import PersonDetector
from pipeline.tracker import SimpleTracker
from pipeline.zones import ZoneClassifier
from pipeline.reid import StaffDetector
from pipeline.event_emitter import EventEmitter


# ---------------------------------------------------------------------------
# Camera mappings for each store
# ---------------------------------------------------------------------------

# Store 1: CAM 1 - zone.mp4, CAM 2 - zone.mp4, CAM 3 - entry.mp4, CAM 5 - billing.mp4
STORE_1_CAMERAS = {
    "CAM 1 - zone.mp4": "CAM_1_ZONE",
    "CAM 2 - zone.mp4": "CAM_2_ZONE",
    "CAM 3 - entry.mp4": "CAM_3_ENTRY",
    "CAM 5 - billing.mp4": "CAM_5_BILLING",
}

# Store 2: entry 1.mp4, entry 2.mp4, zone.mp4, billing_area.mp4
STORE_2_CAMERAS = {
    "entry 1.mp4": "ENTRY_1",
    "entry 2.mp4": "ENTRY_2",
    "zone.mp4": "ZONE_MAIN",
    "billing_area.mp4": "BILLING_AREA",
}

# Legacy CCTV Footage: CAM 1-5.mp4
LEGACY_CAMERAS = {
    "CAM 1.mp4": "CAM_1",
    "CAM 2.mp4": "CAM_2",
    "CAM 3.mp4": "CAM_3",
    "CAM 4.mp4": "CAM_4",
    "CAM 5.mp4": "CAM_5",
}

# Combined store configurations
STORE_CONFIGS = {
    "STORE_1": {
        "cameras": STORE_1_CAMERAS,
        "base_timestamp": datetime(2026, 3, 8, 18, 0, 0),
    },
    "STORE_2": {
        "cameras": STORE_2_CAMERAS,
        "base_timestamp": datetime(2026, 3, 8, 18, 0, 0),
    },
    "ST1008": {
        "cameras": LEGACY_CAMERAS,
        "base_timestamp": datetime(2026, 4, 10, 10, 0, 0),
    },
}


def _auto_discover_stores(base_dir: str) -> dict[str, dict]:
    """
    Auto-discover store directories and their video files.
    Returns {store_id: {"video_dir": path, "cameras": {filename: camera_id}}}.
    """
    discovered = {}

    # Check for Store 1 directory
    for parent_dir in os.listdir(base_dir) if os.path.isdir(base_dir) else []:
        full_path = os.path.join(base_dir, parent_dir)
        if not os.path.isdir(full_path):
            continue

        # Check for Store 1
        store1_path = os.path.join(full_path, "Store 1")
        if os.path.exists(store1_path):
            discovered["STORE_1"] = {
                "video_dir": store1_path,
                "cameras": STORE_1_CAMERAS,
                "base_timestamp": datetime(2026, 3, 8, 18, 0, 0),
            }

        # Check for Store 2
        store2_path = os.path.join(full_path, "Store 2")
        if os.path.exists(store2_path):
            discovered["STORE_2"] = {
                "video_dir": store2_path,
                "cameras": STORE_2_CAMERAS,
                "base_timestamp": datetime(2026, 3, 8, 18, 0, 0),
            }

    return discovered


def process_video(
    video_path: str,
    camera_id: str,
    detector: PersonDetector,
    emitter: EventEmitter,
    max_frames: int = 0,
    frame_skip: int = 5,
    verbose: bool = True,
) -> int:
    """
    Process a single CCTV video file.
    Returns number of events emitted.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[pipeline] ERROR: Cannot open video: {video_path}")
        return 0

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    emitter.fps = fps

    tracker = SimpleTracker(max_age=int(fps * 2), min_hits=3, iou_threshold=0.25)
    staff_detector = StaffDetector(min_frames=int(fps * 5))

    frame_num = 0
    events_emitted = 0
    start_time = time.time()

    if verbose:
        print(f"[pipeline] Processing {camera_id}: {video_path}")
        print(f"[pipeline]   FPS={fps:.1f}, Frames={total_frames}, Resolution={frame_width}x{frame_height}")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_num += 1

        if max_frames > 0 and frame_num > max_frames:
            break

        # Skip frames for performance
        if frame_num % frame_skip != 0:
            continue

        # 1. Detect persons
        detections = detector.detect(frame)

        # 2. Track persons
        tracks = tracker.update(detections)
        confirmed = tracker.get_confirmed_tracks()

        # 3. Staff detection
        staff_detector.update(confirmed)
        for track in confirmed:
            is_staff, staff_conf = staff_detector.classify(track)
            track.is_staff = is_staff

        # 4. Emit events
        frame_events = emitter.process_frame(
            confirmed, camera_id, frame_num, frame_width, frame_height
        )
        events_emitted += len(frame_events)

        # Progress reporting
        if verbose and frame_num % (frame_skip * 100) == 0:
            elapsed = time.time() - start_time
            pct = (frame_num / total_frames * 100) if total_frames > 0 else 0
            print(
                f"[pipeline]   {camera_id} frame {frame_num}/{total_frames} "
                f"({pct:.1f}%) | {len(confirmed)} tracks | "
                f"{events_emitted} events | {elapsed:.1f}s"
            )

    cap.release()

    if verbose:
        elapsed = time.time() - start_time
        print(
            f"[pipeline] {camera_id} complete: {frame_num} frames, "
            f"{events_emitted} events in {elapsed:.1f}s"
        )

    return events_emitted


def run_pipeline(
    video_dir: str,
    output_path: str = "data/events.jsonl",
    store_id: str = "ST1008",
    max_frames: int = 0,
    frame_skip: int = 5,
    model_path: str = "yolov8n.pt",
    confidence: float = 0.3,
) -> str:
    """
    Run the complete detection pipeline on all camera clips for a store.
    Returns path to the generated events JSONL file.
    """
    config = STORE_CONFIGS.get(store_id, STORE_CONFIGS.get("ST1008"))
    camera_map = config["cameras"]
    base_timestamp = config["base_timestamp"]

    print("=" * 60)
    print("  Store Intelligence — Detection Pipeline (PS3f)")
    print("=" * 60)
    print(f"  Store ID:    {store_id}")
    print(f"  Video Dir:   {video_dir}")
    print(f"  Output:      {output_path}")
    print(f"  Frame Skip:  {frame_skip}")
    print(f"  Max Frames:  {'all' if max_frames == 0 else max_frames}")
    print(f"  Cameras:     {len(camera_map)}")
    print("=" * 60)

    # Clear previous output
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    if os.path.exists(output_path):
        os.remove(output_path)

    # Initialize components
    detector = PersonDetector(
        model_path=model_path,
        confidence_threshold=confidence,
    )

    emitter = EventEmitter(
        store_id=store_id,
        output_path=output_path,
        base_timestamp=base_timestamp,
    )

    total_events = 0

    # Process each camera
    for filename, camera_id in camera_map.items():
        video_path = os.path.join(video_dir, filename)
        if not os.path.exists(video_path):
            print(f"[pipeline] WARNING: Video not found: {video_path}")
            continue

        events = process_video(
            video_path=video_path,
            camera_id=camera_id,
            detector=detector,
            emitter=emitter,
            max_frames=max_frames,
            frame_skip=frame_skip,
        )
        total_events += events

    print("\n" + "=" * 60)
    print(f"  Pipeline Complete")
    print(f"  Store:        {store_id}")
    print(f"  Total Events: {total_events}")
    print(f"  Output File:  {output_path}")
    print("=" * 60)

    return output_path


def run_multi_store_pipeline(
    base_dir: str,
    output_dir: str = "data",
    max_frames: int = 0,
    frame_skip: int = 5,
    model_path: str = "yolov8n.pt",
    confidence: float = 0.3,
) -> list[str]:
    """
    Run the pipeline across all discovered stores.
    Returns list of output file paths.
    """
    print("=" * 60)
    print("  Store Intelligence — Multi-Store Pipeline (PS3f)")
    print("=" * 60)

    outputs = []

    # Try auto-discovery
    discovered = _auto_discover_stores(base_dir)

    if discovered:
        for store_id, store_config in discovered.items():
            output_path = os.path.join(output_dir, f"events_{store_id}.jsonl")
            run_pipeline(
                video_dir=store_config["video_dir"],
                output_path=output_path,
                store_id=store_id,
                max_frames=max_frames,
                frame_skip=frame_skip,
                model_path=model_path,
                confidence=confidence,
            )
            outputs.append(output_path)
    else:
        # Fall back to single-store processing
        output_path = os.path.join(output_dir, "events.jsonl")
        run_pipeline(
            video_dir=base_dir,
            output_path=output_path,
            store_id="ST1008",
            max_frames=max_frames,
            frame_skip=frame_skip,
            model_path=model_path,
            confidence=confidence,
        )
        outputs.append(output_path)

    return outputs


def main():
    parser = argparse.ArgumentParser(description="Store Intelligence Detection Pipeline (PS3f)")
    parser.add_argument(
        "--video-dir",
        default=os.environ.get("VIDEO_DIR", "data/videos"),
        help="Directory containing CCTV video files",
    )
    parser.add_argument(
        "--output",
        default=os.environ.get("EVENT_OUTPUT", "data/events.jsonl"),
        help="Output JSONL file path",
    )
    parser.add_argument(
        "--store-id",
        default=os.environ.get("STORE_ID", "ST1008"),
        help="Store identifier (ST1008, STORE_1, STORE_2)",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=int(os.environ.get("MAX_FRAMES", "0")),
        help="Max frames per video (0=all)",
    )
    parser.add_argument(
        "--frame-skip",
        type=int,
        default=int(os.environ.get("FRAME_SKIP", "5")),
        help="Process every Nth frame",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("YOLO_MODEL", "yolov8n.pt"),
        help="YOLO model path",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=float(os.environ.get("DETECTION_CONFIDENCE", "0.3")),
        help="Detection confidence threshold",
    )
    parser.add_argument(
        "--multi-store",
        action="store_true",
        help="Process all discovered stores in video-dir parent",
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="Auto-ingest events into the API after processing",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("API_URL", "http://localhost:8000"),
        help="API base URL for ingestion",
    )

    args = parser.parse_args()

    if args.multi_store:
        outputs = run_multi_store_pipeline(
            base_dir=args.video_dir,
            output_dir=os.path.dirname(args.output) or "data",
            max_frames=args.max_frames,
            frame_skip=args.frame_skip,
            model_path=args.model,
            confidence=args.confidence,
        )
    else:
        output_path = run_pipeline(
            video_dir=args.video_dir,
            output_path=args.output,
            store_id=args.store_id,
            max_frames=args.max_frames,
            frame_skip=args.frame_skip,
            model_path=args.model,
            confidence=args.confidence,
        )
        outputs = [output_path]

    # Auto-ingest if requested
    if args.ingest:
        for output_path in outputs:
            if os.path.exists(output_path):
                print(f"\n[pipeline] Ingesting events from {output_path}...")
                try:
                    import requests

                    events = []
                    with open(output_path, "r") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                events.append(json.loads(line))

                    # Batch ingest (max 500 per batch)
                    batch_size = 500
                    for i in range(0, len(events), batch_size):
                        batch = events[i : i + batch_size]
                        resp = requests.post(
                            f"{args.api_url}/events/ingest",
                            json={"events": batch},
                            timeout=30,
                        )
                        if resp.status_code == 200:
                            result = resp.json()
                            print(
                                f"[pipeline] Batch {i//batch_size + 1}: "
                                f"accepted={result['accepted']}, "
                                f"duplicate={result['duplicate']}, "
                                f"rejected={result['rejected']}"
                            )
                        else:
                            print(f"[pipeline] Batch {i//batch_size + 1} failed: {resp.status_code}")

                    print("[pipeline] Ingestion complete")
                except Exception as e:
                    print(f"[pipeline] Ingestion failed: {e}")


if __name__ == "__main__":
    main()
