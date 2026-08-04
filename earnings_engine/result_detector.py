"""
Result Detector — runs 3x daily
Scans BSE/NSE announcements for newly filed financial results.

Priority order:
  Plan A  — BSE API, today's filings        (primary — most reliable)
  Plan A2 — BSE API, yesterday's filings    (catches late/overnight filings)
  Plan B  — NSE API                         (best-effort, often blocked on servers)
  Plan C  — RSS                             (true last resort, strict filters only)
"""
import requests
import feedparser
import sqlite3
import re
from datetime import datetime, timedelta
from config import IST, DB_PATH

# ─── HEADERS ─────────────────────────────────────────────────────────────────
BSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.bseindia.com/",
    "Origin":          "https://www.bseindia.com",
}

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
}

# ─── PLAN A / A2: BSE ANNOUNCEMENTS (PRIMARY) ────────────────────────────────
# AnnSubCategoryGetData is the working endpoint (AnnGetData returns "No Record Found"
# when called with date params from non-browser clients).
# Without date params it returns the latest ~50 filings across all recent days.
BSE_ANN_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"


def _fetch_bse_for_date(date_str, label):
    """
    Fetch BSE Result announcements.
    date_str: YYYYMMDD  e.g. "20260704" — used for Plan A (today) / A2 (yesterday).
    When both dates return nothing, Plan A fallback queries without dates to get
    the latest batch of filings regardless of date.
    strCat=Result filters to result filings only — no keyword matching needed.
    """
    params = {
        "strCat":      "Result",
        "strPrevDate": date_str,
        "strScrip":    "",
        "strSearch":   "P",
        "strToDate":   date_str,
        "strType":     "C",
        "subcategory": "-1",
    }
    try:
        resp = requests.get(BSE_ANN_URL, params=params, headers=BSE_HEADERS, timeout=15)

        if resp.status_code != 200:
            print(f"[DETECTOR] BSE {label}: HTTP {resp.status_code}")
            return []

        raw = resp.text.strip()
        if not raw or raw[0] not in ('{', '['):
            print(f"[DETECTOR] BSE {label}: empty / non-JSON response")
            return []

        data = resp.json()
        rows = data.get("Table", data.get("Table1", []))
        if not isinstance(rows, list):
            print(f"[DETECTOR] BSE {label}: unexpected response structure")
            return []

        results = []
        for item in rows:
            newsid = str(item.get("NEWSID", "")).strip()
            if not newsid:
                continue

            attachment = item.get("ATTACHMENTNAME", "")
            pdf_url = (
                f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attachment}"
                if attachment else ""
            )

            # BSE uses numeric scrip codes — store as "BSE:XXXXXX"
            # Downstream processing can resolve to NSE symbol via company_profile.bse_code
            scrip_cd = str(item.get("SCRIP_CD", "")).strip()
            symbol   = f"BSE:{scrip_cd}" if scrip_cd else "BSE:UNKNOWN"

            results.append({
                "symbol":            symbol,
                "company_name":      item.get("SLONGNAME", item.get("LNAME", "")).strip(),
                "announcement_id":   f"BSE_{newsid}",
                "subject":           item.get("HEADLINE", item.get("CATEGORYNAME", "")).strip(),
                "announcement_date": item.get("NEWS_DT", date_str),
                "pdf_url":           pdf_url,
                "exchange":          "BSE",
                "source":            "BSE_API",
                "bse_scrip_cd":      scrip_cd,
            })

        print(f"[DETECTOR] BSE {label}: {len(results)} results found")
        return results

    except Exception as e:
        print(f"[DETECTOR] BSE {label} failed: {e}")
        return []


def fetch_bse_announcements():
    """
    Plan A + A2 — today and yesterday.
    Yesterday is always checked because filings submitted after market hours
    show up the next morning. De-dup happens downstream via announcement_id.
    """
    today     = datetime.now(IST).strftime("%Y%m%d")
    yesterday = (datetime.now(IST) - timedelta(days=1)).strftime("%Y%m%d")

    results = _fetch_bse_for_date(today, "today")
    results.extend(_fetch_bse_for_date(yesterday, "yesterday"))
    return results


# ─── PLAN B: NSE ANNOUNCEMENTS (BEST-EFFORT SUPPLEMENT) ─────────────────────
NSE_API_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities"

RESULT_KEYWORDS = [
    "financial result", "quarterly result", "annual result",
    "standalone result", "consolidated result",
    "unaudited result", "audited result",
    "q1 result", "q2 result", "q3 result", "q4 result",
]


def fetch_nse_announcements():
    """
    Plan B — NSE corporate announcements API.
    Requires a warm-up cookie hit on the homepage first.
    Frequently blocked on cloud/VPS servers — failure is handled gracefully.
    """
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=12)

        resp = session.get(NSE_API_URL, headers=NSE_HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"[DETECTOR] NSE Plan B: HTTP {resp.status_code}")
            return []

        raw = resp.text.strip()
        if not raw or raw[0] not in ('{', '['):
            print(f"[DETECTOR] NSE Plan B: non-JSON response")
            return []

        data = resp.json()
        if isinstance(data, dict):
            data = data.get("data", data.get("announcements", []))
        if not isinstance(data, list):
            return []

        results = []
        for item in data:
            subject = item.get("subject", item.get("an_desc", "")).strip()
            if not any(kw in subject.lower() for kw in RESULT_KEYWORDS):
                continue

            seq_no = str(item.get("an_seq_no", item.get("seqNo", ""))).strip()
            if not seq_no:
                continue

            results.append({
                "symbol":            item.get("symbol", "").strip(),
                "company_name":      item.get("company", item.get("companyName", "")).strip(),
                "announcement_id":   f"NSE_{seq_no}",
                "subject":           subject,
                "announcement_date": item.get("exchdisstime", item.get("anDt", "")),
                "pdf_url":           item.get("attchmntFile", item.get("attchmntText", "")),
                "exchange":          "NSE",
                "source":            "NSE_API",
            })

        print(f"[DETECTOR] NSE Plan B: {len(results)} results found")
        return results

    except Exception as e:
        print(f"[DETECTOR] NSE Plan B failed: {e}")
        return []


# ─── PLAN C: RSS (TRUE LAST RESORT) ──────────────────────────────────────────
# Only fires when BOTH BSE and NSE return zero results.
# Two-layer filter: noise blocklist + required filing pattern match.

RSS_FEEDS = [
    "https://economictimes.indiatimes.com/markets/earnings/rssfeeds/2143117.cms",
    "https://www.business-standard.com/rss/markets/earnings.rss",
]

# Noise: headlines that are articles/calendars, NOT actual filings
_NOISE_RE = re.compile(
    r"\bcalendar\b"
    r"|\bschedule[ds]?\b"
    r"|\bupcoming\b"
    r"|\bnext\s+week\b"
    r"|\bwhat\s+to\s+expect\b"
    r"|\b52.week\s+high\b"
    r"|\bshares?\s+(hit|touch|near|surge|fall|rise)\b"
    r"|\bahead\s+of\b"
    r"|\bbefore\s+(the\s+)?results?\b"
    r"|\bwatch\s*list\b"
    r"|\bunlisted\b"
    r"|\bboard\s+meeting\b",
    re.IGNORECASE,
)

# Signal: phrases that confirm this IS an actual result announcement
_FILING_RE = re.compile(
    r"q[1-4]\s+fy\d{2,4}\s+results?"
    r"|q[1-4]\s+results?\s+(announced|declared|out|today)"
    r"|quarterly\s+results?\s+(announced|declared|out)"
    r"|annual\s+results?\s+(announced|declared|out)"
    r"|pat\b.{0,40}\b(jumps?|rises?|falls?|drops?|up|down)\s+\d"
    r"|net\s+profit\b.{0,30}\b(up|down|rises?|falls?)\s+\d",
    re.IGNORECASE,
)

# NSE symbols — word-boundary matched to prevent substring false positives.
# Short ambiguous tickers (LT, IT, GT) excluded; L&T matched by company name.
_KNOWN_SYMBOLS = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "WIPRO",
    "HCLTECH", "BAJFINANCE", "AXISBANK", "KOTAKBANK", "TITAN", "ASIANPAINT",
    "MARUTI", "SUNPHARMA", "NESTLEIND", "ULTRACEMCO", "NTPC", "POWERGRID",
    "ONGC", "COALINDIA", "TECHM", "DIVISLAB", "DRREDDY", "CIPLA", "EICHERMOT",
    "ADANIENT", "TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "TATAMOTORS",
    "BPCL", "GRASIM", "INDUSINDBK", "ITC", "DABUR", "MARICO", "BRITANNIA",
    "TRENT", "DMART", "HAVELLS", "PIDILITIND", "BERGEPAINT", "TATACONSUM",
    "BAJAJFINSV", "ADANIPORTS", "HINDUNILVR", "ZYDUSLIFE", "AUROPHARMA",
    "MAXFINSERV", "LTIM", "LTF", "HDFCLIFE", "SBICARD", "SBILIFE",
    "INDUSTOWER", "BHARTIARTL", "JSWENERGY", "TATAPOWER", "APOLLOHOSP",
]
_SYMBOL_PATTERNS = [
    (sym, re.compile(rf"(?<![A-Z]){re.escape(sym)}(?![A-Z])"))
    for sym in _KNOWN_SYMBOLS
]


def extract_symbol(text):
    """Extract NSE ticker from a headline using word-boundary regex."""
    upper = text.upper()
    for sym, pattern in _SYMBOL_PATTERNS:
        if pattern.search(upper):
            return sym
    if re.search(r"\bL\s*&\s*T\b|\bLARSEN\b", upper):
        return "LT"
    return "UNKNOWN"


def fetch_rss_results():
    """Plan C — RSS emergency fallback with strict two-layer filtering."""
    results = []
    seen    = set()

    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:40]:
                title = entry.get("title", "").strip()
                link  = entry.get("link",  "").strip()
                if not title:
                    continue
                if _NOISE_RE.search(title):
                    continue
                if not _FILING_RE.search(title):
                    continue
                symbol = extract_symbol(title)
                if symbol == "UNKNOWN":
                    continue
                key = title[:70]
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    "symbol":            symbol,
                    "company_name":      title[:80],
                    "announcement_id":   f"RSS_{hash(title) & 0xFFFFFFFF}",
                    "subject":           title,
                    "announcement_date": datetime.now(IST).isoformat(),
                    "pdf_url":           link,
                    "exchange":          "NSE",
                    "source":            "RSS",
                })
        except Exception as e:
            print(f"[DETECTOR] RSS error ({url}): {e}")

    print(f"[DETECTOR] RSS Plan C: {len(results)} results found")
    return results


# ─── DEDUP & LOG ──────────────────────────────────────────────────────────────
def is_already_processed(announcement_id):
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute(
        "SELECT processed FROM result_detection_log WHERE announcement_id=?",
        (announcement_id,),
    ).fetchone()
    conn.close()
    return bool(row and row[0] == 1)


def log_announcement(item):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO result_detection_log
              (symbol, announcement_id, detected_at, source, announcement_url, processed)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (
                item.get("symbol", ""),
                item.get("announcement_id", ""),
                datetime.now(IST).isoformat(),
                item.get("source", ""),
                item.get("pdf_url", ""),
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"[DETECTOR] Log error: {e}")
    conn.close()


# ─── MAIN DETECTION RUN ───────────────────────────────────────────────────────
def run_result_detection():
    """
    Full result detection scan.
    BSE is primary. NSE supplements. RSS only when both APIs fail completely.
    """
    print(
        f"[DETECTOR] {datetime.now(IST).strftime('%d %b %Y %I:%M %p IST')}"
        f" — Scanning for new results..."
    )

    announcements = []
    bse_ok = False
    nse_ok = False

    # Plan A / A2: BSE today + yesterday
    bse_results = fetch_bse_announcements()
    if bse_results:
        announcements.extend(bse_results)
        bse_ok = True

    # Plan B: NSE (supplement, not replacement)
    nse_results = fetch_nse_announcements()
    if nse_results:
        announcements.extend(nse_results)
        nse_ok = True

    # Plan C: RSS only if both APIs completely failed
    if not bse_ok and not nse_ok:
        print("[DETECTOR] Both APIs failed — falling back to RSS (Plan C)")
        announcements.extend(fetch_rss_results())
    else:
        sources = (["BSE"] if bse_ok else []) + (["NSE"] if nse_ok else [])
        print(f"[DETECTOR] Primary sources OK ({', '.join(sources)}) — RSS skipped")

    # Filter to new unprocessed announcements
    new_results = []
    for item in announcements:
        aid = item.get("announcement_id", "")
        if aid and not is_already_processed(aid):
            log_announcement(item)
            new_results.append(item)

    print(f"[DETECTOR] {len(new_results)} new unprocessed results found")
    return new_results


if __name__ == "__main__":
    from database import init_db
    init_db()
    results = run_result_detection()
    for r in results[:5]:
        print(r)
