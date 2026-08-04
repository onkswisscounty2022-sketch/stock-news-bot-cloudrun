"""
Earnings Intelligence Engine — Main Orchestrator
Runs calendar scan, result detection, symbol resolution, PDF download, and VCP sync.

Pipeline per detection run:
  1. Detect new results (BSE API → NSE → RSS fallback)
  2. Resolve BSE scrip codes → NSE symbols + company names
  3. Download PDFs from BSE
  4. [Phase 2B] Extract financials via Gemini
  5. [Phase 2C] AI analysis + scoring
  6. [Phase 3A] Discord + Gmail alerts         ← coming next
"""
import sys
import os
import time
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import IST
from database import init_db
from calendar_scanner import run_calendar_scan, is_earnings_season
from result_detector import run_result_detection
from symbol_resolver import ensure_cache_loaded, refresh_master, enrich_detection
from pdf_downloader import download_batch
from vcp_integration import sync_tiers_to_db
from financial_extractor import extract_batch
from ai_analyzer import analyze_batch
from discord_alert import send_discord_batch
from gmail_alert import send_gmail_batch

def run_calendar_job():
    """7 PM IST — Tomorrow's earnings calendar."""
    print(f"\n{'='*50}")
    print(f"📅 EARNINGS CALENDAR JOB")
    print(f"Time: {datetime.now(IST).strftime('%d %b %Y %I:%M %p IST')}")
    print(f"{'='*50}")

    # Ensure symbol cache is loaded
    ensure_cache_loaded()

    # Sync VCP tiers first
    try:
        tier_a, tier_b = sync_tiers_to_db()
        print(f"[MAIN] VCP tiers: {tier_a} Tier A, {tier_b} Tier B")
    except Exception as e:
        print(f"[MAIN] VCP sync skipped: {e}")

    # Fetch calendar
    active, quarter = is_earnings_season()
    if active:
        entries = run_calendar_scan()
        print(f"[MAIN] Calendar: {len(entries)} companies found for {quarter}")
    else:
        print("[MAIN] Not in earnings season — calendar scan skipped")

def run_detection_job():
    """1:30 PM, 4:30 PM, 8:30 PM IST — Detect new results."""
    print(f"\n{'='*50}")
    print(f"🔍 RESULT DETECTION JOB")
    print(f"Time: {datetime.now(IST).strftime('%d %b %Y %I:%M %p IST')}")
    print(f"{'='*50}")

    active, quarter = is_earnings_season()
    if not active:
        print("[MAIN] Not in earnings season — detection skipped")
        return

    # Ensure symbol cache is ready
    ensure_cache_loaded()

    # ── Step 1: Detect new filings ─────────────────────────────────────────
    new_results = run_result_detection()
    if not new_results:
        print("[MAIN] No new results detected")
        return

    print(f"[MAIN] {len(new_results)} new results detected — enriching...")

    # ── Step 2: Resolve BSE codes → real company names + NSE symbols ───────
    enriched = [enrich_detection(item) for item in new_results]

    # Print resolved names
    for item in enriched:
        print(f"  → {item.get('symbol'):15} | {item.get('company_name','')[:40]:40} | Tier {item.get('tier','D')}")

    # ── Step 3: Download PDFs ──────────────────────────────────────────────
    print(f"\n[MAIN] Downloading PDFs...")
    enriched = download_batch(enriched)

    # ── Step 4: Financial extraction via Gemini (Phase 2B) ────────────────
    # Tier A/B → full extraction + AI. Tier C/D → PDF stored, no AI.
    tier_ab = [i for i in enriched if i.get("tier") in ("A", "B")]
    tier_cd = [i for i in enriched if i.get("tier") in ("C", "D")]

    if tier_cd:
        print(f"[MAIN] {len(tier_cd)} Tier C/D companies — PDFs stored, AI pipeline skipped")

    if not tier_ab:
        print("[MAIN] No Tier A/B companies — extraction skipped")
        return

    print(f"\n[MAIN] {len(tier_ab)} Tier A/B companies — running financial extraction...")
    extracted = extract_batch(tier_ab)

    # ── Step 5: AI analysis + scoring (Phase 2C) ──────────────────────────
    ready_for_analysis = [i for i in extracted if i.get("extraction_status") == "extracted"]
    if not ready_for_analysis:
        print("[MAIN] No successful extractions — AI analysis skipped")
        return

    print(f"\n[MAIN] {len(ready_for_analysis)} extractions successful — running AI analysis...")
    analyzed = analyze_batch(ready_for_analysis)

    # ── Step 6: Send Discord + Gmail alerts (Phase 3A) ─────────────────────
    tier_ab_analyzed = [i for i in analyzed if i.get("analysis_status") == "analyzed"]
    if tier_ab_analyzed:
        print(f"\n[MAIN] Sending alerts for {len(tier_ab_analyzed)} companies...")
        send_discord_batch(tier_ab_analyzed)
        send_gmail_batch(tier_ab_analyzed)

    # Print final pipeline summary
    print(f"\n{'='*50}")
    print("[MAIN] PIPELINE SUMMARY")
    for item in analyzed:
        score  = item.get("overall_score", "N/A")
        cls    = item.get("classification", "N/A")
        status = item.get("analysis_status", "N/A")
        print(f"  {item.get('symbol'):15} | {item.get('company_name','')[:30]:30} "
              f"| Score:{score:>5} | {cls} | {status}")
    print(f"{'='*50}")

def run_full_setup():
    """First-time setup — initialize database and sync VCP."""
    print("\n🚀 EARNINGS INTELLIGENCE ENGINE — SETUP")
    print("="*50)

    # Initialize database
    init_db()
    print("✅ Database initialized")

    # Create directories
    os.makedirs("pdf_archive", exist_ok=True)
    os.makedirs("report_archive", exist_ok=True)
    print("✅ Directories created")

    # Load BSE master data → symbol resolver cache
    print("\n[SETUP] Loading BSE master securities list...")
    refresh_master(force=True)
    print("✅ Symbol resolver ready")

    # Sync VCP tiers
    try:
        tier_a, tier_b = sync_tiers_to_db()
        print(f"✅ VCP sync: {tier_a} Tier A, {tier_b} Tier B companies")
    except Exception as e:
        print(f"⚠️  VCP sync skipped (will retry at runtime): {e}")

    # Test earnings season detection
    active, quarter = is_earnings_season()
    if active:
        print(f"✅ Earnings season: ACTIVE — {quarter}")
    else:
        print(f"ℹ️  Earnings season: INACTIVE (will auto-detect)")

    print("\n✅ Setup complete! Engine ready.")
    print("\nNext steps:")
    print("  python main.py calendar    → Run calendar scan")
    print("  python main.py detect      → Run result detection")
    print("  python main.py setup       → Re-run setup")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "detect"

    if cmd == "setup":
        run_full_setup()
    elif cmd == "calendar":
        init_db()
        run_calendar_job()
    elif cmd == "detect":
        init_db()
        run_detection_job()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python3 main.py [setup|calendar|detect]")
