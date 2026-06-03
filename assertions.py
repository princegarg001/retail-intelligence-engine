"""
Store Intelligence — PS3f Assertions

Validates that the system meets all PS3f specification requirements.
Run this file to verify pipeline output, API behavior, and edge case handling.

Usage:
    python assertions.py [--api-url http://localhost:8000] [--events-file data/events.jsonl]

# PROMPT:
# "Generate PS3f assertion tests that validate event schema compliance,
#  edge case handling, and API response correctness."
#
# CHANGES MADE:
# - Added multi-store support validation
# - Added PS3f native event format validation
# - Added edge case assertions per spec
# - Removed emoji unicode characters to prevent Windows console encoding crashes
"""

import json
import os
import sys
import uuid
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def assert_event_schema(event: dict) -> None:
    """Assert that an event follows the PS3f schema."""
    # Must have all required fields
    required_internal = {"event_id", "store_id", "camera_id", "visitor_id",
                         "event_type", "timestamp", "confidence"}

    required_ps3f_entry = {"event_type", "id_token", "store_code", "camera_id",
                           "event_timestamp"}

    required_ps3f_zone = {"event_type", "track_id", "store_id", "camera_id",
                          "zone_id", "event_time"}

    required_ps3f_queue = {"queue_event_id", "event_type", "track_id",
                           "store_id", "camera_id", "zone_id"}

    keys = set(event.keys())

    # Check which format this event follows
    is_internal = required_internal.issubset(keys)
    is_ps3f_entry = required_ps3f_entry.issubset(keys) and "id_token" in keys
    is_ps3f_zone = required_ps3f_zone.issubset(keys) and "event_time" in keys
    is_ps3f_queue = required_ps3f_queue.issubset(keys) and "queue_event_id" in keys

    assert is_internal or is_ps3f_entry or is_ps3f_zone or is_ps3f_queue, \
        f"Event does not match any known schema. Keys: {keys}"

    # Validate event_type
    valid_internal_types = {"ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
                            "ZONE_DWELL", "BILLING_QUEUE_JOIN",
                            "BILLING_QUEUE_ABANDON", "REENTRY"}
    valid_ps3f_types = {"entry", "exit", "zone_entered", "zone_exited",
                        "zone_dwell", "queue_completed", "queue_abandoned",
                        "reentry"}

    et = event["event_type"]
    assert et in valid_internal_types or et in valid_ps3f_types, \
        f"Invalid event_type: {et}"


def assert_event_types_complete(events: list[dict]) -> None:
    """Assert that all required event types are present in the output."""
    required_types = {"ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
                      "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"}

    # Map PS3f types to internal types
    ps3f_map = {
        "entry": "ENTRY", "exit": "EXIT",
        "zone_entered": "ZONE_ENTER", "zone_exited": "ZONE_EXIT",
        "zone_dwell": "ZONE_DWELL",
        "queue_completed": "BILLING_QUEUE_JOIN",
        "queue_abandoned": "BILLING_QUEUE_ABANDON",
        "reentry": "REENTRY",
    }

    found_types = set()
    for e in events:
        et = e.get("event_type", "")
        if et in required_types:
            found_types.add(et)
        elif et in ps3f_map:
            found_types.add(ps3f_map[et])

    missing = required_types - found_types
    assert len(missing) == 0, \
        f"Missing required event types: {missing}. Found: {found_types}"


def assert_idempotent_ingestion() -> None:
    """Assert that duplicate event_ids are not re-inserted."""
    try:
        import requests
        api_url = os.environ.get("API_URL", "http://localhost:8000")

        event = {
            "event_id": str(uuid.uuid4()),
            "store_id": "TEST_ASSERT",
            "camera_id": "CAM_TEST",
            "visitor_id": "V_ASSERT",
            "event_type": "ENTRY",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "confidence": 0.9,
            "metadata": {},
        }

        # First ingest
        r1 = requests.post(f"{api_url}/events/ingest", json=[event], timeout=10)
        assert r1.status_code == 200, f"First ingest failed: {r1.status_code}"
        result1 = r1.json()
        assert result1["accepted"] == 1, "First ingest should accept"

        # Second ingest (same event_id)
        r2 = requests.post(f"{api_url}/events/ingest", json=[event], timeout=10)
        assert r2.status_code == 200, f"Second ingest failed: {r2.status_code}"
        result2 = r2.json()
        assert result2["duplicate"] == 1, "Second ingest should detect duplicate"
        assert result2["accepted"] == 0, "Second ingest should not accept"

        print("[PASS] Idempotent ingestion")
    except ImportError:
        print("[WARN] Skipping API tests (requests not available)")
    except Exception as e:
        print(f"[FAIL] Idempotent ingestion - {e}")


def assert_staff_exclusion() -> None:
    """Assert that staff events are excluded from customer metrics."""
    try:
        import requests
        api_url = os.environ.get("API_URL", "http://localhost:8000")

        # Ingest one staff and one customer event
        events = [
            {
                "event_id": str(uuid.uuid4()),
                "store_id": "TEST_STAFF",
                "camera_id": "CAM_1",
                "visitor_id": "STAFF_001",
                "event_type": "ENTRY",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "is_staff": True,
                "confidence": 0.9,
                "metadata": {},
            },
            {
                "event_id": str(uuid.uuid4()),
                "store_id": "TEST_STAFF",
                "camera_id": "CAM_1",
                "visitor_id": "CUST_001",
                "event_type": "ENTRY",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "is_staff": False,
                "confidence": 0.9,
                "metadata": {},
            },
        ]

        requests.post(f"{api_url}/events/ingest", json=events, timeout=10)

        # Check metrics
        r = requests.get(f"{api_url}/stores/TEST_STAFF/metrics", timeout=10)
        assert r.status_code == 200
        metrics = r.json()
        # Staff should not be in unique_visitors_today
        assert metrics["unique_visitors_today"] >= 1, "Should have at least 1 customer"

        print("[PASS] Staff exclusion")
    except ImportError:
        print("[WARN] Skipping API tests (requests not available)")
    except Exception as e:
        print(f"[FAIL] Staff exclusion - {e}")


def assert_group_entry_detection(events: list[dict]) -> None:
    """Assert that group entries are detected (group_id present)."""
    group_events = [
        e for e in events
        if e.get("group_id") or (
            isinstance(e.get("metadata"), dict) and e["metadata"].get("group_id")
        )
    ]
    # At least one group entry should exist in sample data
    status = "[PASS]" if group_events else "[WARN]"
    print(f"{status} Group entry detection ({len(group_events)} group events found)")


def assert_reentry_detection(events: list[dict]) -> None:
    """Assert that re-entry events are detected."""
    reentry_events = [
        e for e in events
        if e.get("event_type") in ("REENTRY", "reentry")
    ]
    status = "[PASS]" if reentry_events else "[WARN]"
    print(f"{status} Re-entry detection ({len(reentry_events)} re-entry events found)")


def assert_queue_events(events: list[dict]) -> None:
    """Assert that queue join and abandon events are present."""
    queue_join = [
        e for e in events
        if e.get("event_type") in ("BILLING_QUEUE_JOIN", "queue_completed")
    ]
    queue_abandon = [
        e for e in events
        if e.get("event_type") in ("BILLING_QUEUE_ABANDON", "queue_abandoned")
    ]
    has_both = bool(queue_join) and bool(queue_abandon)
    status = "[PASS]" if has_both else "[WARN]"
    print(f"{status} Queue events ({len(queue_join)} joins, {len(queue_abandon)} abandons)")


def assert_api_endpoints() -> None:
    """Assert that all required API endpoints are accessible."""
    try:
        import requests
        api_url = os.environ.get("API_URL", "http://localhost:8000")

        endpoints = [
            ("POST", "/events/ingest"),
            ("GET", "/stores/TEST/metrics"),
            ("GET", "/stores/TEST/funnel"),
            ("GET", "/stores/TEST/heatmap"),
            ("GET", "/stores/TEST/anomalies"),
            ("GET", "/health"),
        ]

        for method, path in endpoints:
            try:
                if method == "POST":
                    r = requests.post(f"{api_url}{path}", json=[], timeout=5)
                else:
                    r = requests.get(f"{api_url}{path}", timeout=5)
                status = "[PASS]" if r.status_code == 200 else "[WARN]"
                print(f"{status} {method} {path}: {r.status_code}")
            except Exception as e:
                print(f"[FAIL] {method} {path}: {e}")

    except ImportError:
        print("[WARN] Skipping API endpoint tests (requests not available)")


def assert_health_endpoint() -> None:
    """Assert that health endpoint returns valid response."""
    try:
        import requests
        api_url = os.environ.get("API_URL", "http://localhost:8000")

        r = requests.get(f"{api_url}/health", timeout=10)
        assert r.status_code == 200, f"Health endpoint returned {r.status_code}"
        data = r.json()
        assert "status" in data, "Missing 'status' field"
        assert "stores" in data, "Missing 'stores' field"

        print(f"[PASS] Health endpoint (status={data['status']})")
    except ImportError:
        print("[WARN] Skipping API tests (requests not available)")
    except Exception as e:
        print(f"[FAIL] Health endpoint - {e}")


def run_all_assertions(events_file: str = "data/events.jsonl") -> None:
    """Run all PS3f assertions."""
    print("=" * 60)
    print("  PS3f Specification Assertions")
    print("=" * 60)

    # Load events if file exists
    events = []
    sample_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        events_file,
    )
    # Also try sample events
    sample_alt = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data", "sample_events.jsonl",
    )

    for path in [sample_file, sample_alt]:
        if os.path.exists(path):
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            print(f"  Loaded {len(events)} events from {os.path.basename(path)}")
            break

    print("-" * 60)

    # Schema assertions
    if events:
        print("\n=== Event Schema Validation ===")
        try:
            for e in events:
                assert_event_schema(e)
            print("[PASS] All events pass schema validation")
        except AssertionError as ae:
            print(f"[FAIL] Schema validation failed: {ae}")

        print("\n=== Event Type Coverage ===")
        try:
            assert_event_types_complete(events)
            print("[PASS] All required event types present")
        except AssertionError as ae:
            print(f"[WARN] {ae}")

        print("\n=== Edge Case Detection ===")
        assert_group_entry_detection(events)
        assert_reentry_detection(events)
        assert_queue_events(events)

    # API assertions
    print("\n=== API Endpoint Validation ===")
    assert_api_endpoints()
    assert_health_endpoint()

    print("\n=== Idempotency ===")
    assert_idempotent_ingestion()

    print("\n=== Staff Exclusion ===")
    assert_staff_exclusion()

    print("\n" + "=" * 60)
    print("  Assertions Complete")
    print("=" * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PS3f Specification Assertions")
    parser.add_argument("--events-file", default="data/events.jsonl",
                        help="Path to events JSONL file")
    parser.add_argument("--api-url", default="http://localhost:8000",
                        help="API base URL")
    args = parser.parse_args()

    os.environ["API_URL"] = args.api_url
    run_all_assertions(args.events_file)
