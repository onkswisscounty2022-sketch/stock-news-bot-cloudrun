"""
AI Analyzer — Phase 2C
Takes extracted financials, fetches historical data, and runs:
  1. QoQ + YoY comparison
  2. Gemini narrative analysis (auditor-style observations)
  3. 7-component scoring (0–100 each → weighted Overall Score)
  4. Final classification
  5. Saves to earnings_ai_analysis table

Scoring Components (per spec):
  Financial Score     — revenue/PAT absolute strength
  Growth Score        — QoQ + YoY growth rates
  Quality Score       — auditor flags, consistency, one-off items
  Balance Sheet Score — debt, cash, net debt, book value
  Cash Flow Score     — OCF, FCF, capex efficiency
  Consistency Score   — historical trend across last 8 quarters
  Technical Score     — VCP data (RS, stage, 52w proximity)

Final Classification:
  85+  → High Conviction Candidate
  70+  → Watch for Breakout
  55+  → Positive but Wait
  45+  → Neutral
  30+  → Weak
  <30  → Avoid
"""
import json
import sqlite3
import requests
import re
from datetime import datetime, timedelta
from config import GEMINI_URL, DB_PATH, IST
from vcp_integration import get_stock_technical
from history_fetcher import get_history
from peer_comparison import run_peer_comparison

# ─── SCORING WEIGHTS ──────────────────────────────────────────────────────────
WEIGHTS = {
    "financial":    0.20,
    "growth":       0.25,
    "quality":      0.15,
    "balance_sheet":0.15,
    "cashflow":     0.10,
    "consistency":  0.10,
    "technical":    0.05,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"

# ─── CLASSIFICATION THRESHOLDS ────────────────────────────────────────────────
def _classify(score: float) -> str:
    if score >= 85:
        return "High Conviction Candidate"
    elif score >= 70:
        return "Watch for Breakout"
    elif score >= 55:
        return "Positive but Wait"
    elif score >= 45:
        return "Neutral"
    elif score >= 30:
        return "Weak"
    else:
        return "Avoid"


# ─── FETCH HISTORY ────────────────────────────────────────────────────────────

def _get_history(symbol: str, current_quarter: str, current_fy: str) -> list:
    """
    Fetch up to 4 prior quarters for comparison.
    Uses history_fetcher which checks DB first, falls back to Yahoo Finance.
    """
    return get_history(symbol, current_quarter=current_quarter,
                       current_fy=current_fy, limit=4)


def _get_current_financials(symbol: str, quarter: str, fiscal_year: str) -> dict | None:
    """Fetch current quarter's financials from DB."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT * FROM earnings_financials
        WHERE symbol = ? AND quarter = ? AND fiscal_year = ?
    """, (symbol, quarter, fiscal_year)).fetchone()
    conn.close()
    return dict(row) if row else None


# ─── QoQ / YoY COMPUTATION ───────────────────────────────────────────────────

def _pct_change(current, previous) -> float | None:
    """Compute percentage change, return None if inputs invalid."""
    try:
        if previous and previous != 0:
            return round(((current - previous) / abs(previous)) * 100, 1)
    except (TypeError, ValueError):
        pass
    return None


def _compute_comparisons(current: dict, history: list) -> dict:
    """
    Build QoQ and YoY comparison dict for key metrics.
    QoQ = vs immediate previous quarter
    YoY = vs same quarter last fiscal year
    """
    comparisons = {}

    # Find QoQ (previous quarter)
    qoq_data = history[0] if history else None

    # Find YoY (same quarter, previous FY) — walk history to find exact match
    current_q  = current.get("quarter")
    current_fy = current.get("fiscal_year", "")
    yoy_data   = None
    for h in history:
        if h.get("quarter") == current_q and h.get("fiscal_year") != current_fy:
            # Verify it's actually one year prior not two
            try:
                curr_yr = int(current_fy.replace("FY", ""))
                hist_yr = int(h.get("fiscal_year", "").replace("FY", ""))
                if curr_yr - hist_yr == 1:
                    yoy_data = h
                    break
            except Exception:
                yoy_data = h
                break

    metrics = ["revenue_cr", "ebitda_cr", "ebitda_margin_pct", "pat_cr", "eps"]
    for m in metrics:
        val = current.get(m)
        comparisons[m] = {
            "current": val,
            "qoq_prev": qoq_data.get(m) if qoq_data else None,
            "qoq_pct":  _pct_change(val, qoq_data.get(m) if qoq_data else None),
            "yoy_prev": yoy_data.get(m) if yoy_data else None,
            "yoy_pct":  _pct_change(val, yoy_data.get(m) if yoy_data else None),
        }

    return comparisons


# ─── COMPONENT SCORING ────────────────────────────────────────────────────────

def _score_financial(f: dict) -> float:
    """Score raw financial strength (0–100)."""
    score = 50.0  # Base

    # PAT positive = fundamental requirement
    if f.get("pat_cr") is not None:
        if f["pat_cr"] > 0:
            score += 15
        elif f["pat_cr"] < 0:
            score -= 30

    # EBITDA margin bands
    margin = f.get("ebitda_margin_pct")
    if margin is not None:
        if margin >= 25:
            score += 20
        elif margin >= 15:
            score += 12
        elif margin >= 8:
            score += 5
        elif margin < 0:
            score -= 20

    # Revenue scale (proxy for business size)
    rev = f.get("revenue_cr") or 0
    if rev > 10000:
        score += 10
    elif rev > 1000:
        score += 6
    elif rev > 100:
        score += 3

    return max(0, min(100, score))


def _score_growth(comparisons: dict) -> float:
    """Score growth trajectory (0–100). Returns 40 (below neutral) when no data."""
    rev_yoy = comparisons.get("revenue_cr", {}).get("yoy_pct")
    pat_yoy = comparisons.get("pat_cr", {}).get("yoy_pct")
    rev_qoq = comparisons.get("revenue_cr", {}).get("qoq_pct")
    pat_qoq = comparisons.get("pat_cr", {}).get("qoq_pct")

    # No data at all → 40 (slight negative — unknown is cautious not neutral)
    if all(v is None for v in [rev_yoy, pat_yoy, rev_qoq, pat_qoq]):
        return 40.0

    score = 50.0

    def _add_growth(val, high_th, med_th, neg_th):
        if val is None:
            return 0
        if val >= high_th:
            return 20
        elif val >= med_th:
            return 10
        elif val >= 0:
            return 3
        elif val >= neg_th:
            return -10
        else:
            return -20

    score += _add_growth(pat_yoy, 30, 10, -10)
    score += _add_growth(rev_yoy, 20, 10,  -5)
    score += _add_growth(pat_qoq, 15,  5, -10) * 0.5
    score += _add_growth(rev_qoq, 10,  5,  -5) * 0.5

    return max(0, min(100, score))


def _score_balance_sheet(f: dict) -> float:
    """
    Score balance sheet health (0–100).
    Bank-aware: banks structurally have high debt (deposits).
    Detect banks by revenue scale + cash ratio.
    """
    score    = 50.0
    net_debt = f.get("net_debt_cr")
    cash     = f.get("cash_cr")
    debt     = f.get("debt_cr")
    pat      = f.get("pat_cr") or 1
    revenue  = f.get("revenue_cr") or 0

    # Detect if this is a bank/NBFC — cash > revenue is a bank signature
    is_bank = cash and revenue and cash > revenue * 0.5

    if is_bank:
        # For banks: score on PAT growth and NIM proxy instead of debt ratio
        # High cash + high debt is normal — use cash/debt ratio instead
        if cash and debt and debt > 0:
            ratio = cash / debt
            if ratio > 0.5:   score += 20
            elif ratio > 0.3: score += 10
            elif ratio > 0.1: score += 0
            else:             score -= 10
    else:
        # Non-bank: standard net debt scoring
        if net_debt is not None:
            if net_debt < 0:          # net cash
                score += 25
            elif net_debt == 0:
                score += 10
            else:
                ratio = net_debt / max(abs(pat), 1)
                if ratio < 1:   score += 5
                elif ratio < 3: score -= 5
                elif ratio < 5: score -= 15
                else:           score -= 25

    if cash and cash > 100:
        score += 5

    return max(0, min(100, score))


def _score_cashflow(f: dict) -> float:
    """Score cash flow quality (0–100)."""
    score = 50.0

    ocf  = f.get("operating_cf_cr")
    fcf  = f.get("free_cf_cr")
    pat  = f.get("pat_cr") or 0

    if ocf is not None:
        if ocf > 0:
            score += 15
            # OCF > PAT = high quality earnings
            if pat > 0 and ocf > pat:
                score += 10
        else:
            score -= 20

    if fcf is not None:
        if fcf > 0:
            score += 10
        else:
            score -= 10

    return max(0, min(100, score))


def _score_consistency(history: list, current: dict) -> float:
    """Score historical consistency across quarters (0–100)."""
    if not history:
        return 50.0  # Neutral if no history

    score = 50.0
    pat_vals = [h.get("pat_cr") for h in history if h.get("pat_cr") is not None]
    rev_vals = [h.get("revenue_cr") for h in history if h.get("revenue_cr") is not None]

    # Count quarters with positive PAT
    positive_qtrs = sum(1 for v in pat_vals if v > 0)
    total = len(pat_vals) or 1
    score += (positive_qtrs / total - 0.5) * 40

    # Revenue trend (simple — is last > first?)
    if len(rev_vals) >= 3:
        recent_avg = sum(rev_vals[:3]) / 3
        older_avg  = sum(rev_vals[-3:]) / 3
        if older_avg > 0 and recent_avg > older_avg:
            score += 10
        elif recent_avg < older_avg * 0.85:
            score -= 10

    return max(0, min(100, score))


def _score_technical(symbol: str) -> float:
    """Score VCP technical alignment (0–100)."""
    tech = get_stock_technical(symbol)
    if not tech:
        return 50.0  # Neutral if VCP data unavailable

    score = 50.0

    rs = tech.get("rs")
    if rs is not None:
        if rs >= 90:
            score += 25
        elif rs >= 75:
            score += 15
        elif rs >= 50:
            score += 5
        else:
            score -= 10

    stage2 = tech.get("stage2")
    if stage2:
        score += 15

    vcp_pass = tech.get("vcp_pass")
    if vcp_pass:
        score += 10

    return max(0, min(100, score))


def _score_quality(f: dict, ai_response: dict) -> float:
    """
    Score earnings quality based on Gemini's qualitative observations.
    Looks for red flags in the AI narrative.
    """
    score = 65.0  # Start slightly above neutral

    notes = (ai_response.get("auditor_observations") or "").lower()
    text  = (ai_response.get("executive_summary") or "").lower()
    combined = notes + " " + text

    # Red flags → deduct
    red_flags = [
        "exceptional item", "one-off", "one time", "write-off", "write off",
        "impairment", "restatement", "qualified opinion", "emphasis of matter",
        "going concern", "litigation", "contingent liabilit", "revenue recognition",
        "related party", "promoter pledge",
    ]
    for flag in red_flags:
        if flag in combined:
            score -= 8

    # Green flags → add
    green_flags = [
        "consistent growth", "debt free", "cash rich", "strong margin",
        "order book", "capacity expansion", "unaudited clean", "clean audit",
        "dividend declared", "buyback",
    ]
    for flag in green_flags:
        if flag in combined:
            score += 5

    return max(0, min(100, score))


# ─── CONCALL FETCHER ─────────────────────────────────────────────────────────

BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
    "Accept":  "application/json, */*",
    "Referer": "https://www.bseindia.com/",
}

def fetch_concall_details(scrip_cd: str, symbol: str) -> dict:
    """
    Search BSE announcements for concall/investor meet details.
    Looks for: 'Investor Call', 'Concall', 'Earnings Call', 'Conference Call'
    Returns dict with date, time, dial_in, webcast_url, or status='not_announced'
    """
    if not scrip_cd:
        return {"status": "not_announced", "message": "Concall not announced yet"}

    end_date   = datetime.now(IST)
    start_date = end_date - timedelta(days=7)   # 7 days back
    look_ahead = end_date + timedelta(days=45)  # 45 days forward

    url = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
    params = {
        "strCat":      "AGM/EGM",
        "strPrevDate": start_date.strftime("%Y%m%d"),
        "strScrip":    scrip_cd,
        "strSearch":   "P",
        "strToDate":   look_ahead.strftime("%Y%m%d"),
        "strType":     "C",
        "subcategory": "-1",
    }

    concall_keywords = [
        "investor call", "concall", "earnings call",
        "conference call", "analyst call", "investor meet",
        "analyst meet", "earnings conference"
    ]

    try:
        # Try Result category first (concall often filed with result)
        for cat in ["Result", "AGM/EGM", "General"]:
            params["strCat"] = cat
            resp = requests.get(url, params=params, headers=BSE_HEADERS, timeout=12)
            if resp.status_code != 200:
                continue

            data = resp.json()
            rows = data.get("Table", data.get("Table1", []))
            if not rows:
                continue

            for row in rows:
                headline = (row.get("HEADLINE", "") + " " + row.get("CATEGORYNAME", "")).lower()
                if any(kw in headline for kw in concall_keywords):
                    # Try to extract time from headline
                    time_match = re.search(r'(\d{1,2}[:\.]\d{2}\s*(?:am|pm|AM|PM|IST)?)', row.get("HEADLINE", ""))
                    date_str   = row.get("NEWS_DT", "")

                    return {
                        "status":    "announced",
                        "date":      date_str[:10] if date_str else "TBD",
                        "time":      time_match.group(1) if time_match else "Time TBD",
                        "headline":  row.get("HEADLINE", ""),
                        "dial_in":   "Check BSE filing for dial-in details",
                        "bse_url":   f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{row.get('ATTACHMENTNAME','')}",
                        "message":   f"Concall scheduled — {row.get('HEADLINE','')[:80]}",
                    }

    except Exception as e:
        print(f"[CONCALL] Fetch error for {symbol}: {e}")

    return {"status": "not_announced", "message": "Concall not yet announced on BSE"}


# ─── GROWTH ANALYSIS BLOCK ───────────────────────────────────────────────────

def build_growth_analysis(current: dict, history: list, comparisons: dict) -> dict:
    """
    Build a structured growth analysis with:
    - Per-metric trend across available quarters
    - QoQ and YoY verdict per metric
    - Overall growth verdict
    """
    def _trend(vals):
        """
        Trend computed on newest-first series [current, q-1, q-2].
        changes[0] = current - prev = positive if current > prev (growing now)
        changes[1] = prev - older  = positive if prev > older (was growing before)
        """
        clean = [v for v in vals if v is not None]
        if len(clean) < 2:
            return "Insufficient Data"
        # changes: positive = growing from older to newer
        changes = [clean[i] - clean[i+1] for i in range(len(clean)-1)]
        most_recent = changes[0]   # current vs prev
        if all(c > 0 for c in changes):
            # Growing in all periods — is it accelerating or steady?
            return "Accelerating ↑↑" if len(changes) >= 2 and abs(changes[0]) > abs(changes[-1]) else "Consistently Growing ↑"
        elif all(c < 0 for c in changes):
            return "Consistently Declining ↓↓"
        elif most_recent > 0:
            return "Recovering ↑"   # latest period growing even if prior wasn't
        elif most_recent < 0:
            return "Decelerating ↓" # latest period declining even if prior grew
        return "Stable →"

    def _verdict_line(metric_name, qoq_pct, yoy_pct, trend):
        parts = []
        if yoy_pct is not None:
            arrow = "▲" if yoy_pct >= 0 else "▼"
            strength = "Strong" if abs(yoy_pct) > 20 else ("Moderate" if abs(yoy_pct) > 8 else "Mild")
            parts.append(f"{strength} {'growth' if yoy_pct >= 0 else 'decline'} YoY ({arrow}{abs(yoy_pct):.1f}%)")
        if qoq_pct is not None:
            arrow = "▲" if qoq_pct >= 0 else "▼"
            parts.append(f"{'improved' if qoq_pct >= 0 else 'slipped'} QoQ ({arrow}{abs(qoq_pct):.1f}%)")
        parts.append(f"Trend: {trend}")
        return f"{metric_name}: " + " | ".join(parts)

    # Build value series: [current, q-1, q-2, q-3]
    rev_series = [current.get("revenue_cr")] + [h.get("revenue_cr") for h in history[:3]]
    pat_series = [current.get("pat_cr")]     + [h.get("pat_cr")     for h in history[:3]]
    eps_series = [current.get("eps")]        + [h.get("eps")        for h in history[:3]]
    mar_series = [current.get("ebitda_margin_pct")] + [h.get("ebitda_margin_pct") for h in history[:3]]

    rev_trend = _trend(rev_series)
    pat_trend = _trend(pat_series)
    eps_trend = _trend(eps_series)
    mar_trend = _trend(mar_series)

    c = comparisons
    analysis = {
        "revenue": _verdict_line("Revenue",
            c.get("revenue_cr", {}).get("qoq_pct"),
            c.get("revenue_cr", {}).get("yoy_pct"), rev_trend),
        "pat": _verdict_line("PAT",
            c.get("pat_cr", {}).get("qoq_pct"),
            c.get("pat_cr", {}).get("yoy_pct"), pat_trend),
        "eps": _verdict_line("EPS",
            c.get("eps", {}).get("qoq_pct"),
            c.get("eps", {}).get("yoy_pct"), eps_trend),
        "margin": _verdict_line("EBITDA Margin",
            c.get("ebitda_margin_pct", {}).get("qoq_pct"),
            c.get("ebitda_margin_pct", {}).get("yoy_pct"), mar_trend),
    }

    # Overall verdict
    yoy_pats  = [c.get("pat_cr", {}).get("yoy_pct"), c.get("revenue_cr", {}).get("yoy_pct")]
    yoy_valid = [v for v in yoy_pats if v is not None]
    if yoy_valid:
        avg_yoy = sum(yoy_valid) / len(yoy_valid)
        if avg_yoy > 20 and pat_trend in ("Accelerating ↑↑", "Consistently Growing ↑"):
            overall = "STRONG GROWTH — Momentum building, fundamentals improving"
        elif avg_yoy > 10:
            overall = "GROWTH INTACT — Steady performance, watching for acceleration"
        elif avg_yoy > 0:
            overall = "MILD GROWTH — Revenue and profits growing but pace is slow"
        elif avg_yoy > -10:
            overall = "FLAT / WEAK — Growth stalling, monitor next quarter closely"
        else:
            overall = "DECLINING — Business under pressure, caution warranted"
    else:
        overall = "INSUFFICIENT DATA — First quarter in system"

    analysis["overall_verdict"] = overall
    return analysis

NARRATIVE_PROMPT = """You are a senior equity research analyst at an institutional fund.
Analyze the following quarterly earnings data for {company_name} ({symbol}) and produce a structured assessment.

CURRENT QUARTER: {quarter} {fiscal_year}
FINANCIALS:
{financials_json}

QoQ / YoY COMPARISON:
{comparisons_json}

HISTORICAL TREND (last {hist_count} quarters):
{history_json}

VCP TECHNICAL DATA:
{technical_json}

Respond in EXACTLY this JSON structure (no markdown, no extra text):
{{
  "executive_summary": "3-4 sentence plain English summary of this quarter's results",
  "auditor_observations": "2-3 sentence assessment of earnings quality, any red flags, accounting changes, or exceptional items",
  "qoq_analysis": "2 sentences on sequential (QoQ) performance",
  "yoy_analysis": "2 sentences on year-over-year performance",
  "bullish_factors": ["factor1", "factor2", "factor3"],
  "bearish_factors": ["factor1", "factor2"],
  "trading_notes": "2 sentence actionable observation for swing/positional traders",
  "marathi_summary": "2 sentence summary in Marathi language for WhatsApp sharing"
}}"""


def _call_gemini_narrative(symbol: str, company_name: str, quarter: str,
                            fiscal_year: str, financials: dict,
                            comparisons: dict, history: list,
                            technical: dict) -> dict:
    """Call Gemini for qualitative narrative analysis."""

    prompt = NARRATIVE_PROMPT.format(
        company_name    = company_name,
        symbol          = symbol,
        quarter         = quarter,
        fiscal_year     = fiscal_year,
        financials_json = json.dumps(financials, indent=2),
        comparisons_json= json.dumps(comparisons, indent=2),
        hist_count      = len(history),
        history_json    = json.dumps(history[:2], indent=2),  # Limit to 2 quarters to save tokens
        technical_json  = json.dumps(technical, indent=2),
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature":      0.3,
            "maxOutputTokens":  4096,  # Increased from 2048
            "responseMimeType": "application/json"
        }
    }

    raw_text = ""
    try:
        resp = requests.post(GEMINI_URL, json=payload, timeout=90)
        if resp.status_code != 200:
            print(f"[ANALYZER] Gemini narrative HTTP {resp.status_code}")
            return {}

        data       = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return {}

        raw_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```\s*$", "", raw_text.strip()).strip()

        # Clean parse first
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, list):
                parsed = parsed[0] if parsed else {}
            return parsed
        except json.JSONDecodeError:
            pass

        # Truncated — repair string fields via regex
        print("[ANALYZER] Narrative JSON truncated — attempting repair...")
        repaired = {}

        # Extract string fields
        for m in re.finditer(r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*)"', raw_text):
            repaired[m.group(1)] = m.group(2).replace('\\"', '"')

        # Extract array fields like bullish_factors, bearish_factors
        for m in re.finditer(r'"(\w+)"\s*:\s*\[(.*?)\]', raw_text, re.DOTALL):
            key = m.group(1)
            try:
                items = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2))
                if items:
                    repaired[key] = items
            except Exception:
                pass

        if repaired:
            print(f"[ANALYZER] Narrative repaired {len(repaired)} fields")
            # Ensure required keys exist with fallbacks
            repaired.setdefault("executive_summary", repaired.get("summary", "Analysis data extracted."))
            repaired.setdefault("bullish_factors", [])
            repaired.setdefault("bearish_factors", [])
            return repaired

        return {}

    except Exception as e:
        print(f"[ANALYZER] Gemini narrative failed: {e}")
        if raw_text:
            print(f"[ANALYZER] Partial: {raw_text[:100]}")
        return {}


# ─── SAVE ANALYSIS TO DB ──────────────────────────────────────────────────────

def _save_analysis(symbol: str, quarter: str, fiscal_year: str,
                   narrative: dict, scores: dict, classification: str,
                   overall_score: float, growth_analysis: dict = None,
                   peer_comparison: dict = None, concall: dict = None):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
        INSERT INTO earnings_ai_analysis (
            symbol, quarter, fiscal_year,
            executive_summary, auditor_observations, qoq_analysis, yoy_analysis,
            bullish_factors, bearish_factors, trading_notes,
            financial_score, growth_score, quality_score,
            balance_sheet_score, cashflow_score, consistency_score, technical_score,
            overall_score, classification, marathi_summary
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol, quarter, fiscal_year) DO UPDATE SET
            executive_summary    = excluded.executive_summary,
            auditor_observations = excluded.auditor_observations,
            qoq_analysis         = excluded.qoq_analysis,
            yoy_analysis         = excluded.yoy_analysis,
            bullish_factors      = excluded.bullish_factors,
            bearish_factors      = excluded.bearish_factors,
            trading_notes        = excluded.trading_notes,
            financial_score      = excluded.financial_score,
            growth_score         = excluded.growth_score,
            quality_score        = excluded.quality_score,
            balance_sheet_score  = excluded.balance_sheet_score,
            cashflow_score       = excluded.cashflow_score,
            consistency_score    = excluded.consistency_score,
            technical_score      = excluded.technical_score,
            overall_score        = excluded.overall_score,
            classification       = excluded.classification,
            marathi_summary      = excluded.marathi_summary
        """, (
            symbol, quarter, fiscal_year,
            narrative.get("executive_summary"),
            narrative.get("auditor_observations"),
            narrative.get("qoq_analysis"),
            narrative.get("yoy_analysis"),
            json.dumps(narrative.get("bullish_factors", [])),
            json.dumps(narrative.get("bearish_factors", [])),
            narrative.get("trading_notes"),
            scores["financial"],
            scores["growth"],
            scores["quality"],
            scores["balance_sheet"],
            scores["cashflow"],
            scores["consistency"],
            scores["technical"],
            overall_score,
            classification,
            narrative.get("marathi_summary"),
        ))
        conn.commit()
        print(f"[ANALYZER] {symbol} {quarter} {fiscal_year}: analysis saved ✓")
        return True
    except Exception as e:
        print(f"[ANALYZER] DB save error: {e}")
        return False
    finally:
        conn.close()


# ─── MAIN PUBLIC FUNCTION ─────────────────────────────────────────────────────

def analyze(item: dict) -> dict:
    """
    Run full AI analysis pipeline for one detected result.

    Expects item to have: symbol, company_name, quarter, fiscal_year,
                          financials (dict), tier
                          bse_scrip_cd (for concall lookup)

    Returns item enriched with:
        analysis_status  — 'analyzed' | 'failed' | 'no_financials'
        overall_score    — 0–100
        classification   — final label
        narrative        — full narrative dict
        growth_analysis  — per-metric trend + overall verdict
        peer_comparison  — peer ranking table
        concall          — concall details or not_announced
    """
    symbol       = item.get("symbol", "")
    company_name = item.get("company_name", symbol)
    quarter      = item.get("quarter")
    fiscal_year  = item.get("fiscal_year")
    financials   = item.get("financials") or {}
    scrip_cd     = item.get("bse_scrip_cd", "")
    pnl_comp     = item.get("pnl_comparison", {})

    print(f"\n[ANALYZER] {company_name} ({symbol}) {quarter} {fiscal_year} — analyzing...")

    if not financials or not quarter or not fiscal_year:
        print(f"[ANALYZER] {symbol}: no financials to analyze")
        item["analysis_status"] = "no_financials"
        return item

    # 1. Fetch history (DB first → Yahoo Finance fallback)
    current = _get_current_financials(symbol, quarter, fiscal_year) or financials
    history = _get_history(symbol, quarter, fiscal_year)

    # 2. Compute QoQ / YoY
    comparisons = _compute_comparisons(current, history)

    # 3. Get VCP technical data
    technical = get_stock_technical(symbol)

    # 4. Build growth analysis
    growth_analysis = build_growth_analysis(current, history, comparisons)

    # 5. Run peer comparison
    print(f"[ANALYZER] Running peer comparison...")
    peer_data = run_peer_comparison(symbol, quarter, fiscal_year, financials, history)

    # 6. Fetch concall details from BSE
    print(f"[ANALYZER] Checking BSE for concall announcement...")
    concall = fetch_concall_details(scrip_cd, symbol)
    print(f"[ANALYZER] Concall: {concall.get('status')} — {concall.get('message','')[:60]}")

    # 7. Run Gemini narrative — skip if summary already extracted from PDF
    narrative = item.get("summary") or {}
    if not narrative:
        narrative = _call_gemini_narrative(
            symbol, company_name, quarter, fiscal_year,
            financials, comparisons, history, technical
        )
    if not narrative:
        print(f"[ANALYZER] {symbol}: narrative unavailable — scoring without")
        narrative = {}

    # 8. Score each component
    scores = {
        "financial":    _score_financial(current),
        "growth":       _score_growth(comparisons),
        "quality":      _score_quality(current, narrative),
        "balance_sheet":_score_balance_sheet(current),
        "cashflow":     _score_cashflow(current),
        "consistency":  _score_consistency(history, current),
        "technical":    _score_technical(symbol),
    }

    # 9. Weighted overall score
    overall_score = sum(scores[k] * WEIGHTS[k] for k in scores)
    overall_score = round(overall_score, 1)

    # 10. Classify
    classification = _classify(overall_score)

    # 11. Save to DB
    saved = _save_analysis(symbol, quarter, fiscal_year,
                           narrative, scores, classification, overall_score,
                           growth_analysis, peer_data, concall)

    # 12. Enrich item
    item["analysis_status"] = "analyzed" if saved else "failed"
    item["overall_score"]   = overall_score
    item["classification"]  = classification
    item["narrative"]       = narrative
    item["scores"]          = scores
    item["growth_analysis"] = growth_analysis
    item["peer_comparison"] = peer_data
    item["concall"]         = concall
    item["comparisons"]     = comparisons
    item["history"]         = history

    print(f"[ANALYZER] {symbol}: Score={overall_score}/100 → {classification}")
    print(f"[ANALYZER] Components: " +
          " | ".join(f"{k[:3].upper()}={v:.0f}" for k, v in scores.items()))

    return item


def analyze_batch(items: list) -> list:
    """
    Run AI analysis on a batch of extracted results.
    Only processes items where extraction_status == 'extracted'.
    """
    enriched = []
    for item in items:
        if item.get("extraction_status") == "extracted":
            item = analyze(item)
        else:
            item["analysis_status"] = "skipped"
        enriched.append(item)

    # Summary
    analyzed = sum(1 for i in enriched if i.get("analysis_status") == "analyzed")
    failed   = sum(1 for i in enriched if i.get("analysis_status") == "failed")
    skipped  = sum(1 for i in enriched if i.get("analysis_status") == "skipped")
    print(f"\n[ANALYZER] Batch done — analyzed:{analyzed} failed:{failed} skipped:{skipped}")
    return enriched


if __name__ == "__main__":
    """Test analyzer against a symbol already in earnings_financials."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from database import init_db
    init_db()

    # Pull most recent extraction from DB
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM earnings_financials ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if not row:
        print("No extracted financials in DB yet. Run financial_extractor.py first.")
        sys.exit(0)

    row = dict(row)
    test_item = {
        "symbol":            row["symbol"],
        "company_name":      row["symbol"],
        "quarter":           row["quarter"],
        "fiscal_year":       row["fiscal_year"],
        "tier":              "B",
        "extraction_status": "extracted",
        "financials": {k: row[k] for k in [
            "revenue_cr", "ebitda_cr", "ebitda_margin_pct", "pat_cr", "eps",
            "cash_cr", "debt_cr", "net_debt_cr", "operating_cf_cr",
            "free_cf_cr", "capex_cr", "dividend_per_share", "book_value",
            "order_book_cr", "employee_count", "guidance_text"
        ]},
    }

    result = analyze(test_item)
    print(f"\nOverall Score : {result.get('overall_score')}/100")
    print(f"Classification: {result.get('classification')}")
    if result.get("narrative"):
        print(f"Summary       : {result['narrative'].get('executive_summary', '')[:200]}")
