"""
On-Demand Company Lookup
Usage:
    python3 lookup.py TIMEX
    python3 lookup.py "rail vikas"
    python3 lookup.py 542649
"""
import sys
import os
import json
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import init_db
from symbol_resolver import ensure_cache_loaded, resolve, search
from pdf_downloader import download_pdf
from financial_extractor import extract_financials
from ai_analyzer import analyze
from config import IST, DB_PATH
from datetime import datetime, timedelta

BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
    "Accept":     "application/json, */*",
    "Referer":    "https://www.bseindia.com/",
}


# ─── STEP 1: COMPANY SEARCH ──────────────────────────────────────────────────

def search_company(query: str) -> list:
    query = query.strip()
    if query.isdigit():
        profile = resolve(query)
        return [{"scrip_cd": query, "name": profile["company_name"],
                 "nse_symbol": profile["nse_symbol"], "isin": profile["isin"]}]
    matches = search(query, max_results=5)
    return [{"scrip_cd": p.get("bse_scrip_cd",""), "name": p.get("company_name",""),
             "nse_symbol": p.get("nse_symbol",""), "isin": p.get("isin","")}
            for p in matches]


# ─── STEP 2: FETCH LATEST RESULT FILING ──────────────────────────────────────

def fetch_latest_result(scrip_cd: str) -> dict | None:
    end_date   = datetime.now(IST)
    start_date = end_date - timedelta(days=180)
    url = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
    params = {
        "strCat": "Result", "strPrevDate": start_date.strftime("%Y%m%d"),
        "strScrip": scrip_cd, "strSearch": "P",
        "strToDate": end_date.strftime("%Y%m%d"), "strType": "C", "subcategory": "-1",
    }
    try:
        resp = requests.get(url, params=params, headers=BSE_HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"[LOOKUP] BSE HTTP {resp.status_code}")
            return None
        data = resp.json()
        rows = data.get("Table", data.get("Table1", []))
        if not rows:
            print(f"[LOOKUP] No result filings found for scrip {scrip_cd}")
            return None
        rows.sort(key=lambda x: x.get("NEWS_DT", ""), reverse=True)
        item = rows[0]
        att  = item.get("ATTACHMENTNAME", "")
        all_atts = []
        for r in rows[:3]:
            a = r.get("ATTACHMENTNAME","")
            if a and a not in all_atts: all_atts.append(a)
        pdf_url  = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}" if att else ""
        headline = item.get("HEADLINE","").strip()
        if not headline or headline.lower() in ("please find attached","outcome of board meeting",""):
            headline = item.get("CATEGORYNAME","Financial Result Filing")
        print(f"[LOOKUP] Found filing: {headline[:80]}")
        print(f"[LOOKUP] Date: {item.get('NEWS_DT','')}  |  PDF: {att}")
        return {
            "scrip_cd": scrip_cd, "announcement_id": f"BSE_{item.get('NEWSID','')}",
            "subject": headline, "announcement_date": item.get("NEWS_DT",""),
            "pdf_url": pdf_url, "all_attachments": all_atts,
        }
    except Exception as e:
        print(f"[LOOKUP] Fetch filing error: {e}")
        return None


import re as _re

def _clean_name(name: str) -> str:
    """Remove BSE suffix artifacts like -$, -$ etc from display names."""
    return _re.sub(r'[-\s]*\$\s*$', '', str(name)).strip()


# ─── STEP 3: FORMAT OUTPUT ───────────────────────────────────────────────────

def print_terminal_report(item: dict):
    symbol    = item.get("symbol", "")
    company   = _clean_name(item.get("company_name", symbol))
    quarter   = item.get("quarter", "")
    fy        = item.get("fiscal_year", "")
    f         = item.get("financials", {}) or {}
    scores    = item.get("scores", {}) or {}
    overall   = item.get("overall_score")
    cls       = item.get("classification", "N/A")
    pnl       = item.get("pnl_comparison", {}) or {}
    narrative = item.get("narrative", {}) or {}
    peers     = item.get("peer_comparison", {}) or {}
    concall   = item.get("concall", {}) or {}
    growth    = item.get("growth_analysis", {}) or {}

    # Map narrative to summary sections
    summary = {
        "performance_overview":    narrative.get("executive_summary", ""),
        "growth_quality":          " ".join(filter(None, [narrative.get("qoq_analysis",""), narrative.get("yoy_analysis","")])),
        "auditor_observations":    narrative.get("auditor_observations", ""),
        "balance_sheet_commentary":"",
        "management_outlook":      "",
        "red_flags":               narrative.get("bearish_factors", []),
        "green_flags":             narrative.get("bullish_factors", []),
        "verdict":                 f"{cls} — {narrative.get('trading_notes','')[:60]}" if narrative.get("trading_notes") else cls,
        "trading_note":            narrative.get("trading_notes", ""),
        "whatsapp_summary":        narrative.get("marathi_summary", ""),
    }

    W       = 72
    HR      = "─" * (W - 2)
    now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")

    def _cr(v):
        return f"₹{v:>10,.1f} Cr" if v is not None else "        N/A   "

    def _arrow(v):
        if v is None: return "  N/A  "
        return f"{'▲' if v >= 0 else '▼'} {abs(v):5.1f}%"

    def _bar(v, w=10):
        if v is None: return "░" * w
        return "█" * max(0, min(w, int(v/10))) + "░" * (w - max(0, min(w, int(v/10))))

    def _wrap(text, width=68, indent=2):
        if not text: return ""
        words = str(text).split()
        lines, line = [], " " * indent
        for word in words:
            if len(line) + len(word) + 1 > width:
                lines.append(line)
                line = " " * indent + word + " "
            else:
                line += word + " "
        if line.strip(): lines.append(line)
        return "\n".join(lines)

    def _pct(cv, pv):
        try:
            if cv is not None and pv and pv != 0:
                return round(((cv - pv) / abs(pv)) * 100, 1)
        except: pass
        return None

    # ── Header ────────────────────────────────────────────────────────────────
    print("\n" + "═" * W)
    print(f"  Generated: {now_str}")
    print(f"  📊  {company}  ({symbol})")
    rt = f.get("result_type") or pnl.get("current", {}).get("result_type", "")
    print(f"  {quarter} {fy}  |  {rt or 'Standalone/Consolidated'}")
    print("═" * W)

    # ── Concall ──────────────────────────────────────────────────────────────
    print()
    if concall.get("status") == "announced":
        print(f"  📞 CONCALL SCHEDULED")
        print(f"     Date : {concall.get('date','TBD')}  |  Time: {concall.get('time','TBD')}")
        print(f"     Info : {concall.get('headline','')[:65]}")
    else:
        print(f"  📞 CONCALL  : {concall.get('message','Not announced yet')}")

    # ── Quarterly Comparison Table ────────────────────────────────────────────
    curr_q = pnl.get("current", {}) or {}
    prev_q = pnl.get("prev_quarter", {}) or {}
    yoy_q  = pnl.get("same_qtr_last_year", {}) or {}

    cl = f"{curr_q.get('quarter',quarter)} {curr_q.get('fiscal_year',fy)}"
    pl = f"{prev_q.get('quarter','—')} {prev_q.get('fiscal_year','')}" if prev_q.get("quarter") else "Prior Q"
    yl = f"{yoy_q.get('quarter','—')} {yoy_q.get('fiscal_year','')}"  if yoy_q.get("quarter")  else "Prior Year"

    print(f"\n  {HR}")
    print(f"\n  QUARTERLY COMPARISON")
    print(f"  {'Metric':<18} {cl:>12}  {pl:>12}  {'QoQ':>7}  {yl:>12}  {'YoY':>7}")
    print(f"  {'─'*18} {'─'*12}  {'─'*12}  {'─'*7}  {'─'*12}  {'─'*7}")

    for key, label, fmt, is_margin in [
        ("revenue_cr",        "Revenue",       lambda v: f"₹{v:,.0f} Cr" if v is not None else "N/A", False),
        ("ebitda_cr",         "EBITDA",        lambda v: f"₹{v:,.0f} Cr" if v is not None else "N/A", False),
        ("ebitda_margin_pct", "EBITDA Margin", lambda v: f"{v:.1f}%"     if v is not None else "N/A", True),
        ("pat_cr",            "PAT",           lambda v: f"₹{v:,.0f} Cr" if v is not None else "N/A", False),
        ("eps",               "EPS",           lambda v: f"₹{v:.2f}"     if v is not None else "N/A", False),
    ]:
        cv = curr_q.get(key) or f.get(key)
        pv = prev_q.get(key)
        yv = yoy_q.get(key)
        if is_margin:
            qs = f"{'▲' if (cv or 0)>=(pv or 0) else '▼'} {abs((cv or 0)-(pv or 0)):.1f}pp" if pv is not None and cv is not None else "  N/A  "
            ys = f"{'▲' if (cv or 0)>=(yv or 0) else '▼'} {abs((cv or 0)-(yv or 0)):.1f}pp" if yv is not None and cv is not None else "  N/A  "
        else:
            qs = _arrow(_pct(cv, pv))
            ys = _arrow(_pct(cv, yv))
        print(f"  {label:<18} {fmt(cv):>12}  {fmt(pv):>12}  {qs:>7}  {fmt(yv):>12}  {ys:>7}")

    # ── Intelligence Score ────────────────────────────────────────────────────
    print(f"\n  {HR}")
    if overall is not None:
        print(f"\n  INTELLIGENCE SCORE   [{_bar(overall)}] {overall:.0f}/100  →  {cls}")
    print()
    for key, label in [("financial","Financial Strength"),("growth","Growth"),
                       ("quality","Earnings Quality"),("balance_sheet","Balance Sheet"),
                       ("cashflow","Cash Flow"),("consistency","Consistency"),
                       ("technical","Technical (VCP)")]:
        v = scores.get(key)
        if v is not None:
            print(f"  {label:<22} [{_bar(v)}] {v:.0f}")

    # ── Financials (2-column) ─────────────────────────────────────────────────
    print(f"\n  {HR}")
    print(f"\n  FINANCIALS")
    nd = f.get("net_debt_cr")
    nd_str = "Net Cash ✅" if nd is not None and nd < 0 else (_cr(nd) if nd is not None else "N/A")
    rows_l = [("Revenue",  _cr(f.get("revenue_cr"))),
              ("EBITDA",   _cr(f.get("ebitda_cr"))),
              ("PAT",      _cr(f.get("pat_cr"))),
              ("EPS",      f"₹{f['eps']:.2f}" if f.get("eps") is not None else "N/A"),
              ("Margin",   f"{f.get('ebitda_margin_pct'):.2f}%" if f.get("ebitda_margin_pct") else "N/A"),
              ("Op CF",    _cr(f.get("operating_cf_cr"))),
              ("Free CF",  _cr(f.get("free_cf_cr")))]
    rows_r = [("Cash",     _cr(f.get("cash_cr"))),
              ("Debt",     _cr(f.get("debt_cr"))),
              ("Net Debt", nd_str),
              ("Capex",    _cr(f.get("capex_cr"))),
              ("Div/Share",f"₹{f['dividend_per_share']:.2f}" if f.get("dividend_per_share") else "N/A"),
              ("Book Val", _cr(f.get("book_value"))),
              ("Order Bk", _cr(f.get("order_book_cr")))]
    for (ll, lv), (rl, rv) in zip(rows_l, rows_r):
        print(f"  {ll:<12} {lv:<22}  {rl:<12} {rv}")
    if f.get("operating_cf_cr") is None:
        print(f"  ℹ️  CF data typically in annual report")

    # ── Growth Analysis Table ─────────────────────────────────────────────────
    print(f"\n  {HR}")
    print(f"\n  GROWTH ANALYSIS")

    def _trend_lbl(vals):
        """
        vals = [yoy, prev, current] = oldest to newest.
        changes[0] = prev-yoy, changes[1] = current-prev
        Positive change = growing.
        """
        clean = [v for v in vals if v is not None]
        if len(clean) < 2: return "Insufficient Data"
        changes = [clean[i+1] - clean[i] for i in range(len(clean)-1)]
        most_recent = changes[-1]  # current vs prev
        if all(c > 0 for c in changes):
            return "Accelerating ↑↑" if len(changes) >= 2 and abs(changes[-1]) > abs(changes[0]) else "Consistently Growing ↑"
        elif all(c < 0 for c in changes):
            return "Consistently Declining ↓↓"
        elif most_recent > 0:
            return "Recovering ↑"
        elif most_recent < 0:
            return "Decelerating ↓"
        return "Stable →"

    print(f"  {'Metric':<8} {'Trend':<20} {'Direction (old→new)':<35} Verdict")
    print(f"  {'─'*8} {'─'*20} {'─'*35} {'─'*12}")

    for lbl, key, fmt in [
        ("Revenue", "revenue_cr",        lambda v: f"₹{v:.0f}Cr" if v else "N/A"),
        ("PAT",     "pat_cr",            lambda v: f"₹{v:.0f}Cr" if v else "N/A"),
        ("Margin",  "ebitda_margin_pct", lambda v: f"{v:.1f}%"   if v else "N/A"),
        ("EPS",     "eps",               lambda v: f"₹{v:.1f}"   if v else "N/A"),
    ]:
        yv  = yoy_q.get(key); pv = prev_q.get(key); cv = curr_q.get(key) or f.get(key)
        trend   = _trend_lbl([v for v in [yv, pv, cv] if v is not None])
        dirn    = "→".join([fmt(v) if v is not None else "N/A" for v in [yv, pv, cv]])
        # Verdict: use YoY pct if available
        yoy_p   = _pct(cv, yv) if cv is not None and yv is not None else None
        gk      = {"Revenue":"revenue","PAT":"pat","Margin":"margin","EPS":"eps"}.get(lbl,"")
        ga_text = growth.get(gk, "")
        if yoy_p is not None:
            verdict = f"{'▲' if yoy_p>=0 else '▼'}{abs(yoy_p):.1f}% YoY"
        elif ga_text:
            # extract just the YoY part from the growth text
            import re as _re2
            m = _re2.search(r'YoY \(([▲▼][^)]+)\)', ga_text)
            verdict = m.group(1) if m else ""
        else:
            verdict = ""
        print(f"  {lbl:<8} {trend:<22} {dirn:<35} {verdict[:12]}")

    if growth.get("overall_verdict"):
        print(f"\n  ★ {growth['overall_verdict']}")

    # ── Peer Comparison ───────────────────────────────────────────────────────
    if peers.get("available"):
        print(f"\n  {HR}")
        print(f"\n  PEER COMPARISON  [{peers.get('sector','')}]")
        print(f"  {'Company':<22} {'Rev Gr':>8}  {'PAT Gr':>8}  {'Margin':>8}  {'EPS Gr':>8}  Rank")
        print(f"  {'─'*22} {'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}  ────")
        subj = peers.get("subject", {})
        rnk  = peers.get("rankings", {})
        r1   = rnk.get("pat_growth", {}).get("rank")
        medal= "🥇" if r1==1 else ("🥈" if r1==2 else ("🥉" if r1==3 else ""))
        print(f"  {'★ '+symbol:<22} {_arrow(subj.get('revenue_growth_yoy')):>8}  "
              f"{_arrow(subj.get('pat_growth_yoy')):>8}  "
              f"{str(subj.get('ebitda_margin_pct') or 'N/A')+'%':>8}  "
              f"{_arrow(subj.get('eps_growth_yoy')):>8}  {medal}")
        for p in peers.get("peers_data", []):
            if not p.get("available"):
                print(f"  {p['name']:<22} {'N/A':>8}"); continue
            print(f"  {p['name']:<22} {_arrow(p.get('revenue_growth_yoy')):>8}  "
                  f"{_arrow(p.get('pat_growth_yoy')):>8}  "
                  f"{str(p.get('ebitda_margin_pct') or 'N/A')+'%':>8}  "
                  f"{_arrow(p.get('eps_growth_yoy')):>8}")
        print(f"\n  RANKINGS (1 = best)")
        meaningful = False
        for rk_key, lbl in [("revenue_growth","Revenue Gr"),("pat_growth","PAT Gr"),
                              ("ebitda_margin","Margin"),("eps_growth","EPS Gr")]:
            rk = rnk.get(rk_key, {})
            if rk.get("rank") and rk.get("total") and rk["total"] > 1:
                meaningful = True
                m   = "🥇" if rk["rank"]==1 else ("🥈" if rk["rank"]==2 else ("🥉" if rk["rank"]==3 else "  "))
                val = rk.get("value")
                print(f"  {m} {lbl:<16}: {rk['rank']} of {rk['total']}  "
                      f"({'N/A' if val is None else f'{val:+.1f}%'})")
        if not meaningful:
            print(f"  ℹ️  Peer data builds automatically as more companies are analyzed")
        if peers.get("verdict"):
            print(f"\n  ► {peers['verdict']}")

    # ── Auditor Summary ───────────────────────────────────────────────────────
    print(f"\n  {HR}")
    print(f"\n  📝 AUDITOR SUMMARY")
    for sec_key, sec_label in [
        ("performance_overview",    "PERFORMANCE OVERVIEW"),
        ("growth_quality",          "GROWTH QUALITY"),
        ("auditor_observations",    "AUDITOR OBSERVATIONS"),
        ("balance_sheet_commentary","BALANCE SHEET"),
        ("management_outlook",      "MANAGEMENT OUTLOOK"),
    ]:
        text = summary.get(sec_key, "")
        if text and text.strip():
            print(f"\n  {sec_label}")
            print(_wrap(text))

    # ── Red / Green Flags ─────────────────────────────────────────────────────
    red   = summary.get("red_flags",   [])
    green = summary.get("green_flags", [])
    if isinstance(red,   str):
        try: red   = json.loads(red)
        except: red = []
    if isinstance(green, str):
        try: green = json.loads(green)
        except: green = []
    if red or green:
        print(f"\n  {HR}")
        print(f"\n  RED FLAGS & GREEN FLAGS")
        for g in (green or []): print(f"    ✅ {g}")
        for r in (red   or []): print(f"    ⚠️  {r}")

    # ── Verdict & Trading Note ────────────────────────────────────────────────
    print(f"\n  {HR}")
    if summary.get("verdict"):
        print(f"\n  🎯 VERDICT: {summary['verdict']}")
    if summary.get("trading_note"):
        print(f"\n  TRADING NOTE")
        print(_wrap(summary["trading_note"]))
    if f.get("guidance_text"):
        print(f"\n  📣 MANAGEMENT GUIDANCE")
        print(_wrap(f["guidance_text"][:300]))

    # ── WhatsApp Summary ──────────────────────────────────────────────────────
    if summary.get("whatsapp_summary"):
        print(f"\n  {HR}")
        print(f"\n  🇮🇳 WHATSAPP SUMMARY")
        print(_wrap(summary["whatsapp_summary"]))

    # ── Footer ────────────────────────────────────────────────────────────────
    print(f"\n  {HR}")
    print(f"  Generated: {now_str}  |  {company} ({symbol})  |  Designed by Onkar Ghadge  |  Earnings Intelligence Engine")
    print("═" * W + "\n")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def run_lookup(query: str):
    print(f"\n🔍 Looking up: {query}")
    print("─" * 40)

    init_db()
    ensure_cache_loaded()

    matches = search_company(query)
    if not matches:
        print(f"❌ No company found for '{query}'")
        print("   Try: exact NSE symbol, BSE scrip code, or part of company name")
        return

    if len(matches) > 1:
        print(f"\nFound {len(matches)} matches:")
        for i, m in enumerate(matches):
            print(f"  [{i+1}] {m['name'][:45]:<45} | NSE: {m['nse_symbol']:<12} | BSE: {m['scrip_cd']}")
        print(f"\n  → Using: {matches[0]['name']} (BSE: {matches[0]['scrip_cd']})")
        print("     (Pass BSE scrip code for exact match)")

    company      = matches[0]
    scrip_cd     = company["scrip_cd"]
    nse_symbol   = company["nse_symbol"] or f"BSE:{scrip_cd}"
    company_name = company["name"]

    print(f"\n✅ Company   : {company_name}")
    print(f"   NSE Symbol: {nse_symbol}")
    print(f"   BSE Code  : {scrip_cd}")

    print(f"\n📄 Fetching latest result filing from BSE...")
    filing = fetch_latest_result(scrip_cd)
    if not filing:
        print("❌ No result filing found.")
        return

    print(f"\n⬇️  Downloading PDF...")
    item = {
        "symbol":            nse_symbol,
        "company_name":      company_name,
        "announcement_id":   filing["announcement_id"],
        "announcement_date": filing["announcement_date"],
        "pdf_url":           filing["pdf_url"],
        "bse_scrip_cd":      scrip_cd,
        "tier":              "A",
    }
    item = download_pdf(item)

    # Alternate attachment fallback
    if item.get("download_status") == "failed":
        for att in filing.get("all_attachments", [])[1:]:
            for base in ["AttachLive", "AttachHis"]:
                item["pdf_url"] = f"https://www.bseindia.com/xml-data/corpfiling/{base}/{att}"
                item = download_pdf(item)
                if item.get("download_status") in ("downloaded", "already_exists"):
                    break
            if item.get("download_status") in ("downloaded", "already_exists"):
                break

    if item.get("download_status") not in ("downloaded", "already_exists"):
        print(f"❌ PDF download failed ({item.get('download_status')})")
        return

    print(f"✅ PDF ready: {os.path.basename(item.get('pdf_path',''))}")

    print(f"\n🤖 Extracting financials via Gemini AI...")
    item = extract_financials(item)

    if item.get("extraction_status") != "extracted":
        print(f"❌ Extraction failed — status: {item.get('extraction_status')}")
        return

    print(f"🧠 Running AI analysis and scoring...")
    item = analyze(item)

    print_terminal_report(item)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 lookup.py <company name or symbol or BSE code>")
        print("Examples:")
        print("  python3 lookup.py TIMEX")
        print("  python3 lookup.py RVNL")
        print("  python3 lookup.py \"desi farms\"")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    run_lookup(query)
