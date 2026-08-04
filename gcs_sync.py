"""
Cloud Run Jobs get a brand new, empty container filesystem on every
execution -- nothing written to disk survives between runs. This bot's
state (dedup JSON files, the earnings SQLite DB, downloaded PDFs) used to
live forever on the VM's disk. To keep that working on Cloud Run, we
mirror a fixed set of local paths to/from a GCS bucket immediately before
and after each run.

Usage (called by entrypoint.sh, not run directly):
    python3 gcs_sync.py pull   # download bucket -> local disk (before run)
    python3 gcs_sync.py push   # upload local disk -> bucket (after run)

Configuration:
    STATE_BUCKET  env var, e.g. "onkar-stock-bot-state" (no gs:// prefix)
    If unset, sync is a no-op (state simply won't persist -- fine for
    local/dev runs, not for production).
"""
import os
import sys

STATE_BUCKET = os.environ.get("STATE_BUCKET")

# Local paths (relative to /app) that must survive between Cloud Run
# executions. Files are synced individually; directories recursively.
SYNC_PATHS = [
    "alert_state.json",
    "concall_state.json",
    "earnings_engine/earnings.db",
    "earnings_engine/pdf_archive",
    "earnings_engine/report_archive",
]


def _client():
    from google.cloud import storage
    return storage.Client()


def pull():
    if not STATE_BUCKET:
        print("[gcs_sync] STATE_BUCKET not set - skipping pull (no persisted state)")
        return
    client = _client()
    bucket = client.bucket(STATE_BUCKET)
    for rel_path in SYNC_PATHS:
        prefix = rel_path
        blobs = list(bucket.list_blobs(prefix=prefix))
        if not blobs:
            continue
        for blob in blobs:
            local_path = blob.name
            os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
            blob.download_to_filename(local_path)
            print(f"[gcs_sync] pulled {local_path}")


def push():
    if not STATE_BUCKET:
        print("[gcs_sync] STATE_BUCKET not set - skipping push (state will not persist)")
        return
    client = _client()
    bucket = client.bucket(STATE_BUCKET)
    for rel_path in SYNC_PATHS:
        if os.path.isfile(rel_path):
            bucket.blob(rel_path).upload_from_filename(rel_path)
            print(f"[gcs_sync] pushed {rel_path}")
        elif os.path.isdir(rel_path):
            for root, _dirs, files in os.walk(rel_path):
                for fname in files:
                    local_path = os.path.join(root, fname)
                    blob_name = local_path.replace(os.sep, "/")
                    bucket.blob(blob_name).upload_from_filename(local_path)
                    print(f"[gcs_sync] pushed {local_path}")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "pull":
        pull()
    elif action == "push":
        push()
    else:
        print("Usage: python3 gcs_sync.py [pull|push]")
        sys.exit(1)
