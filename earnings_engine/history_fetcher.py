"""
History Fetcher
Pulls last 4 quarters of financial data from Yahoo Finance for any NSE symbol.
Saves to earnings_financials DB so subsequent runs use local cache.

Used when:
  - Company appears for the first time (no DB history)
  - QoQ / YoY comparison would otherwise show N/A

Yahoo Finance NSE format: SYMBOL.NS  (e.g. TITAN.NS, RELIANCE.NS)
"""
import sqlite3
import json
from datetime import datetime
from config import DB_PATH, IST

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False
    print("[HISTORY] yfinance not installed — historical fetch disabled")


# ─── QUARTER HELPERS ─────────────────────────────────────────────────────────

def _date_to_quarter(dt) -> tuple:
    """
    Map a quarter end date to (quarter_label, fiscal_year).
    Indian FY: Apr-Mar
      Q1 = Apr-Jun
      Q2 = Jul-Sep
      Q3 = Oct-Dec
      Q4 = Jan-Mar
    """
    try:
        if hasattr(dt, 'month'):
            month = dt.month
            year  = dt.year
        else:
            d     = datetime.strptime(str(dt)[:10], "%Y-%m-%d")
            month = d.month
            year  = d.year

        if month in (4, 5, 6):
            return "Q1", f"FY{str(year + 1)[2:]}"
        elif month in (7, 8, 9):
            return "Q2", f"FY{str(year + 1)[2:]}"
        elif month in (10, 11, 12):
            return "Q3", f"FY{str(year + 1)[2:]}"
        else:  # Jan-Mar
            return "Q4", f"FY{str(year)[2:]}"
    except Exception:
        return None, None


# ─── YAHOO FINANCE FETCH ──────────────────────────────────────────────────────

def fetch_from_yahoo(symbol: str, quarters: int = 5) -> list:
    """
    Fetch last N quarters of financials from Yahoo Finance.

    Returns list of dicts (newest first):
    [{quarter, fiscal_year, revenue_cr, pat_cr, ebitda_cr,
      ebitda_margin_pct, eps, cash_cr, debt_cr, net_debt_cr, source}]
    """
    if not YF_AVAILABLE:
        return []

    # Try NSE first, then BSE suffix
    for suffix in [".NS", ".BO"]:
        ticker_sym = symbol.upper() + suffix
        try:
            ticker = yf.Ticker(ticker_sym)

            # Quarterly income statement
            income = ticker.quarterly_income_stmt
            if income is None or income.empty:
                continue

            # Quarterly balance sheet
            balance = ticker.quarterly_balance_sheet

            results = []
            cols = income.columns[:quarters]  # newest first

            for col in cols:
                try:
                    quarter, fy = _date_to_quarter(col)
                    if not quarter:
                        continue

                    # ── Income Statement ──────────────────────────────
                    def _get(df, *keys):
                        for k in keys:
                            try:
                                val = df.loc[k, col]
                                if val is not None and str(val) not in ("nan", "None", "<NA>"):
                                    return float(val)
                            except Exception:
                                pass
                        return None

                    revenue_raw  = _get(income, "Total Revenue", "Revenue")
                    pat_raw      = _get(income, "Net Income", "Net Income Common Stockholders")
                    ebitda_raw   = _get(income, "EBITDA", "Normalized EBITDA")
                    op_income    = _get(income, "Operating Income", "EBIT")
                    dep          = _get(income, "Reconciled Depreciation", "Depreciation And Amortization")
                    eps_raw      = _get(income, "Basic EPS", "Diluted EPS")

                    # EBITDA fallback: Operating Income + D&A
                    if ebitda_raw is None and op_income and dep:
                        ebitda_raw = op_income + dep

                    # Convert to Crores (Yahoo gives INR absolute)
                    def _to_cr(val):
                        return round(val / 1e7, 2) if val is not None else None

                    revenue_cr = _to_cr(revenue_raw)
                    pat_cr     = _to_cr(pat_raw)
                    ebitda_cr  = _to_cr(ebitda_raw)

                    margin = None
                    if ebitda_cr and revenue_cr and revenue_cr > 0:
                        margin = round((ebitda_cr / revenue_cr) * 100, 1)

                    # ── Balance Sheet ─────────────────────────────────
                    cash_raw  = None
                    debt_raw  = None
                    if balance is not None and not balance.empty and col in balance.columns:
                        cash_raw = _get(balance, "Cash And Cash Equivalents",
                                        "Cash Cash Equivalents And Short Term Investments")
                        debt_raw = _get(balance, "Total Debt", "Long Term Debt")

                    cash_cr = _to_cr(cash_raw)
                    debt_cr = _to_cr(debt_raw)
                    net_debt_cr = None
                    if debt_cr is not None and cash_cr is not None:
                        net_debt_cr = round(debt_cr - cash_cr, 2)

                    results.append({
                        "quarter":          quarter,
                        "fiscal_year":      fy,
                        "result_date":      str(col)[:10],
                        "revenue_cr":       revenue_cr,
                        "ebitda_cr":        ebitda_cr,
                        "ebitda_margin_pct":margin,
                        "pat_cr":           pat_cr,
                        "eps":              round(eps_raw, 2) if eps_raw else None,
                        "cash_cr":          cash_cr,
                        "debt_cr":          debt_cr,
                        "net_debt_cr":      net_debt_cr,
                        "source":           f"YAHOO_{suffix[1:]}",
                    })

                except Exception as e:
                    print(f"[HISTORY] Column parse error for {col}: {e}")
                    continue

            if results:
                print(f"[HISTORY] {symbol}: {len(results)} quarters from Yahoo ({suffix})")
                return results

        except Exception as e:
            print(f"[HISTORY] Yahoo fetch error for {ticker_sym}: {e}")
            continue

    print(f"[HISTORY] {symbol}: Yahoo Finance returned no data")
    return []


def fetch_from_rediff(symbol: str) -> list:
    """
    Fallback: Fetch last 5 quarters from Rediff Money.
    Works for ALL Indian listed companies — no login, not geo-blocked.

    Flow:
      1. Get BSE scrip code from DB
      2. Find Rediff internal ID by searching company listing page
      3. Fetch quarterly results page and parse HTML table
      4. Cache Rediff ID in DB for future runs (avoid repeat search)
    """
    import requests as _req
    from bs4 import BeautifulSoup as _BS
    import re as _re
    import sqlite3 as _sql

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept":     "text/html,application/xhtml+xml,*/*",
        "Referer":    "https://money.rediff.com/",
    }

    try:
        # ── Step 1: Get BSE scrip code and company name from DB ───────────────
        conn = _sql.connect(DB_PATH)
        # Add rediff_id column if not exists (safe to run every time)
        try:
            conn.execute("ALTER TABLE company_profile ADD COLUMN rediff_id TEXT")
            conn.commit()
        except Exception:
            pass  # Column already exists
        row  = conn.execute(
            "SELECT bse_code, name, rediff_id FROM company_profile WHERE symbol=? OR nse_code=?",
            (symbol, symbol)
        ).fetchone()
        conn.close()

        if not row or not row[0]:
            print(f"[HISTORY] {symbol}: no BSE code in DB — cannot fetch from Rediff")
            return []

        bse_code     = str(row[0]).strip()
        company_name = str(row[1] or "").strip()
        rediff_id    = row[2] if len(row) > 2 and row[2] else None

        # Build slug from company name (used later for URL)
        slug = _re.sub(r'[^a-zA-Z0-9\s-]', '', company_name)
        slug = _re.sub(r'\s+', '-', slug.strip())
        slug = _re.sub(r'-+', '-', slug).strip('-')

        # ── Step 2: Find Rediff internal ID via Google search ────────────────
        if not rediff_id:
            import urllib.parse as _ul
            try:
                # Google search to find exact Rediff URL
                search_query = f'site:money.rediff.com "{bse_code}" results-quarter'
                google_url   = f"https://www.google.com/search?q={_ul.quote(search_query)}&num=3"
                g_headers    = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0",
                    "Accept": "text/html,application/xhtml+xml",
                }
                g_resp = _req.get(google_url, headers=g_headers, timeout=15)
                if g_resp.status_code == 200:
                    # Extract Rediff URL from Google results
                    rediff_pattern = _re.compile(
                        r'money\.rediff\.com/companies/[^/]+/(\d+)/results-quarter'
                    )
                    match = rediff_pattern.search(g_resp.text)
                    if match:
                        rediff_id = match.group(1)
                        print(f"[HISTORY] {symbol}: Rediff ID found via Google = {rediff_id}")
            except Exception as e:
                print(f"[HISTORY] {symbol}: Google search for Rediff ID failed: {e}")

        if not rediff_id:
            print(f"[HISTORY] {symbol}: Rediff ID not found — no history available")
            return []

            # Cache rediff_id in DB for future runs
            try:
                conn2 = _sql.connect(DB_PATH)
                conn2.execute(
                    "UPDATE company_profile SET rediff_id=? WHERE symbol=? OR nse_code=?",
                    (rediff_id, symbol, symbol)
                )
                conn2.commit()
                conn2.close()
            except Exception as e:
                print(f"[HISTORY] Rediff ID cache error: {e}")

        # ── Step 3: Fetch quarterly results page ──────────────────────────────
        results_url = f"https://money.rediff.com/companies/{slug}/{rediff_id}/results-quarter"

        resp = _req.get(results_url, headers=headers, timeout=20)
        if resp.status_code != 200:
            print(f"[HISTORY] Rediff results HTTP {resp.status_code}")
            return []

        soup = _BS(resp.text, "html.parser")

        # ── Step 4: Parse the quarterly results table ─────────────────────────
        # Find column headers (period names like "Mar' 26", "Dec' 25")
        tables = soup.find_all("table")
        if not tables:
            print(f"[HISTORY] {symbol}: No tables found on Rediff page")
            return []

        # Find the results brief table
        periods      = []
        sales_row    = []
        op_profit    = []
        net_profit   = []
        eps_row      = []

        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = [td.get_text(strip=True) for td in row.find_all(["td","th"])]
                if not cells:
                    continue

                label = cells[0].lower() if cells else ""

                # Header row — extract period names
                if not periods and len(cells) >= 3:
                    # Check if cells look like period headers
                    period_pattern = _re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)['\s]*[\s']?\s*\d{2}", _re.IGNORECASE)
                    if any(period_pattern.search(c) for c in cells[1:]):
                        periods = cells[1:]  # Skip label column

                if "sales" in label and not sales_row:
                    sales_row = cells[1:]
                elif "operating profit" in label and not op_profit:
                    op_profit = cells[1:]
                elif "net profit" in label and not net_profit:
                    net_profit = cells[1:]
                elif "eps" in label and not eps_row:
                    eps_row = cells[1:]

        if not periods or not sales_row:
            print(f"[HISTORY] {symbol}: Could not parse Rediff table structure")
            return []

        # ── Step 5: Build results list ────────────────────────────────────────
        def _parse_cr(val):
            try:
                v = str(val).replace(",", "").replace("-", "").strip()
                return round(float(v), 2) if v and v != "" else None
            except Exception:
                return None

        def _parse_period(p):
            """Parse 'Mar' 26' or 'Mar 2026' to (quarter, fiscal_year)."""
            m = _re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[^\d]*(\d{2,4})', p, _re.IGNORECASE)
            if m:
                month_str = m.group(1)[:3].upper()
                year_str  = m.group(2)
                year = int(year_str) if len(year_str) == 4 else 2000 + int(year_str)
                month_map = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                             "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
                month = month_map.get(month_str)
                if month:
                    return _date_to_quarter(f"{year}-{month:02d}-01")
            return None, None

        results = []
        num_cols = min(len(periods), len(sales_row), 5)

        for i in range(num_cols):
            period = periods[i] if i < len(periods) else ""
            q, fy  = _parse_period(period)
            if not q:
                continue

            rev   = _parse_cr(sales_row[i]   if i < len(sales_row)    else None)
            ebitda= _parse_cr(op_profit[i]   if i < len(op_profit)    else None)
            pat   = _parse_cr(net_profit[i]  if i < len(net_profit)   else None)
            eps   = _parse_cr(eps_row[i]     if i < len(eps_row)      else None)

            margin = round(ebitda / rev * 100, 1) if ebitda and rev and rev > 0 else None

            results.append({
                "quarter":           q,
                "fiscal_year":       fy,
                "result_date":       period,
                "revenue_cr":        rev,
                "pat_cr":            pat,
                "ebitda_cr":         ebitda,
                "ebitda_margin_pct": margin,
                "eps":               eps,
                "cash_cr":           None,
                "debt_cr":           None,
                "net_debt_cr":       None,
                "source":            "REDIFF",
            })

        if results:
            print(f"[HISTORY] {symbol}: {len(results)} quarters from Rediff Money")
        return results

    except Exception as e:
        print(f"[HISTORY] Rediff fetch error for {symbol}: {e}")
        return []


# ─── SAVE TO DB ───────────────────────────────────────────────────────────────

def _save_history_to_db(symbol: str, quarters: list):
    """Save Yahoo-fetched historical quarters to earnings_financials."""
    if not quarters:
        return 0

    conn   = sqlite3.connect(DB_PATH)
    saved  = 0
    now    = datetime.now(IST).isoformat()

    for q in quarters:
        try:
            conn.execute("""
            INSERT INTO earnings_financials (
                symbol, quarter, fiscal_year, result_date,
                revenue_cr, ebitda_cr, ebitda_margin_pct, pat_cr, eps,
                cash_cr, debt_cr, net_debt_cr, source, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol, quarter, fiscal_year) DO NOTHING
            """, (
                symbol,
                q["quarter"], q["fiscal_year"], q.get("result_date"),
                q.get("revenue_cr"), q.get("ebitda_cr"), q.get("ebitda_margin_pct"),
                q.get("pat_cr"), q.get("eps"),
                q.get("cash_cr"), q.get("debt_cr"), q.get("net_debt_cr"),
                q.get("source", "YAHOO"),
                now
            ))
            saved += 1
        except Exception as e:
            print(f"[HISTORY] DB save error ({symbol} {q.get('quarter')}): {e}")

    conn.commit()
    conn.close()
    print(f"[HISTORY] {symbol}: {saved} quarters saved to DB")
    return saved


# ─── MAIN PUBLIC FUNCTION ─────────────────────────────────────────────────────

def get_history(symbol: str, current_quarter: str = None,
                current_fy: str = None, limit: int = 4) -> list:
    """
    Get historical quarters for a symbol.
    Checks DB first. If fewer than 2 prior quarters found, fetches from Yahoo.

    Returns list of dicts (newest first), excluding current quarter if provided.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    exclude_clause = ""
    params         = [symbol]
    if current_quarter and current_fy:
        exclude_clause = "AND NOT (quarter = ? AND fiscal_year = ?)"
        params += [current_quarter, current_fy]

    rows = conn.execute(f"""
        SELECT * FROM earnings_financials
        WHERE symbol = ? {exclude_clause}
        ORDER BY fiscal_year DESC, quarter DESC
        LIMIT {limit}
    """, params).fetchall()
    conn.close()

    db_history = [dict(r) for r in rows]

    # If we have enough history in DB, use it
    if len(db_history) >= 2:
        print(f"[HISTORY] {symbol}: {len(db_history)} quarters from DB cache")
        return db_history

    # Not enough — fetch from Yahoo and save
    print(f"[HISTORY] {symbol}: only {len(db_history)} in DB — fetching from Yahoo Finance...")
    yahoo_data = fetch_from_yahoo(symbol, quarters=limit + 1)

    if yahoo_data:
        _save_history_to_db(symbol, yahoo_data)
        # Filter out current quarter
        if current_quarter and current_fy:
            yahoo_data = [
                q for q in yahoo_data
                if not (q["quarter"] == current_quarter and q["fiscal_year"] == current_fy)
            ]
        return yahoo_data[:limit]

    # Yahoo failed — prior quarter data comes from PDF extraction (see financial_extractor.py)
    # The Gemini PDF extraction now extracts prev_quarter and same_quarter_last_year
    # directly from the comparison table in the result PDF.
    print(f"[HISTORY] {symbol}: Yahoo unavailable — using PDF-extracted comparison data")
    return db_history


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from database import init_db
    init_db()

    sym = sys.argv[1] if len(sys.argv) > 1 else "TITAN"
    print(f"\nFetching history for {sym}...")
    history = get_history(sym)
    for h in history:
        print(f"  {h['quarter']} {h['fiscal_year']} | "
              f"Rev: {h.get('revenue_cr')} Cr | "
              f"PAT: {h.get('pat_cr')} Cr | "
              f"EPS: {h.get('eps')} | "
              f"Source: {h.get('source')}")
