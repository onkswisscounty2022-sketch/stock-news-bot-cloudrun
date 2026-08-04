#!/usr/bin/env bash
# Cloud Run Job entrypoint: restore state from GCS, run the requested bot
# script, then persist state back to GCS regardless of exit code.
set -uo pipefail

python3 gcs_sync.py pull

"$@"
STATUS=$?

python3 gcs_sync.py push

exit $STATUS
