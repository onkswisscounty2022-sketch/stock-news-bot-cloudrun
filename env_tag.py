"""
Tags outgoing Discord posts and Gmail subjects with their execution origin
(VM vs Cloud Run) so you can tell them apart while both run side by side
during migration.

Cloud Run automatically injects CLOUD_RUN_JOB (for Jobs) or K_SERVICE (for
Services) into the container - see the container runtime contract:
https://cloud.google.com/run/docs/container-contract
Neither variable exists on the VM or when running locally, so detection
needs no manual configuration.
"""
import os

SOURCE_TAG = "☁️ Cloud Run" if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE") else "🖥️ VM"
