"""
Earnings Calendar Scanner
Fetches upcoming earnings from NSE/BSE (Plan A)
Falls back to Google News + ET Markets RSS (Plan B) if blocked.
Detects active earnings season automatically.
"""
import requests
import feedparser
import re
import sqlite3
import json
from datetime import datetime, timedelta
from config import IST, DB_PATH

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# ─── EARNINGS SEASON DETECTION ───────────────────────────────────────────────
EARNINGS_SEASONS = {
    "Q1": (7, 8),    # July-August
    "Q2": (10, 11),  # October-November
    "Q3": (1, 2),    # January-February
    "Q4": (4, 5),    # April-May
}

def is_earnings_season():
    """Check if we are currently in an earnings season."""
    month = datetime.now(IST).month
    for quarter, months in EARNINGS_SEASONS.items():
        if month in months:
            return True, quarter
    return False, None

def get_current_quarter():
    """Return the current reporting quarter."""
    month = datetime.now(IST).month
    year  = datetime.now(IST).year
    for quarter, months in EARNINGS_SEASONS.items():
        if month in months:
            # Q1 = April-June results, reported July-Aug
            fy_map = {"Q1": "Q1", "Q2": "Q2", "Q3": "Q3", "Q4": "Q4"}
            return fy_map[quarter], f"FY{str(year+1)[2:]}"
    return None, None

# ─── PLAN A: NSE DIRECT ───────────────────────────────────────────────────────
def fetch_nse_calendar():
    """Fetch board meeting / results calendar from NSE."""
    try:
        session = requests.Session()
        # First hit NSE homepage to get cookies
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=10)

        today    = datetime.now(IST).strftime("%d-%m-%Y")
        end_date = (datetime.now(IST) + timedelta(days=15)).strftime("%d-%m-%Y")
        url = f"https://www.nseindia.com/api/event-calendar?index=equities&from_date={today}&to_date={end_date}"

        resp = session.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            results = []
            for item in data:
                purpose = item.get("purpose", "").lower()
                if any(kw in purpose for kw in ["financial result", "quarterly result", "annual result", "q1", "q2", "q3", "q4"]):
                    results.append({
                        "symbol":        item.get("symbol", ""),
                        "company_name":  item.get("company", ""),
                        "result_date":   item.get("date", ""),
                        "purpose":       item.get("purpose", ""),
                        "exchange":      "NSE",
                        "source":        "NSE_CALENDAR",
                    })
            print(f"[CALENDAR] NSE Plan A: {len(results)} companies found")
            return results
        else:
            print(f"[CALENDAR] NSE returned {resp.status_code} — trying Plan B")
            return []
    except Exception as e:
        print(f"[CALENDAR] NSE Plan A failed: {e} — trying Plan B")
        return []

# ─── PLAN B: RSS FALLBACK ─────────────────────────────────────────────────────
CALENDAR_RSS_FEEDS = [
    "https://economictimes.indiatimes.com/markets/earnings/rssfeeds/2143117.cms",
    "https://www.moneycontrol.com/rss/results.xml",
    "https://news.google.com/rss/search?q=quarterly+results+india+NSE+BSE&hl=en-IN&gl=IN&ceid=IN:en",
]

def fetch_calendar_plan_b():
    """RSS-based fallback for earnings calendar."""
    results = []
    seen = set()
    quarter, fy = get_current_quarter()

    for url in CALENDAR_RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:30]:
                title   = entry.get("title", "").strip()
                link    = entry.get("link", "").strip()
                summary = entry.get("summary", "").strip()
                text    = (title + " " + summary).lower()

                if any(kw in text for kw in ["result", "q1", "q2", "q3", "q4", "quarterly", "earnings"]):
                    key = title[:60]
                    if key not in seen:
                        seen.add(key)
                        # Try to extract symbol
                        symbol = extract_symbol_from_text(title)
                        results.append({
                            "symbol":       symbol or title[:20],
                            "company_name": title,
                            "result_date":  datetime.now(IST).strftime("%Y-%m-%d"),
                            "quarter":      quarter,
                            "fiscal_year":  fy,
                            "exchange":     "NSE",
                            "source":       "RSS_FALLBACK",
                        })
        except Exception as e:
            print(f"[CALENDAR] RSS fallback error: {e}")

    print(f"[CALENDAR] Plan B RSS: {len(results)} items found")
    return results

def extract_symbol_from_text(text):
    """Try to extract NSE symbol from news title."""
    # Common patterns: "RELIANCE Q1 results", "TCS quarterly results"
    known_symbols = [
        "RELIANCE","TCS","INFY","HDFCBANK","ICICIBANK","SBIN","WIPRO","HCLTECH",
        "BAJFINANCE","AXISBANK","KOTAKBANK","LT","TITAN","ASIANPAINT","MARUTI",
        "SUNPHARMA","NESTLEIND","ULTRACEMCO","NTPC","POWERGRID","ONGC","COALINDIA",
        "BAJAJFINSV","TECHM","DIVISLAB","DRREDDY","CIPLA","EICHERMOT","ADANIENT",
        "TATASTEEL","JSWSTEEL","HINDALCO","VEDL","TATAMOTORS","BPCL","GRASIM",
        "INDUSINDBK","YESBANK","TATACONSUM","DABUR","MARICO","BRITANNIA","ITC",
        "HAVELLS","VOLTAS","PIDILITIND","BERGEPAINT","WHIRLPOOL","TRENT","DMART",
    ]
    t = text.upper()
    for sym in known_symbols:
        if sym in t:
            return sym
    return None

# ─── SAVE TO DATABASE ─────────────────────────────────────────────────────────
def save_calendar_to_db(entries):
    """Store calendar entries in SQLite."""
    conn = sqlite3.connect(DB_PATH)
    saved = 0
    for e in entries:
        try:
            # Parse date
            raw_date = e.get("result_date", "")
            try:
                if "-" in raw_date and len(raw_date) == 10:
                    result_date = raw_date
                else:
                    result_date = datetime.strptime(raw_date, "%d-%m-%Y").strftime("%Y-%m-%d")
            except:
                result_date = datetime.now(IST).strftime("%Y-%m-%d")

            quarter, fy = get_current_quarter()

            conn.execute("""
            INSERT OR IGNORE INTO earnings_calendar
              (symbol, company_name, result_date, quarter, fiscal_year, exchange, status, tier)
            VALUES (?, ?, ?, ?, ?, ?, 'upcoming',
              COALESCE((SELECT tier FROM company_profile WHERE symbol=?), 'D'))
            """, (
                e.get("symbol", "UNKNOWN"),
                e.get("company_name", ""),
                result_date,
                e.get("quarter", quarter),
                e.get("fiscal_year", fy),
                e.get("exchange", "NSE"),
                e.get("symbol", "UNKNOWN"),
            ))
            saved += 1
        except Exception as ex:
            print(f"[CALENDAR] DB save error: {ex}")

    conn.commit()
    conn.close()
    print(f"[CALENDAR] Saved {saved} entries to database")
    return saved

# ─── MAIN FUNCTION ────────────────────────────────────────────────────────────
def run_calendar_scan():
    """Main calendar scan — tries NSE first, falls back to RSS."""
    active, quarter = is_earnings_season()
    if not active:
        print(f"[CALENDAR] Not in earnings season — skipping scan")
        return []

    print(f"[CALENDAR] Earnings season active: {quarter} — scanning...")

    # Try NSE Plan A first
    entries = fetch_nse_calendar()

    # Fall back to Plan B if NSE blocked or returned nothing
    if not entries:
        entries = fetch_calendar_plan_b()

    if entries:
        save_calendar_to_db(entries)

    return entries

if __name__ == "__main__":
    from database import init_db
    init_db()
    entries = run_calendar_scan()
    print(f"Total: {len(entries)} entries")
    for e in entries[:5]:
        print(e)
