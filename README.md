# Store Intelligence — PS3f Retail Analytics Pipeline

> End-to-end system converting raw CCTV footage into session-based business metrics (conversion rate, dwell analytics, queue performance) with multi-store, multi-camera support. Built for the PS3f specification.

---

## Quick Start — `docker compose up`

### 1. Place Dataset Files

The dataset is **not included** in this repository. Place the provided PS3f dataset files into a `data/` folder at the project root before running:

```
store-intelligence/
└── data/                          ← Create this folder
    ├── videos/                    ← CCTV clips go here
    │   ├── CAM 1.mp4
    │   ├── CAM 2.mp4
    │   ├── CAM 3.mp4
    │   ├── CAM 4.mp4
    │   └── CAM 5.mp4
    ├── pos_transactions.csv       ← POS transaction file
    └── sample_events.jsonl        ← PS3f sample events
```

Alternatively, run the setup script to auto-detect and link datasets from the parent directory:
```bash
python scripts/setup_data.py
```

### 2. Start All Services

```bash
docker compose up --build
```

On startup, the system:
1. Builds the Docker image (installs YOLOv8n model at build time)
2. Starts the **API** at `http://localhost:8000` — auto-loads POS CSV + sample events from `data/`
3. Waits for the API healthcheck to pass, then starts the **Detection Pipeline** against clips in `data/videos/`
4. Starts the **Live Dashboard** at `http://localhost:8501`

| Service | URL | What It Does |
|---------|-----|-------------|
| Intelligence API | http://localhost:8000 | All analytics endpoints |
| OpenAPI Docs | http://localhost:8000/docs | Interactive API documentation |
| **Live Dashboard** | **http://localhost:8501** | Real-time Streamlit analytics UI |

---

## Running the Detection Pipeline Against CCTV Clips

The pipeline processes video files from `data/videos/`, detects persons using YOLOv8, tracks them across frames, classifies their positions into store zones, and emits structured events.

### Via Docker (automatic)
The `pipeline` service in `docker-compose.yml` runs automatically after the API is healthy:
```bash
docker compose up --build
# Pipeline processes data/videos/*.mp4 and ingests events into the API
```

### Via CLI (manual, for testing)
```bash
# Single store (default ST1008)
python -m pipeline.run_pipeline \
  --video-dir data/videos \
  --output data/events.jsonl \
  --store-id ST1008 \
  --frame-skip 5 \
  --ingest \
  --api-url http://localhost:8000

# Multi-store (auto-discovers Store 1, Store 2 subdirectories)
python -m pipeline.run_pipeline \
  --video-dir data/videos \
  --multi-store \
  --ingest \
  --api-url http://localhost:8000
```

### Or use the shell script
```bash
./pipeline/run.sh
```

### Pipeline Output
Events are written to `data/events.jsonl` as structured JSON lines:
```json
{"event_id": "a3f1...", "store_id": "ST1008", "camera_id": "CAM_1", "visitor_id": "V00042", "event_type": "ENTRY", "timestamp": "2026-04-10T10:15:03Z", "confidence": 0.87, "is_staff": false}
```

**8 event types emitted**: `ENTRY`, `EXIT`, `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_DWELL`, `BILLING_QUEUE_JOIN`, `BILLING_QUEUE_ABANDON`, `REENTRY`

---

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/events/ingest` | POST | Batch event ingestion (idempotent, deduplicates by event_id) |
| `/stores/{id}/metrics` | GET | Unique visitors, conversion rate, queue depth, dwell, demographics |
| `/stores/{id}/funnel` | GET | Session-based funnel: ENTRY → ZONE_VISIT → BILLING_QUEUE → PURCHASE |
| `/stores/{id}/heatmap` | GET | Zone engagement heatmap with confidence scores |
| `/stores/{id}/anomalies` | GET | Active anomalies (queue spikes, conversion drops, stale feeds) |
| `/health` | GET | System health with per-store and per-camera freshness |

### North Star Metric — Conversion Rate
```
conversion_rate = converted_unique_visitors / total_unique_visitors
```
A visitor is "converted" when they enter the billing queue and a POS transaction occurs at that store within a 5-minute window. Staff sessions are excluded.

---

## Edge Case Handling

| Edge Case | How It's Handled |
|-----------|-----------------|
| **Group entry** | Detected by temporal proximity at entry cameras; `group_id` + `group_size` tracked per session |
| **Staff exclusion** | `is_staff` flag; staff filtered from all customer metrics (visitors, conversion, funnel) |
| **Visitor re-entry** | `REENTRY` event type emitted; `is_reentry` flag on session |
| **Partial occlusion** | `is_face_hidden` metadata; low-confidence detections never suppressed |
| **Queue buildup** | Real-time depth from JOIN/ABANDON events; `wait_seconds` from PS3f queue events |
| **Empty store** | Returns zero-valued metrics (not errors); `FOOTFALL_ANOMALY` when only staff detected |
| **Cross-camera dedup** | Same `visitor_id` tracked across cameras; `cross_camera_links` table |

---

## Repository Structure

```
store-intelligence/
├── pipeline/                   # Detection + Tracking Pipeline
│   ├── detect.py               # Main detection (YOLOv8n) + tracking script
│   ├── tracker.py              # Re-ID / IoU tracking logic
│   ├── event_emitter.py        # Event schema + state-machine emission
│   ├── zones.py                # Multi-store zone classification
│   ├── reid.py                 # Staff detection heuristics
│   ├── run_pipeline.py         # Multi-store pipeline orchestrator
│   └── run.sh                  # One command to process all clips → events
├── app/                        # FastAPI Intelligence API
│   ├── main.py                 # FastAPI entrypoint
│   ├── models.py               # Pydantic event schema (internal + PS3f native)
│   ├── ingestion.py            # Ingest, dedup, session building
│   ├── metrics.py              # Real-time metric computation
│   ├── funnel.py               # Funnel + session logic
│   ├── anomalies.py            # Anomaly detection (6 types)
│   ├── health.py               # Per-store + per-camera health
│   ├── storage.py              # SQLite persistence (WAL, 10 tables)
│   └── logging_config.py       # Structured JSON logging
├── tests/                      # 91 tests, 70% coverage
│   ├── test_pipeline.py        # Include prompt block header
│   ├── test_ingest.py          # Ingestion + idempotency tests
│   ├── test_metrics.py         # Conversion + staff exclusion tests
│   ├── test_funnel.py          # Session-based funnel tests
│   ├── test_anomalies.py       # Anomaly detection tests
│   ├── test_health.py          # Health endpoint tests
│   └── test_edge_cases.py      # All 7 PS3f mandatory edge cases
├── dashboard/
│   └── live.py                 # Streamlit live dashboard (http://localhost:8501)
├── scripts/
│   └── setup_data.py           # Auto-links dataset from parent directory
├── docs/
│   ├── DESIGN.md               # Architecture + AI-assisted decisions
│   └── CHOICES.md              # 3 decisions with full reasoning
├── assertions.py               # PS3f specification validator
├── Dockerfile                  # Production container
├── docker-compose.yml          # API + Pipeline + Dashboard
├── requirements.txt            # Python dependencies
├── pyproject.toml              # Pytest configuration
└── README.md
```

> **Note**: The `data/` directory is **not committed** to the repository. It is created at runtime and populated with the PS3f dataset before running `docker compose up`. See the [Quick Start](#quick-start--docker-compose-up) section.

---

## Testing

```bash
# Run all 91 tests with coverage report
pytest tests/ -v --cov=app --cov-report=term-missing

# Run PS3f specification assertions against live API
python assertions.py --api-url http://localhost:8000
```

| Suite | Tests | What It Covers |
|-------|-------|----------------|
| Ingestion | 17 | Schema validation, idempotency, batch, partial success |
| Metrics | 11 | Conversion, staff exclusion, dwell, freshness |
| Funnel | 7 | Session stages, drop-off, empty store |
| Anomalies | 10 | Queue spike, conversion drop, dead zone, stale feed |
| Health | 4 | Multi-store status, freshness |
| Pipeline | 12 | Detection, tracking, zones, event emission |
| Edge Cases | 30 | All 7 mandatory PS3f edge cases |

---

## Production Readiness

- **Deployment**: Single `docker compose up` — no manual intervention
- **Observability**: Structured JSON logs on every request (`trace_id`, `endpoint`, `latency_ms`, `status_code`)
- **Health monitoring**: `/health` returns per-store and per-camera freshness with stale feed warnings
- **Graceful errors**: 503 with structured JSON on failure — no raw stack traces
- **Idempotency**: Duplicate `event_id` submissions return `duplicate` count, never inflate metrics

---

## Documentation

- **[DESIGN.md](docs/DESIGN.md)** — System architecture, detection pipeline flow, event processing flow, storage model, edge case handling, observability, **AI-Assisted Decisions** section, design trade-offs
- **[CHOICES.md](docs/CHOICES.md)** — Three engineering decisions:
  1. **Detection model selection** — YOLOv8n (nano)
  2. **Event schema design** — dual-format auto-detection
  3. **Storage/API architecture** — SQLite with WAL mode

---

## AI Engineering Transparency

Every source file includes AI-assisted decision annotations. Every test file includes:
```python
# PROMPT:
<AI prompt used to generate the test>

# CHANGES MADE:
<Manual modifications after generation>
```

---

## Live Dashboard (Part E)

**URL**: http://localhost:8501

The Streamlit dashboard provides real-time store analytics:
- Multi-store selector (sidebar dropdown)
- KPI cards: unique visitors, conversion rate, queue depth, abandonment rate, data freshness
- Plotly conversion funnel with POS revenue correlation
- Zone engagement heatmap with confidence scores
- Customer demographics (gender distribution, age buckets)
- Active anomaly console with severity levels and suggested actions
- Per-camera health status with event counts
- Auto-refresh every 5 seconds
