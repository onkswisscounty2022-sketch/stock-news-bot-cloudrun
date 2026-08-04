"""
Symbol Resolver
Fetches BSE master securities list and caches in SQLite company_profile.
Maps BSE numeric scrip codes → NSE ticker + full company name.

Refreshes weekly (or on demand). Fast lookup from local cache.
"""
import requests
import sqlite3
import json
import re
from datetime import datetime, timedelta
from config import DB_PATH, IST

BSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept":  "application/json, */*",
    "Referer": "https://www.bseindia.com/",
}

BSE_MASTER_URL = (
    "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
    "?Group=&Scripcode=&industry=&segment=Equity&status=Active"
)

# In-memory cache — loaded once per process
_CACHE:      dict = {}   # scrip_cd (str) → profile
_NSE_INDEX:  dict = {}   # nse_symbol.upper() → profile
_NAME_INDEX: dict = {}   # compressed_name → profile


# ─── FETCH & POPULATE ─────────────────────────────────────────────────────────
def _fetch_bse_master() -> list:
    """Download full BSE equity master list (~5000 companies)."""
    try:
        resp = requests.get(BSE_MASTER_URL, headers=BSE_HEADERS, timeout=30)
        if resp.status_code == 200 and resp.text.strip().startswith("["):
            data = resp.json()
            print(f"[RESOLVER] BSE master fetched: {len(data)} securities")
            return data
        else:
            print(f"[RESOLVER] BSE master fetch failed: HTTP {resp.status_code}")
            return []
    except Exception as e:
        print(f"[RESOLVER] BSE master fetch error: {e}")
        return []


def refresh_master(force: bool = False):
    """
    Populate company_profile from BSE master list.
    Skips if already refreshed in last 7 days (unless force=True).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Check last refresh date
    if not force:
        row = conn.execute(
            "SELECT updated_at FROM company_profile ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row and row["updated_at"]:
            try:
                last = datetime.fromisoformat(row["updated_at"])
                if datetime.now() - last < timedelta(days=7):
                    print("[RESOLVER] Master cache fresh — skipping refresh")
                    conn.close()
                    _load_cache_from_db()
                    return
            except Exception:
                pass

    print("[RESOLVER] Refreshing BSE master data...")
    data = _fetch_bse_master()
    if not data:
        print("[RESOLVER] No data — using existing DB cache")
        conn.close()
        _load_cache_from_db()
        return

    now = datetime.now(IST).isoformat()
    upserted = 0
    for item in data:
        scrip_cd   = str(item.get("SCRIP_CD", "")).strip()
        nse_symbol = str(item.get("scrip_id", "")).strip()   # NSE ticker
        name       = str(item.get("Scrip_Name", "")).strip()
        isin       = str(item.get("ISIN_NUMBER", "")).strip()

        if not scrip_cd:
            continue

        # Use NSE symbol as primary key if available, else BSE:XXXXXX
        symbol = nse_symbol if nse_symbol else f"BSE:{scrip_cd}"

        try:
            conn.execute("""
            INSERT INTO company_profile
                (symbol, name, isin, bse_code, nse_code, exchange, updated_at)
            VALUES (?, ?, ?, ?, ?, 'BSE', ?)
            ON CONFLICT(symbol) DO UPDATE SET
                name       = excluded.name,
                isin       = excluded.isin,
                bse_code   = excluded.bse_code,
                nse_code   = excluded.nse_code,
                updated_at = excluded.updated_at
            """, (symbol, name, isin, scrip_cd, nse_symbol, now))
            upserted += 1
        except Exception as e:
            pass  # Skip individual bad rows silently

    conn.commit()
    conn.close()
    print(f"[RESOLVER] Upserted {upserted} companies into company_profile")
    _load_cache_from_db()


def _load_cache_from_db():
    """Load bse_code → profile mapping into memory from DB."""
    global _CACHE, _NSE_INDEX, _NAME_INDEX
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Load ALL company_profile rows — including VCP companies without bse_code
    rows = conn.execute(
        "SELECT symbol, name, bse_code, nse_code, isin, tier FROM company_profile"
    ).fetchall()
    conn.close()

    _CACHE      = {}   # bse_scrip_cd → profile
    _NSE_INDEX  = {}   # nse_symbol.upper() → profile
    _NAME_INDEX = {}   # compressed_name → profile  (spaces/punctuation removed)

    for row in rows:
        bse_cd = str(row["bse_code"] or "").strip()
        nse    = str(row["nse_code"] or row["symbol"] or "").strip().upper()
        name   = str(row["name"] or "").strip()

        profile = {
            "symbol":       row["symbol"] or nse,
            "company_name": name,
            "nse_symbol":   nse,
            "isin":         row["isin"] or "",
            "tier":         row["tier"] or "D",
            "bse_scrip_cd": bse_cd,
        }

        # BSE code index
        if bse_cd:
            _CACHE[bse_cd] = profile

        # NSE symbol index (includes VCP companies)
        if nse:
            _NSE_INDEX[nse] = profile

        # Name index
        name_compressed = re.sub(r'[^A-Z0-9]', '', name.upper())
        if name_compressed:
            _NAME_INDEX[name_compressed] = profile

    print(f"[RESOLVER] Cache loaded: {len(_CACHE)} BSE scrip mappings, "
          f"{len(_NSE_INDEX)} NSE symbols, {len(_NAME_INDEX)} name entries")


# ─── PUBLIC LOOKUP ────────────────────────────────────────────────────────────
def resolve(bse_scrip_cd: str) -> dict:
    """
    Resolve a BSE numeric scrip code to full profile.

    Returns dict with keys:
        symbol        — NSE ticker (e.g. RELIANCE) or BSE:XXXXXX if unknown
        company_name  — Full company name
        nse_symbol    — NSE ticker (may be empty for BSE-only companies)
        isin          — ISIN code
        tier          — A/B/C/D

    Falls back to per-scrip API call if not in cache.
    """
    scrip_cd = str(bse_scrip_cd).strip()

    # Try in-memory cache first
    if scrip_cd in _CACHE:
        return _CACHE[scrip_cd]

    # Try single-scrip BSE API as fallback
    try:
        url = f"https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w?Debtflag=&scripcode={scrip_cd}&seriesid="
        resp = requests.get(url, headers=BSE_HEADERS, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            cmpname = data.get("Cmpname", {})
            full_name = cmpname.get("FullN", "") or cmpname.get("ShortN", "")
            short_id  = cmpname.get("ShortN", "")
            if full_name:
                result = {
                    "symbol":       short_id.upper() if short_id else f"BSE:{scrip_cd}",
                    "company_name": full_name,
                    "nse_symbol":   short_id.upper() if short_id else "",
                    "isin":         "",
                    "tier":         "D",
                }
                # Cache it for the rest of the session
                _CACHE[scrip_cd] = result
                return result
    except Exception:
        pass

    # Final fallback
    return {
        "symbol":       f"BSE:{scrip_cd}",
        "company_name": f"BSE:{scrip_cd}",
        "nse_symbol":   "",
        "isin":         "",
        "tier":         "D",
    }


def enrich_detection(item: dict) -> dict:
    """
    Enrich a detection result dict with resolved company info.
    Modifies in-place and returns the dict.
    """
    raw_symbol = item.get("symbol", "")

    # Only resolve BSE: prefixed symbols
    if not raw_symbol.startswith("BSE:"):
        return item

    scrip_cd = raw_symbol.replace("BSE:", "").strip()
    if not scrip_cd:
        return item

    profile = resolve(scrip_cd)

    item["symbol"]       = profile["symbol"]
    item["company_name"] = profile["company_name"]
    item["nse_symbol"]   = profile["nse_symbol"]
    item["isin"]         = profile["isin"]
    item["tier"]         = profile["tier"]
    item["bse_scrip_cd"] = scrip_cd

    return item


def search(query: str, max_results: int = 5) -> list:
    """
    Search for a company by:
      - Exact NSE symbol  (RVNL, INFY, TCS)
      - Partial NSE symbol (RVN → RVNL)
      - Company name keywords (rail vikas, desi farms, easy trip)
      - BSE scrip code (numeric)

    Returns list of profile dicts sorted by match quality.
    """
    ensure_cache_loaded()
    query = query.strip()

    # Numeric → direct scrip code lookup
    if query.isdigit():
        p = resolve(query)
        return [p] if p.get("company_name") != f"BSE:{query}" else []

    q_upper      = query.upper().strip()
    q_compressed = re.sub(r'[^A-Z0-9]', '', q_upper)

    results = []
    seen    = set()

    def _add(profile, score):
        key = profile.get("bse_scrip_cd") or profile.get("nse_symbol") or profile.get("company_name")
        if key and key not in seen:
            seen.add(key)
            results.append((score, profile))

    # 1. Exact NSE symbol match (highest priority)
    if q_upper in _NSE_INDEX:
        _add(_NSE_INDEX[q_upper], 100)

    # 2. Exact compressed name match
    if q_compressed in _NAME_INDEX:
        _add(_NAME_INDEX[q_compressed], 90)

    # 3. NSE symbol starts with query
    for sym, profile in _NSE_INDEX.items():
        if sym.startswith(q_upper) and len(q_upper) >= 2:
            _add(profile, 80)

    # 4. Compressed name contains query (substring)
    for name_c, profile in _NAME_INDEX.items():
        if q_compressed in name_c and len(q_compressed) >= 3:
            _add(profile, 70)

    # 5. Original name contains query words (fuzzy — each word must appear)
    query_words = [w for w in q_upper.split() if len(w) >= 3]
    if query_words:
        for bse_cd, profile in _CACHE.items():
            name_upper = profile["company_name"].upper()
            if all(w in name_upper for w in query_words):
                _add(profile, 60)

    # 6. BSE per-scrip API fallback (works from GCP, no cookie needed)
    if not results:
        try:
            url = (f"https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w"
                   f"?Debtflag=&scripcode=&seriesid=&strSearch={query}")
            resp = requests.get(url, headers=BSE_HEADERS, timeout=10)
            if resp.status_code == 200 and resp.text.strip().startswith(("{","[")):
                data  = resp.json()
                if data is None:
                    raise ValueError("null response")
                items = data if isinstance(data, list) else [data]
                for item in items[:5]:
                    if not isinstance(item, dict):
                        continue
                    cmp       = item.get("Cmpname", item) or {}
                    if not isinstance(cmp, dict):
                        cmp = {}
                    full_name = cmp.get("FullN", "") or cmp.get("ShortN", "") or item.get("FullN", "")
                    nse_sym   = (cmp.get("ShortN", "") or item.get("ShortN", "")).upper().strip()
                    scrip_cd  = str(item.get("scripcode", item.get("SCRIP_CD", ""))).strip()
                    if full_name:
                        profile = {
                            "symbol":       nse_sym or f"BSE:{scrip_cd}",
                            "company_name": full_name,
                            "nse_symbol":   nse_sym,
                            "isin":         item.get("ISIN", ""),
                            "tier":         "D",
                            "bse_scrip_cd": scrip_cd,
                        }
                        _add(profile, 50)
        except Exception as e:
            print(f"[RESOLVER] BSE search fallback error: {e}")

    # Sort by score descending
    results.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in results[:max_results]]


def ensure_cache_loaded():
    """Call at startup — loads cache from DB without triggering a refresh."""
    if not _CACHE:
        _load_cache_from_db()
        if not _CACHE:
            # DB empty — do a full refresh
            refresh_master()


if __name__ == "__main__":
    from database import init_db
    init_db()
    refresh_master(force=True)

    # Test a few known BSE codes
    test_codes = ["500325", "532540", "523465", "530755", "500112"]
    print("\nTest resolutions:")
    for code in test_codes:
        r = resolve(code)
        print(f"  {code} → {r['symbol']:15} | {r['company_name']}")
