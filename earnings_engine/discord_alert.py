"""
Discord Alert
Sends earnings report to #earning-alerts channel via webhook.
Splits into 3 messages (Part 1/3, 2/3, 3/3) to stay within Discord's 2000 char limit.
"""
import requests
import time
from config import DISCORD_WEBHOOK_EARNINGS
from alert_formatter import format_discord


def send_discord_alert(item: dict) -> bool:
    """
    Send 3-part Discord alert for a single company result.
    Returns True if all parts sent successfully.
    """
    webhook_url = DISCORD_WEBHOOK_EARNINGS
    if not webhook_url:
        print("[DISCORD] Webhook URL not configured — skipping")
        return False

    symbol   = item.get("symbol", "UNKNOWN")
    company  = item.get("company_name", symbol)
    quarter  = item.get("quarter", "")
    fy       = item.get("fiscal_year", "")

    print(f"[DISCORD] Sending alert for {company} ({symbol}) {quarter} {fy}...")

    parts = format_discord(item)
    success_count = 0

    for i, message in enumerate(parts, 1):
        try:
            resp = requests.post(
                webhook_url,
                json={"content": message},
                timeout=15
            )
            if resp.status_code in (200, 204):
                print(f"[DISCORD] Part {i}/3 sent ✓")
                success_count += 1
            else:
                print(f"[DISCORD] Part {i}/3 failed: HTTP {resp.status_code} — {resp.text[:100]}")

            # Small delay between messages to avoid rate limiting
            if i < len(parts):
                time.sleep(1)

        except Exception as e:
            print(f"[DISCORD] Part {i}/3 error: {e}")

    print(f"[DISCORD] {success_count}/{len(parts)} parts sent for {symbol}")
    return success_count == len(parts)


def send_discord_batch(items: list) -> int:
    """Send Discord alerts for a batch of analyzed results."""
    sent = 0
    for item in items:
        if item.get("analysis_status") == "analyzed":
            if send_discord_alert(item):
                sent += 1
            time.sleep(2)  # Rate limit between companies
    print(f"[DISCORD] Batch done — {sent}/{len(items)} alerts sent")
    return sent


def _build_full_item_from_db():
    """
    Load latest analyzed result from DB and reconstruct full item dict.
    Used by test scripts to send real data alerts.
    """
    import sqlite3 as _sq
    import json as _json
    from config import DB_PATH

    conn = _sq.connect(DB_PATH)
    conn.row_factory = _sq.Row

    analysis = conn.execute("""
        SELECT a.*, f.revenue_cr, f.ebitda_cr, f.ebitda_margin_pct, f.pat_cr, f.eps,
               f.cash_cr, f.debt_cr, f.net_debt_cr, f.operating_cf_cr, f.free_cf_cr,
               f.capex_cr, f.dividend_per_share, f.book_value, f.order_book_cr,
               f.guidance_text, COALESCE(f.result_type, 'Consolidated') as result_type
        FROM earnings_ai_analysis a
        JOIN earnings_financials f ON a.symbol=f.symbol AND a.quarter=f.quarter AND a.fiscal_year=f.fiscal_year
        WHERE f.revenue_cr IS NOT NULL
        ORDER BY a.created_at DESC, (
            COALESCE(f.cash_cr,0) + COALESCE(f.debt_cr,0) +
            COALESCE(f.operating_cf_cr,0) + COALESCE(f.capex_cr,0)
        ) DESC
        LIMIT 1
    """).fetchone()

    if not analysis:
        conn.close()
        return None

    row = dict(analysis)

    prev_rows = conn.execute("""
        SELECT * FROM earnings_financials
        WHERE symbol=? AND NOT (quarter=? AND fiscal_year=?)
        ORDER BY fiscal_year DESC, quarter DESC LIMIT 2
    """, (row["symbol"], row["quarter"], row["fiscal_year"])).fetchall()

    cp = conn.execute("SELECT name FROM company_profile WHERE symbol=? OR nse_code=?",
                      (row["symbol"], row["symbol"])).fetchone()
    conn.close()  # Fix 2: single connection, properly closed

    prev_list    = [dict(r) for r in prev_rows]
    prev_q       = prev_list[0] if len(prev_list) > 0 else {}
    yoy_q        = prev_list[1] if len(prev_list) > 1 else {}
    company_name = dict(cp)["name"] if cp else row["symbol"]

    def _sl(val):
        if isinstance(val, list): return val
        if isinstance(val, str):
            try: return _json.loads(val)
            except: return []
        return []

    # Fix 3: compute growth_analysis from actual pnl data
    def _trend(vals):
        clean = [v for v in vals if v is not None]
        if len(clean) < 2: return "Insufficient Data"
        ch = [clean[i] - clean[i+1] for i in range(len(clean)-1)]
        if all(c > 0 for c in ch): return "Consistently ↑"
        elif all(c < 0 for c in ch): return "Deteriorating ↓↓"
        elif ch[0] > 0: return "Recovering ↑"
        elif ch[0] < 0: return "Decelerating ↓"
        return "Stable →"

    rev_vals = [yoy_q.get("revenue_cr"), prev_q.get("revenue_cr"), row.get("revenue_cr")]
    pat_vals = [yoy_q.get("pat_cr"),     prev_q.get("pat_cr"),     row.get("pat_cr")]
    rev_trend = _trend(rev_vals)
    pat_trend = _trend(pat_vals)

    # Overall verdict
    yoy_rev_pct = None
    try:
        if row.get("revenue_cr") and yoy_q.get("revenue_cr") and yoy_q["revenue_cr"] != 0:
            yoy_rev_pct = round(((row["revenue_cr"] - yoy_q["revenue_cr"]) / abs(yoy_q["revenue_cr"])) * 100, 1)
    except Exception:
        pass

    if yoy_rev_pct and yoy_rev_pct > 20:
        overall_verdict = f"STRONG GROWTH — Revenue ▲{yoy_rev_pct:.1f}% YoY, fundamentals improving"
    elif yoy_rev_pct and yoy_rev_pct > 5:
        overall_verdict = "GROWTH INTACT — Steady performance, watching for acceleration"
    elif yoy_rev_pct and yoy_rev_pct > 0:
        overall_verdict = "MILD GROWTH — Revenue growing but pace is slow"
    else:
        overall_verdict = "INSUFFICIENT DATA — First quarter in system"

    growth_analysis = {
        "revenue":        f"Revenue: {rev_trend}",
        "pat":            f"PAT: {pat_trend}",
        "overall_verdict": overall_verdict,
    }

    return {
        "symbol":          row["symbol"],
        "company_name":    company_name,
        "quarter":         row["quarter"],
        "fiscal_year":     row["fiscal_year"],
        "overall_score":   row["overall_score"],
        "classification":  row["classification"],
        "analysis_status": "analyzed",
        "financials": {
            "revenue_cr":         row["revenue_cr"],
            "ebitda_cr":          row["ebitda_cr"],
            "ebitda_margin_pct":  row["ebitda_margin_pct"],
            "pat_cr":             row["pat_cr"],
            "eps":                row["eps"],
            "cash_cr":            row["cash_cr"],
            "debt_cr":            row["debt_cr"],
            "net_debt_cr":        row["net_debt_cr"],
            "operating_cf_cr":    row["operating_cf_cr"],
            "free_cf_cr":         row["free_cf_cr"],
            "capex_cr":           row["capex_cr"],
            "dividend_per_share": row["dividend_per_share"],
            "book_value":         row["book_value"],
            "order_book_cr":      row["order_book_cr"],
            "guidance_text":      row["guidance_text"],
            "result_type":        "Consolidated",   # default — not stored in DB yet
        },
        "pnl_comparison": {
            "current": {
                "quarter": row["quarter"], "fiscal_year": row["fiscal_year"],
                "revenue_cr": row["revenue_cr"], "ebitda_cr": row["ebitda_cr"],
                "ebitda_margin_pct": row["ebitda_margin_pct"],
                "pat_cr": row["pat_cr"], "eps": row["eps"],
                "result_type": "Consolidated",
            },
            "prev_quarter": {
                "quarter": prev_q.get("quarter"), "fiscal_year": prev_q.get("fiscal_year"),
                "revenue_cr": prev_q.get("revenue_cr"), "ebitda_cr": prev_q.get("ebitda_cr"),
                "ebitda_margin_pct": prev_q.get("ebitda_margin_pct"),
                "pat_cr": prev_q.get("pat_cr"), "eps": prev_q.get("eps"),
            },
            "same_qtr_last_year": {
                "quarter": yoy_q.get("quarter"), "fiscal_year": yoy_q.get("fiscal_year"),
                "revenue_cr": yoy_q.get("revenue_cr"), "ebitda_cr": yoy_q.get("ebitda_cr"),
                "ebitda_margin_pct": yoy_q.get("ebitda_margin_pct"),
                "pat_cr": yoy_q.get("pat_cr"), "eps": yoy_q.get("eps"),
            },
        },
        "narrative": {
            "executive_summary":   row.get("executive_summary", ""),
            "auditor_observations":row.get("auditor_observations", ""),
            "qoq_analysis":        row.get("qoq_analysis", ""),
            "yoy_analysis":        row.get("yoy_analysis", ""),
            "trading_notes":       row.get("trading_notes", ""),
            "marathi_summary":     row.get("marathi_summary", ""),
            "bullish_factors":     _sl(row.get("bullish_factors", [])),
            "bearish_factors":     _sl(row.get("bearish_factors", [])),
        },
        "scores": {
            "financial":     row.get("financial_score"),
            "growth":        row.get("growth_score"),
            "quality":       row.get("quality_score"),
            "balance_sheet": row.get("balance_sheet_score"),
            "cashflow":      row.get("cashflow_score"),
            "consistency":   row.get("consistency_score"),
            "technical":     row.get("technical_score"),
        },
        "concall":         {"status": "not_announced", "message": "Concall not yet announced on BSE"},
        "peer_comparison": {"available": False},
        "growth_analysis": growth_analysis,
    }


if __name__ == "__main__":
    """Test with latest analyzed result from DB — using real extracted data."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from database import init_db

    init_db()
    item = _build_full_item_from_db()
    if not item:
        print("No analyzed results in DB yet. Run: python3 lookup.py TIMEX first.")
        sys.exit(0)

    print(f"Testing Discord alert for {item['company_name']} ({item['symbol']}) {item['quarter']} {item['fiscal_year']}...")
    result = send_discord_alert(item)
    print(f"Discord test: {'✓ Success' if result else '✗ Failed'}")
