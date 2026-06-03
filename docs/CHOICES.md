# Store Intelligence — Engineering Choices (PS3f Aligned)

This document explains the three most significant engineering decisions, following the format: options considered → AI suggestion → final choice → rationale → trade-off accepted.

---

## Decision 1: Detection Model — YOLOv8n (Nano)

### Options Considered
1. **YOLOv8n** — 3.2M params, ~6ms/frame on GPU, good accuracy for person detection
2. **YOLOv8s** — 11.2M params, better accuracy but slower
3. **YOLOv8m/l/x** — Higher accuracy, much slower, impractical without GPU
4. **RT-DETR** — Transformer-based, SOTA accuracy but heavy
5. **Custom lightweight detector** — Maximum speed but poor generalization

### What the AI Suggested
AI recommended YOLOv8s or RT-DETR for "maximum detection quality," noting that transformer-based detectors handle occlusion better and would score higher on detection completeness.

### What I Chose
**YOLOv8n** (nano variant)

### Why I Chose It
1. **Deployment reality**: The system must run from `docker compose up` on any machine. YOLOv8n runs at acceptable speed even on CPU (unlike larger models that would require GPU).
2. **Sufficient accuracy**: For retail CCTV with fixed cameras and moderate density, YOLOv8n achieves >85% mAP on COCO person class. The marginal accuracy gain from larger models doesn't justify the 3-5x latency cost.
3. **Fallback graceful degradation**: If YOLOv8n fails to load, the pipeline falls back to motion-based detection — ensuring the system always produces output.
4. **Challenge alignment**: The rubric values "functional correctness over model complexity" (§1). A working YOLOv8n pipeline scores higher than a broken YOLOv8x setup.
5. **PS3f edge cases**: For group detection and partial occlusion, we handle these at the session/metadata level rather than depending on a more complex detection model.

### Trade-off Accepted
Lower detection accuracy in crowded scenes and partial occlusion. Mitigated by:
- Lower confidence threshold (0.3) to catch more detections
- Metadata `ambiguity` flag for uncertain detections
- Never silently suppressing low-confidence results (§6.5)
- `is_face_hidden` metadata flag for partial occlusion acknowledgment

---

## Decision 2: Event Schema — Dual-Format with Auto-Detection

### Options Considered
1. **Internal format only** — Single canonical schema, reject non-conforming events
2. **PS3f native format only** — Accept only the sample_events.jsonl format
3. **Dual-format with auto-detection** — Accept both and normalize
4. **Schema transformation middleware** — Pre-process all events before ingestion

### What the AI Suggested
AI recommended a strict single schema with validation, noting that "format flexibility introduces ambiguity." It suggested a preprocessing step to transform all events before they reach the API.

### What I Chose
**Dual-format with auto-detection** — The `parse_ps3f_event()` function automatically detects whether an event follows the internal schema or any of the three PS3f native formats (entry/exit, zone, queue), and normalizes it.

### Why I Chose It
1. **Dataset reality**: The PS3f spec provides `sample_events.jsonl` with three distinct event shapes that differ significantly from the internal format. The system must handle these seamlessly.
2. **Zero friction**: Reviewers can `cat sample_events.jsonl | curl -X POST /events/ingest` without preprocessing. This directly demonstrates compatibility.
3. **Backward compatibility**: Existing pipeline output (internal format) continues to work unchanged.
4. **Robustness**: Auto-detection means the system handles real-world data variety without manual format specification.

### Trade-off Accepted
- Slightly more complex ingestion logic
- Potential for false format detection (mitigated by checking specific field combinations)
- Minor performance overhead for format detection on each event

---

## Decision 3: Storage — SQLite with Enriched Schema

### Options Considered
1. **SQLite** — Zero-config, single-file, embedded database
2. **PostgreSQL** — Full-featured RDBMS, excellent for production
3. **Redis** — In-memory, fast but volatile
4. **DuckDB** — Analytical queries, columnar storage
5. **Pure in-memory Python dicts** — Simplest but non-persistent

### What the AI Suggested
AI strongly recommended PostgreSQL, citing "production readiness" and noting that Docker Compose makes PostgreSQL easy to deploy. AI also suggested Redis as a cache layer.

### What I Chose
**SQLite with WAL (Write-Ahead Logging) mode**, enriched with 10 tables including cross-camera links, camera health monitoring, and store layouts.

### Why I Chose It
1. **Zero dependencies**: `docker compose up` from a clean clone must work. SQLite requires no additional service, no connection string configuration, no health check for a database container. PostgreSQL adds a failure point.
2. **Data volume fit**: This system processes hundreds to low-thousands of events from 3-5 cameras per store. SQLite handles this with zero performance issues.
3. **Schema richness**: Despite choosing SQLite, the schema is production-grade with 10 tables, proper indexes, foreign keys, and WAL mode for concurrent reads.
4. **PS3f enrichment**: Added `cross_camera_links`, `camera_health`, and `store_layouts` tables to support multi-store, multi-camera tracking without changing the storage engine.
5. **Production awareness**: Connection handling, transactions, indexes, and graceful error handling demonstrate production thinking without operational burden.

### Trade-off Accepted
- No true concurrent write scaling (single-writer model)
- No built-in replication
- No advanced query planner for complex analytical queries

Mitigated by:
- Data volume is small enough that these limitations don't matter
- Clean storage interface that could be swapped to PostgreSQL with minimal code changes
- DuckDB could be added as a read-only analytical layer if needed

---

## Summary of Engineering Philosophy

| Dimension | Approach |
|-----------|----------|
| Model choice | Practical over maximal |
| Schema design | Flexible over rigid |
| Storage | Pragmatic over scalable |
| Event handling | Auto-detecting over restrictive |
| Error handling | Graceful over silent |
| Edge cases | Explicit handling over ignoring |
| Testing | Edge cases over coverage % |
| Documentation | Genuine reasoning over boilerplate |
| Multi-store | Independent processing over monolithic |

The common thread: **choose what works reliably for this specific problem**, explain the trade-off, and leave room to scale later. This is the difference between a demo and a system.
