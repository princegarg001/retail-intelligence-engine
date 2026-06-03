"""
Store Intelligence — FastAPI Application (PS3f Aligned)

AI-Assisted Decision: FastAPI chosen over Flask/Django because it provides automatic
OpenAPI docs, native async support, Pydantic integration, and high performance.

Edit Rationale: Updated for PS3f specification:
- POS CSV loader handles new schema (product_id, brand_name, no store_name/invoice_number)
- Sample events JSONL loader for PS3f native format auto-ingestion
- Store layout JSON loader
- Enhanced heatmap with zone metadata (zone_name, zone_type, is_revenue_zone)
- Multi-store support

# CHANGES MADE:
# - Updated _load_pos_csv for PS3f CSV schema
# - Added _load_sample_events for sample_events.jsonl
# - Added _load_store_layout for store_layout.json
# - Enhanced heatmap with zone metadata from events
# - Added startup event time tracking for health
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.anomalies import detect_anomalies
from app.funnel import compute_funnel
from app.health import check_health
from app.ingestion import ingest_events
from app.logging_config import generate_trace_id, logger
from app.metrics import compute_store_metrics
from app.models import (
    AnomalyResponse,
    FunnelResponse,
    HealthResponse,
    HeatmapResponse,
    IngestResponse,
    POSTransaction,
    StoreMetrics,
    ZoneHeatmapEntry,
)
from app.storage import (
    get_connection,
    get_events_by_store,
    get_sessions_by_store,
    init_db,
    insert_transaction,
    save_store_layout,
)

# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Store Intelligence API",
    description=(
        "PS3f-compliant store intelligence system converting raw CCTV footage "
        "into actionable business metrics. Supports multi-store, multi-camera "
        "event ingestion with cross-camera deduplication."
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Startup: initialize database and load all data sources
# ---------------------------------------------------------------------------
@app.on_event("startup")
def startup() -> None:
    """Initialize database and load POS/sample/layout data on startup."""
    init_db()
    logger.info("Store Intelligence API v2.0.0 (PS3f) started")

    # Auto-load POS CSV if present
    pos_path = os.environ.get("POS_CSV_PATH", "data/pos_transactions.csv")
    if os.path.exists(pos_path):
        _load_pos_csv(pos_path)

    # Auto-load sample events JSONL if present
    sample_path = os.environ.get("SAMPLE_EVENTS_PATH", "data/sample_events.jsonl")
    if os.path.exists(sample_path):
        _load_sample_events(sample_path)

    # Auto-load store layout JSON if present
    layout_path = os.environ.get("STORE_LAYOUT_PATH", "data/store_layout.json")
    if os.path.exists(layout_path):
        _load_store_layout(layout_path)


def _load_pos_csv(path: str) -> None:
    """Load POS transactions from CSV into the database.

    Handles both PS3f schema (order_id, order_date, order_time, store_id,
    product_id, brand_name, total_amount) and legacy schema.
    """
    conn = get_connection()
    try:
        loaded = 0
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    # Parse order_date and order_time into a combined timestamp
                    order_date = row.get("order_date", "")
                    order_time = row.get("order_time", "")
                    timestamp = None
                    if order_date and order_time:
                        for fmt in ["%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                                    "%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M"]:
                            try:
                                timestamp = datetime.strptime(
                                    f"{order_date} {order_time}", fmt
                                )
                                break
                            except ValueError:
                                continue

                    total_amount = 0.0
                    try:
                        total_amount = float(row.get("total_amount", 0) or 0)
                    except (ValueError, TypeError):
                        pass

                    txn = POSTransaction(
                        order_id=row.get("order_id", ""),
                        store_id=row.get("store_id", ""),
                        order_date=order_date,
                        order_time=order_time,
                        product_id=row.get("product_id"),
                        brand_name=row.get("brand_name"),
                        total_amount=total_amount,
                        # Legacy fields (may not exist in new CSV)
                        store_name=row.get("store_name", ""),
                        customer_name=row.get("customer_name", ""),
                        invoice_number=row.get("invoice_number", ""),
                        timestamp=timestamp,
                    )
                    insert_transaction(conn, txn)
                    loaded += 1
                except Exception as e:
                    logger.warning(
                        "Failed to load POS row",
                        extra={"error": str(e)[:100]},
                    )

        conn.commit()
        logger.info("POS data loaded", extra={"records": loaded, "path": path})
    except Exception as e:
        logger.error("Failed to load POS CSV", extra={"error": str(e)})
    finally:
        conn.close()


def _load_sample_events(path: str) -> None:
    """Load PS3f sample events from JSONL file."""
    try:
        events = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))

        if events:
            result = ingest_events(events)
            logger.info(
                "Sample events loaded",
                extra={
                    "accepted": result.accepted,
                    "duplicate": result.duplicate,
                    "rejected": result.rejected,
                    "path": path,
                },
            )
    except Exception as e:
        logger.error("Failed to load sample events", extra={"error": str(e)})


def _load_store_layout(path: str) -> None:
    """Load store layout JSON into the database."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            layout_data = json.load(f)

        conn = get_connection()
        try:
            if isinstance(layout_data, dict):
                # Single store layout
                store_id = layout_data.get("store_id", "UNKNOWN")
                save_store_layout(conn, store_id, json.dumps(layout_data))
            elif isinstance(layout_data, list):
                # Multiple store layouts
                for layout in layout_data:
                    store_id = layout.get("store_id", "UNKNOWN")
                    save_store_layout(conn, store_id, json.dumps(layout))
            conn.commit()
            logger.info("Store layout loaded", extra={"path": path})
        finally:
            conn.close()
    except Exception as e:
        logger.error("Failed to load store layout", extra={"error": str(e)})


# ---------------------------------------------------------------------------
# Middleware: structured logging per request
# ---------------------------------------------------------------------------
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Log every request with trace_id, endpoint, latency, and status."""
    trace_id = generate_trace_id()
    start = time.time()

    try:
        response = await call_next(request)
    except Exception as e:
        logger.error(
            "Request failed",
            extra={
                "trace_id": trace_id,
                "endpoint": str(request.url.path),
                "method": request.method,
                "error": str(e),
            },
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "Service temporarily unavailable",
                "trace_id": trace_id,
            },
        )

    latency_ms = round((time.time() - start) * 1000, 2)
    logger.info(
        "Request completed",
        extra={
            "trace_id": trace_id,
            "endpoint": str(request.url.path),
            "method": request.method,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
        },
    )
    response.headers["X-Trace-ID"] = trace_id
    return response


# ---------------------------------------------------------------------------
# Graceful error handler
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Return structured 503 instead of raw stack traces."""
    logger.error(
        "Unhandled exception",
        extra={
            "endpoint": str(request.url.path),
            "error": str(exc)[:200],
        },
    )
    return JSONResponse(
        status_code=503,
        content={
            "error": "Internal service error. Please retry.",
            "detail": str(exc)[:200],
        },
    )


# ---------------------------------------------------------------------------
# POST /events/ingest
# ---------------------------------------------------------------------------
@app.post("/events/ingest", response_model=IngestResponse)
async def ingest(request: Request) -> IngestResponse:
    """
    Ingest a batch of structured events.
    - Accepts up to 500 events per batch
    - Validates each event individually
    - Deduplicates by event_id (idempotent)
    - Returns structured per-item errors
    - Supports PS3f native format auto-detection
    """
    body = await request.json()

    if isinstance(body, dict) and "events" in body:
        raw_events = body["events"]
    elif isinstance(body, list):
        raw_events = body
    else:
        raise HTTPException(
            status_code=400,
            detail="Request body must be a list of events or {events: [...]}",
        )

    return ingest_events(raw_events)


# ---------------------------------------------------------------------------
# GET /stores/{store_id}/metrics
# ---------------------------------------------------------------------------
@app.get("/stores/{store_id}/metrics", response_model=StoreMetrics)
async def get_metrics(store_id: str) -> StoreMetrics:
    """
    Return store metrics excluding staff:
    - Unique visitors today
    - Total footfall (including group members)
    - Conversion rate (North Star metric)
    - Average dwell per zone
    - Queue depth and average wait time
    - Abandonment rate
    - Group entries and re-entry count
    - Gender/age distribution
    - Last event timestamp and data freshness
    """
    return compute_store_metrics(store_id)


# ---------------------------------------------------------------------------
# GET /stores/{store_id}/funnel
# ---------------------------------------------------------------------------
@app.get("/stores/{store_id}/funnel", response_model=FunnelResponse)
async def get_funnel(store_id: str) -> FunnelResponse:
    """
    Return session-based funnel:
    ENTRY -> ZONE_VISIT -> BILLING_QUEUE -> PURCHASE
    With drop-off percentages, conversion rate, POS transaction count and revenue.
    """
    return compute_funnel(store_id)


# ---------------------------------------------------------------------------
# GET /stores/{store_id}/heatmap
# ---------------------------------------------------------------------------
@app.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse)
async def get_heatmap(store_id: str) -> HeatmapResponse:
    """
    Return zone heatmap with visit frequency, average dwell,
    normalised score (0-100), data confidence flag, and PS3f zone metadata
    (zone_name, zone_type, is_revenue_zone).
    """
    conn = get_connection()
    try:
        events = get_events_by_store(conn, store_id)
        sessions = get_sessions_by_store(conn, store_id)

        zone_visits: dict[str, int] = defaultdict(int)
        zone_dwell: dict[str, list[int]] = defaultdict(list)
        zone_metadata: dict[str, dict] = {}  # zone_id -> {name, type, is_revenue}

        # Count zone visits and dwells from events, extract zone metadata
        for e in events:
            if e["event_type"] == "ZONE_ENTER" and e.get("zone_id"):
                zone_visits[e["zone_id"]] += 1
                # Extract zone metadata from event metadata
                try:
                    meta = json.loads(e.get("metadata", "{}"))
                    zid = e["zone_id"]
                    if zid not in zone_metadata:
                        zone_metadata[zid] = {
                            "zone_name": meta.get("zone_name"),
                            "zone_type": meta.get("zone_type"),
                            "is_revenue_zone": meta.get("is_revenue_zone", False),
                        }
                except (json.JSONDecodeError, TypeError):
                    pass

            if e["event_type"] == "ZONE_DWELL" and e.get("zone_id"):
                zone_dwell[e["zone_id"]].append(e.get("dwell_ms", 0))

        # Supplement from sessions
        for s in sessions:
            zones = json.loads(s.get("zones_visited", "[]"))
            for z in zones:
                if z not in zone_visits:
                    zone_visits[z] = 0
                zone_visits[z] += 1

        if not zone_visits:
            return HeatmapResponse(store_id=store_id)

        max_visits = max(zone_visits.values()) if zone_visits else 1

        zones = []
        for zone_id, count in zone_visits.items():
            dwells = zone_dwell.get(zone_id, [])
            avg_dwell = sum(dwells) / len(dwells) if dwells else 0.0
            normalised = round((count / max_visits) * 100, 1) if max_visits > 0 else 0.0

            # Confidence based on sample size
            if count >= 10:
                confidence = "high"
            elif count >= 5:
                confidence = "medium"
            else:
                confidence = "low"

            # Zone metadata from events
            meta = zone_metadata.get(zone_id, {})

            zones.append(
                ZoneHeatmapEntry(
                    zone_id=zone_id,
                    zone_name=meta.get("zone_name"),
                    zone_type=meta.get("zone_type"),
                    is_revenue_zone=bool(meta.get("is_revenue_zone")),
                    visit_frequency=count,
                    avg_dwell_ms=round(avg_dwell, 1),
                    normalised_score=normalised,
                    data_confidence=confidence,
                )
            )

        return HeatmapResponse(store_id=store_id, zones=zones)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /stores/{store_id}/anomalies
# ---------------------------------------------------------------------------
@app.get("/stores/{store_id}/anomalies", response_model=AnomalyResponse)
async def get_anomalies(store_id: str) -> AnomalyResponse:
    """
    Return active anomalies with severity, explanation,
    and suggested action. Includes: queue spikes, conversion drops,
    dead zones, stale feeds, group surges, and footfall anomalies.
    """
    return detect_anomalies(store_id)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """
    Return service health including per-store freshness,
    per-camera status, and stale feed warnings.
    """
    return check_health()


# ---------------------------------------------------------------------------
# POST /pos/upload — POS CSV upload
# ---------------------------------------------------------------------------
@app.post("/pos/upload")
async def upload_pos(file: UploadFile = File(...)):
    """Upload a POS transactions CSV file (supports PS3f and legacy schema)."""
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    conn = get_connection()
    loaded = 0
    try:
        for row in reader:
            try:
                order_date = row.get("order_date", "")
                order_time = row.get("order_time", "")
                timestamp = None
                if order_date and order_time:
                    for fmt in ["%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
                        try:
                            timestamp = datetime.strptime(
                                f"{order_date} {order_time}", fmt
                            )
                            break
                        except ValueError:
                            continue

                total_amount = float(row.get("total_amount", 0) or 0)
                txn = POSTransaction(
                    order_id=row.get("order_id", ""),
                    store_id=row.get("store_id", ""),
                    order_date=order_date,
                    order_time=order_time,
                    product_id=row.get("product_id"),
                    brand_name=row.get("brand_name"),
                    total_amount=total_amount,
                    store_name=row.get("store_name", ""),
                    customer_name=row.get("customer_name", ""),
                    invoice_number=row.get("invoice_number", ""),
                    timestamp=timestamp,
                )
                insert_transaction(conn, txn)
                loaded += 1
            except Exception:
                continue

        conn.commit()
    finally:
        conn.close()

    return {"loaded": loaded, "filename": file.filename}


# ---------------------------------------------------------------------------
# GET /stores — list all stores
# ---------------------------------------------------------------------------
@app.get("/stores")
async def list_stores():
    """List all stores that have events."""
    conn = get_connection()
    try:
        from app.storage import get_all_store_ids
        return {"stores": get_all_store_ids(conn)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POST /events/ingest/jsonl — JSONL file upload
# ---------------------------------------------------------------------------
@app.post("/events/ingest/jsonl")
async def ingest_jsonl(file: UploadFile = File(...)):
    """Upload a JSONL events file for batch ingestion."""
    content = await file.read()
    text = content.decode("utf-8")

    events = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not events:
        raise HTTPException(status_code=400, detail="No valid events found in file")

    # Process in batches of 500
    total_result = IngestResponse()
    batch_size = 500
    for i in range(0, len(events), batch_size):
        batch = events[i : i + batch_size]
        result = ingest_events(batch)
        total_result.accepted += result.accepted
        total_result.rejected += result.rejected
        total_result.duplicate += result.duplicate
        total_result.errors.extend(result.errors)

    return {
        "filename": file.filename,
        "total_events": len(events),
        "accepted": total_result.accepted,
        "rejected": total_result.rejected,
        "duplicate": total_result.duplicate,
        "errors": len(total_result.errors),
    }
