"""
Store Intelligence — Data Setup Script (PS3f Aligned)

Prepares the data directory by:
1. Copying POS CSV to the expected location
2. Copying sample_events.jsonl
3. Copying store_layout.json (if it exists as .xlsx, skip — loaded at runtime)
4. Creating video symlinks/copies for all stores
5. Initializing the database
"""

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def setup_data():
    """Set up data directory with POS CSV, sample events, and video files."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parent_dir = os.path.dirname(project_root)
    data_dir = os.path.join(project_root, "data")
    video_dir = os.path.join(data_dir, "videos")

    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(video_dir, exist_ok=True)

    # ---- Copy POS CSV (PS3f format) ----
    pos_candidates = [
        os.path.join(parent_dir, "POS - sample transactionsb1e826f.csv"),
        os.path.join(parent_dir, "Brigade_Bangalore_10_April_26 (1)bc6219c.csv"),
    ]
    pos_dest = os.path.join(data_dir, "pos_transactions.csv")

    for pos_source in pos_candidates:
        if os.path.exists(pos_source) and not os.path.exists(pos_dest):
            shutil.copy2(pos_source, pos_dest)
            print(f"[setup] Copied POS CSV: {os.path.basename(pos_source)}")
            break
    else:
        if os.path.exists(pos_dest):
            print(f"[setup] POS CSV already exists")
        else:
            print(f"[setup] WARNING: No POS CSV found")

    # ---- Copy sample events JSONL ----
    sample_source = os.path.join(parent_dir, "sample_eventsbe42122.jsonl")
    sample_dest = os.path.join(data_dir, "sample_events.jsonl")

    if os.path.exists(sample_source) and not os.path.exists(sample_dest):
        shutil.copy2(sample_source, sample_dest)
        print(f"[setup] Copied sample_events.jsonl")
    elif os.path.exists(sample_dest):
        print(f"[setup] sample_events.jsonl already exists")
    else:
        print(f"[setup] WARNING: sample_events.jsonl not found")

    # ---- Copy or symlink CCTV footage ----

    # Legacy CCTV footage directory
    cctv_source = os.path.join(
        parent_dir,
        "CCTV Footage-20260529T160731Z-3-00144614ea",
        "CCTV Footage",
    )
    if os.path.exists(cctv_source):
        _link_videos(cctv_source, video_dir)

    # Store 1 footage
    store1_source = os.path.join(
        parent_dir,
        "Store 1-20260602T101818Z-3-001ec38db8",
        "Store 1",
    )
    store1_dest = os.path.join(video_dir, "store1")
    if os.path.exists(store1_source):
        os.makedirs(store1_dest, exist_ok=True)
        _link_videos(store1_source, store1_dest)

    # Store 2 footage
    store2_source = os.path.join(
        parent_dir,
        "Store 2-20260602T101819Z-3-001099f208",
        "Store 2",
    )
    store2_dest = os.path.join(video_dir, "store2")
    if os.path.exists(store2_source):
        os.makedirs(store2_dest, exist_ok=True)
        _link_videos(store2_source, store2_dest)

    # ---- Initialize database ----
    from app.storage import init_db
    init_db()
    print("[setup] Database initialized")

    print("\n[setup] Data setup complete!")
    print(f"[setup] Data directory: {data_dir}")
    print(f"[setup] Video directory: {video_dir}")


def _link_videos(source_dir: str, dest_dir: str) -> None:
    """Copy or symlink video files from source to destination."""
    for filename in os.listdir(source_dir):
        if filename.endswith((".mp4", ".png", ".jpg")):
            src = os.path.join(source_dir, filename)
            dst = os.path.join(dest_dir, filename)
            if not os.path.exists(dst):
                try:
                    os.symlink(src, dst)
                    print(f"[setup] Linked {filename}")
                except (OSError, NotImplementedError):
                    shutil.copy2(src, dst)
                    print(f"[setup] Copied {filename}")
            else:
                print(f"[setup] {filename} already exists")


if __name__ == "__main__":
    setup_data()
