"""
Central configuration — all webhooks and credentials
"""
import os

# ─── CREDENTIALS ─────────────────────────────────────────────────────────────
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY")
GMAIL_SENDER    = os.environ.get("GMAIL_SENDER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
GMAIL_RECEIVER  = os.environ.get("GMAIL_RECEIVER")

# ─── DISCORD WEBHOOKS ─────────────────────────────────────────────────────────
# All webhooks come from env vars now (previously 3 of these were hardcoded
# here in source - rotate those webhook URLs in Discord since they were
# committed to the repo). Set values via Secret Manager on Cloud Run.
WEBHOOK_STOCKS_NEWS   = os.environ.get("DISCORD_WEBHOOK_URL")   # #stocks-news
WEBHOOK_EARNINGS      = os.environ.get("WEBHOOK_EARNINGS")      # #earning-alerts
WEBHOOK_CONCALL       = os.environ.get("WEBHOOK_CONCALL")       # #concall-intel
WEBHOOK_SMART_ALERTS  = os.environ.get("WEBHOOK_SMART_ALERTS")  # #smart-alerts
WEBHOOK_WEEKLY_WRAP   = os.environ.get("WEBHOOK_WEEKLY_WRAP")   # #weekly-wrap

# ─── GEMINI ───────────────────────────────────────────────────────────────────
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

# ─── TIMEZONE ─────────────────────────────────────────────────────────────────
import pytz
IST = pytz.timezone("Asia/Kolkata")
