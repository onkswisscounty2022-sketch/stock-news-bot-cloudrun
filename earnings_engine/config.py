"""
Earnings Intelligence Engine V1.0
Central configuration
"""
import os
import pytz

# ─── LOAD .env ────────────────────────────────────────────────────────────────
# Load from parent directory's .env file (works on both Windows dev and GCP VM)
_env_paths = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'),
    os.path.expanduser('~/.env'),
]
for _env_path in _env_paths:
    if os.path.exists(_env_path):
        for _line in open(_env_path):
            _line = _line.strip()
            if _line and '=' in _line and not _line.startswith('#'):
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())
        break

# ─── GCP / BigQuery ───────────────────────────────────────────────────────────
GCP_PROJECT_ID   = "project-a8446c31-7279-4456-bbf"
BQ_DATASET       = "onkar_vcp"
BQ_LOCATION      = "asia-south1"

# ─── CREDENTIALS ─────────────────────────────────────────────────────────────
# Try environment variable first, fall back to parent config.py hardcoded value
DISCORD_WEBHOOK_EARNINGS = os.environ.get("WEBHOOK_EARNINGS")

# If not in env, try to import from parent config
if not DISCORD_WEBHOOK_EARNINGS:
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from config import WEBHOOK_EARNINGS as _WE
        DISCORD_WEBHOOK_EARNINGS = _WE
    except Exception:
        pass
GMAIL_SENDER             = os.environ.get("GMAIL_SENDER")
GMAIL_APP_PASSWORD       = os.environ.get("GMAIL_APP_PASSWORD")
GMAIL_RECEIVER           = os.environ.get("GMAIL_RECEIVER")
GEMINI_API_KEY           = os.environ.get("GEMINI_API_KEY")

# ─── GEMINI ───────────────────────────────────────────────────────────────────
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

# ─── TIMEZONE ─────────────────────────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")

# ─── PATHS ────────────────────────────────────────────────────────────────────
import os
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DB_PATH      = os.path.join(BASE_DIR, "earnings.db")
PDF_ARCHIVE  = os.path.join(BASE_DIR, "pdf_archive")
REPORT_ARCHIVE = os.path.join(BASE_DIR, "report_archive")

# ─── EARNINGS SEASONS (IST months) ───────────────────────────────────────────
EARNINGS_SEASONS = {
    "Q1": (7, 8),    # July-August
    "Q2": (10, 11),  # October-November
    "Q3": (1, 2),    # January-February
    "Q4": (4, 5),    # April-May
}

# ─── COMPANY TIERS ───────────────────────────────────────────────────────────
# Tier A = current VCP watchlist (from BigQuery)
# Tier B = VCP watchlist last 90 days
# Tier C = high-growth filter
# Tier D = all others (calendar only)
TIER_A_LOOKBACK_DAYS = 1
TIER_B_LOOKBACK_DAYS = 90

# ─── NIFTY 500 (starting universe) ───────────────────────────────────────────
NIFTY500_INDEX_SYMBOL = "NIFTY500"

# ─── SCHEDULE ─────────────────────────────────────────────────────────────────
# Calendar check: 7 PM IST = 13:30 UTC
# Result scans: 1:30 PM, 4:30 PM, 8:30 PM IST = 08:00, 11:00, 15:00 UTC
CALENDAR_SCAN_UTC  = (13, 30)
RESULT_SCAN_UTCS   = [(8, 0), (11, 0), (15, 0)]
