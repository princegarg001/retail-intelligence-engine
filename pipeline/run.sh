#!/bin/bash
# Store Intelligence — Pipeline Runner Script
#
# Usage: ./pipeline/run.sh [OPTIONS]
#
# This script runs the complete detection pipeline on CCTV footage
# and optionally ingests the events into the API.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Defaults (can be overridden via environment variables)
VIDEO_DIR="${VIDEO_DIR:-/app/data/videos}"
EVENT_OUTPUT="${EVENT_OUTPUT:-/app/data/events.jsonl}"
STORE_ID="${STORE_ID:-ST1008}"
FRAME_SKIP="${FRAME_SKIP:-5}"
MAX_FRAMES="${MAX_FRAMES:-0}"
YOLO_MODEL="${YOLO_MODEL:-yolov8n.pt}"
DETECTION_CONFIDENCE="${DETECTION_CONFIDENCE:-0.3}"
API_URL="${API_URL:-http://localhost:8000}"
AUTO_INGEST="${AUTO_INGEST:-true}"

echo "========================================"
echo "  Store Intelligence Pipeline"
echo "========================================"
echo "  Video Dir:  $VIDEO_DIR"
echo "  Output:     $EVENT_OUTPUT"
echo "  Store ID:   $STORE_ID"
echo "  Frame Skip: $FRAME_SKIP"
echo "========================================"

cd "$PROJECT_DIR"

# Run detection pipeline
python -m pipeline.run_pipeline \
    --video-dir "$VIDEO_DIR" \
    --output "$EVENT_OUTPUT" \
    --store-id "$STORE_ID" \
    --frame-skip "$FRAME_SKIP" \
    --max-frames "$MAX_FRAMES" \
    --model "$YOLO_MODEL" \
    --confidence "$DETECTION_CONFIDENCE" \
    $([ "$AUTO_INGEST" = "true" ] && echo "--ingest --api-url $API_URL" || echo "")

echo "Pipeline finished successfully"
