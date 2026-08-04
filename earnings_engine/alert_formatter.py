"""
Alert Formatter
Shared formatting logic for Discord and Gmail alerts.
Converts the analyzed item dict into formatted strings/HTML.
"""
import json
import os
import sys
from datetime import datetime
from config import IST

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from env_tag import SOURCE_TAG


def _arrow(v):
    if v is None: return "N/A"
    return f"{'▲' if v >= 0 else '▼'} {abs(v):.1f}%"


def _pct(cv, pv):
    try:
        if cv is not None and pv and pv != 0:
            return round(((cv - pv) / abs(pv)) * 100, 1)
    except Exception:
        pass
    return None


def _cr(v, short=False):
    if v is None: return "N/A"
    if short:
        if abs(v) >= 1000: return f"₹{v/1000:.1f}kCr"
        return f"₹{v:.0f}Cr"
    return f"₹{v:,.1f} Cr"


def _bar(v, w=10):
    if v is None: return "░" * w
    filled = max(0, min(w, int(v / 10)))
    return "█" * filled + "░" * (w - filled)


def _now():
    return datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")


def _safe_list(val):
    if isinstance(val, list): return val
    if isinstance(val, str):
        try: return json.loads(val)
        except: return []
    return []


def _clean_name(name: str) -> str:
    """Strip BSE suffix artifacts like -$ from company names."""
    import re as _re
    return _re.sub(r'[-\s]*\$\s*$', '', str(name)).strip()


# ─── DISCORD PARTS (3 messages) ──────────────────────────────────────────────

def format_discord(item: dict) -> list:
    """
    Returns list of 3 Discord messages (each ≤ 1900 chars).
    Each message starts with: 📊 SYMBOL — Qx FYxx | Part N/3
    """
    symbol    = item.get("symbol", "")
    company   = _clean_name(item.get("company_name", symbol))  # Fix 7: strip $
    quarter   = item.get("quarter", "")
    fy        = item.get("fiscal_year", "")
    f         = item.get("financials", {}) or {}
    scores    = item.get("scores", {}) or {}
    overall   = item.get("overall_score")
    cls       = item.get("classification", "N/A")
    pnl       = item.get("pnl_comparison", {}) or {}
    narrative = item.get("narrative", {}) or {}
    concall   = item.get("concall", {}) or {}
    now_str   = _now()

    curr_q = pnl.get("current", {}) or {}
    prev_q = pnl.get("prev_quarter", {}) or {}
    yoy_q  = pnl.get("same_qtr_last_year", {}) or {}

    # Fix 4 — plain text score, no emoji bar (cleaner for Discord)
    def _score_text(v):
        if v is None: return "N/A"
        return f"{v:.0f}/100"

    header = f"📊 **{symbol}** — {quarter} {fy} | Part {{part}}/3 | {SOURCE_TAG}\n"
    sep    = "─" * 40 + "\n"

    # ── Part 1: Header + Quarterly Comparison ─────────────────────────────────
    cl = f"{curr_q.get('quarter',quarter)} {curr_q.get('fiscal_year',fy)}"
    pl = f"{prev_q.get('quarter','—')} {prev_q.get('fiscal_year','')}" if prev_q.get("quarter") else "Prior Q"
    yl = f"{yoy_q.get('quarter','—')} {yoy_q.get('fiscal_year','')}"  if yoy_q.get("quarter")  else "Prior Year"

    if concall.get("status") == "announced":
        concall_line = f"📞 **CONCALL**: {concall.get('date','TBD')} | {concall.get('time','TBD')}\n"
    else:
        concall_line = f"📞 **CONCALL**: {concall.get('message','Not announced yet')}\n"

    comp_rows = ""
    for key, label, fmt, is_margin in [
        ("revenue_cr",        "Revenue",  lambda v: _cr(v, True) if v is not None else "N/A", False),
        ("ebitda_cr",         "EBITDA",   lambda v: _cr(v, True) if v is not None else "N/A", False),
        ("ebitda_margin_pct", "Margin",   lambda v: f"{v:.1f}%"  if v is not None else "N/A", True),
        ("pat_cr",            "PAT",      lambda v: _cr(v, True) if v is not None else "N/A", False),
        ("eps",               "EPS",      lambda v: f"₹{v:.2f}"  if v is not None else "N/A", False),
    ]:
        cv = curr_q.get(key) or f.get(key)
        pv = prev_q.get(key)
        yv = yoy_q.get(key)
        if is_margin:
            qs = f"{'▲' if (cv or 0)>=(pv or 0) else '▼'}{abs((cv or 0)-(pv or 0)):.1f}pp" if pv is not None and cv is not None else "N/A"
            ys = f"{'▲' if (cv or 0)>=(yv or 0) else '▼'}{abs((cv or 0)-(yv or 0)):.1f}pp" if yv is not None and cv is not None else "N/A"
        else:
            qs = _arrow(_pct(cv, pv))
            ys = _arrow(_pct(cv, yv))
        comp_rows += f"`{label:<8}` {fmt(cv):>9}  {fmt(pv):>9}  {qs:>8}  {fmt(yv):>9}  {ys:>8}\n"

    score_line = f"**SCORE**: `{_score_text(overall)}` → **{cls}**\n"

    part1 = (
        header.format(part=1) +
        f"*{company}* | Generated: {now_str}\n" +
        sep +
        concall_line +
        sep +
        f"**QUARTERLY COMPARISON**\n" +
        f"`{'Metric':<8}` {'Current':>9}  {pl:>9}  {'QoQ':>8}  {yl:>9}  {'YoY':>8}\n" +
        comp_rows +
        sep +
        score_line
    )

    # ── Part 2: Financials + Flags + Verdict ──────────────────────────────────
    nd = f.get("net_debt_cr")
    nd_str = "Net Cash ✅" if nd is not None and nd < 0 else (_cr(nd, True) if nd is not None else "N/A")

    fin_block = (
        f"**FINANCIALS — {symbol} {quarter} {fy}**\n"
        f"`Revenue` {_cr(f.get('revenue_cr'),True):>10}   `Cash`    {_cr(f.get('cash_cr'),True):>10}\n"
        f"`EBITDA ` {_cr(f.get('ebitda_cr'),True):>10}   `Debt`    {_cr(f.get('debt_cr'),True):>10}\n"
        f"`PAT    ` {_cr(f.get('pat_cr'),True):>10}   `Net Debt` {nd_str:>9}\n"
        f"`Op CF  ` {_cr(f.get('operating_cf_cr'),True):>10}   `Capex`   {_cr(f.get('capex_cr'),True):>10}\n"
        f"`Free CF` {_cr(f.get('free_cf_cr'),True):>10}   `Div/Sh`  {'₹'+str(round(f['dividend_per_share'],2)) if f.get('dividend_per_share') else 'N/A':>10}\n"
        f"`EPS    ` {'₹'+str(round(f['eps'],2)) if f.get('eps') is not None else 'N/A':>10}   "
        f"`Margin` {str(round(f.get('ebitda_margin_pct') or 0,1))+'%':>10}\n"
    )

    green = _safe_list(narrative.get("bullish_factors", []))
    red   = _safe_list(narrative.get("bearish_factors", []))

    # Fix 5 — increase truncation limits
    # Fix 2 — 150 chars per flag line
    flags = ""
    for g in green[:3]: flags += f"✅ {g[:150]}\n"
    for r in red[:3]:   flags += f"⚠️ {r[:150]}\n"

    # Fix 5 — verdict gets full 400 chars
    verdict_line = ""
    if narrative.get("trading_notes"):
        verdict_line = f"\n🎯 **VERDICT**: **{cls}**\n{narrative['trading_notes'][:400]}\n"

    # Fix 6 — ensure Part 2 ends with clear separator before Part 3
    part2 = (
        header.format(part=2) +
        sep +
        fin_block +
        sep +
        flags +
        verdict_line +
        sep   # explicit separator at end of Part 2
    )

    # ── Part 3: Auditor Summary + Marathi ─────────────────────────────────────
    # Fix 2 — increase auditor summary to 600 chars, marathi to 400 chars
    summary_text = narrative.get("executive_summary", "")[:600]
    marathi      = narrative.get("marathi_summary", "")[:400]

    # Fix 3 — ensure Part 3 header is clearly separated from Part 2
    part3 = (
        "\n" + header.format(part=3) +
        sep +
        f"**📝 AUDITOR SUMMARY**\n{summary_text}\n" +
        sep +
        f"**🇮🇳 WHATSAPP SUMMARY**\n{marathi}\n" +
        sep +
        f"*Generated: {now_str} | Designed by Onkar Ghadge | Earnings Intelligence Engine*\n"
    )

    return [part1[:1900], part2[:1900], part3[:1900]]


# ─── GMAIL HTML ───────────────────────────────────────────────────────────────

def format_gmail_html(item: dict) -> tuple:
    """
    Returns (subject, html_body) for Gmail.
    Full detailed report as HTML.
    """
    symbol    = item.get("symbol", "")
    company   = _clean_name(item.get("company_name", symbol))  # Fix 7
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
    now_str   = _now()

    curr_q = pnl.get("current", {}) or {}
    prev_q = pnl.get("prev_quarter", {}) or {}
    yoy_q  = pnl.get("same_qtr_last_year", {}) or {}

    # Score color
    score_color = "#27ae60" if (overall or 0) >= 70 else ("#e67e22" if (overall or 0) >= 50 else "#e74c3c")

    subject = f"[{SOURCE_TAG}] 📊 {symbol} {quarter} {fy} — {cls} ({overall:.0f}/100) | Earnings Intelligence Engine"

    # Color helpers
    def _cell_color(v):
        if v is None: return ""
        return 'style="color:#27ae60;font-weight:bold"' if v >= 0 else 'style="color:#e74c3c;font-weight:bold"'

    def _pct_cell(cv, pv, is_margin=False):
        if cv is None or pv is None:
            return "<td>N/A</td>"
        if is_margin:
            diff = round((cv or 0) - (pv or 0), 1)
            direction = "▲" if diff >= 0 else "▼"
            color = "#27ae60" if diff >= 0 else "#e74c3c"
            return f'<td style="color:{color};font-weight:bold">{direction} {abs(diff):.1f}pp</td>'
        p = _pct(cv, pv)
        if p is None: return "<td>N/A</td>"
        color = "#27ae60" if p >= 0 else "#e74c3c"
        arrow = "▲" if p >= 0 else "▼"
        return f'<td style="color:{color};font-weight:bold">{arrow} {abs(p):.1f}%</td>'

    cl = f"{curr_q.get('quarter',quarter)} {curr_q.get('fiscal_year',fy)}"
    pl = f"{prev_q.get('quarter','—')} {prev_q.get('fiscal_year','')}" if prev_q.get("quarter") else "Prior Q"
    yl = f"{yoy_q.get('quarter','—')} {yoy_q.get('fiscal_year','')}"  if yoy_q.get("quarter")  else "Prior Year"

    # Comparison table rows
    comp_rows_html = ""
    for key, label, fmt, is_margin in [
        ("revenue_cr",        "Revenue",       lambda v: _cr(v) if v is not None else "N/A", False),
        ("ebitda_cr",         "EBITDA",        lambda v: _cr(v) if v is not None else "N/A", False),
        ("ebitda_margin_pct", "EBITDA Margin", lambda v: f"{v:.1f}%" if v is not None else "N/A", True),
        ("pat_cr",            "PAT",           lambda v: _cr(v) if v is not None else "N/A", False),
        ("eps",               "EPS",           lambda v: f"₹{v:.2f}" if v is not None else "N/A", False),
    ]:
        cv = curr_q.get(key) or f.get(key)
        pv = prev_q.get(key)
        yv = yoy_q.get(key)
        comp_rows_html += f"""
        <tr>
            <td><b>{label}</b></td>
            <td>{fmt(cv)}</td>
            <td>{fmt(pv)}</td>
            {_pct_cell(cv, pv, is_margin)}
            <td>{fmt(yv)}</td>
            {_pct_cell(cv, yv, is_margin)}
        </tr>"""

    # Score bars
    score_rows = ""
    for key, label in [("financial","Financial Strength"),("growth","Growth"),
                       ("quality","Earnings Quality"),("balance_sheet","Balance Sheet"),
                       ("cashflow","Cash Flow"),("consistency","Consistency"),
                       ("technical","Technical (VCP)")]:
        v = scores.get(key)
        if v is not None:
            bar_color = "#27ae60" if v >= 70 else ("#e67e22" if v >= 50 else "#e74c3c")
            score_rows += f"""
            <tr>
                <td style="padding:3px 8px">{label}</td>
                <td style="padding:3px 8px">
                    <div style="background:#ecf0f1;border-radius:4px;width:200px;height:12px;display:inline-block">
                        <div style="background:{bar_color};width:{v*2}px;height:12px;border-radius:4px"></div>
                    </div>
                    <span style="margin-left:6px">{v:.0f}</span>
                </td>
            </tr>"""

    # Financials table
    nd = f.get("net_debt_cr")
    nd_str = "Net Cash ✅" if nd is not None and nd < 0 else (_cr(nd) if nd is not None else "N/A")
    fin_rows = f"""
        <tr><td>Revenue</td><td>{_cr(f.get('revenue_cr'))}</td><td>Cash</td><td>{_cr(f.get('cash_cr'))}</td></tr>
        <tr><td>EBITDA</td><td>{_cr(f.get('ebitda_cr'))}</td><td>Debt</td><td>{_cr(f.get('debt_cr'))}</td></tr>
        <tr><td>PAT</td><td>{_cr(f.get('pat_cr'))}</td><td>Net Debt</td><td>{nd_str}</td></tr>
        <tr><td>EPS</td><td>{'₹'+str(round(f['eps'],2)) if f.get('eps') is not None else 'N/A'}</td><td>Capex</td><td>{_cr(f.get('capex_cr'))}</td></tr>
        <tr><td>EBITDA Margin</td><td>{f.get('ebitda_margin_pct') or 'N/A'}%</td><td>Div/Share</td><td>{'₹'+str(round(f['dividend_per_share'],2)) if f.get('dividend_per_share') else 'N/A'}</td></tr>
        <tr><td>Op CF</td><td>{_cr(f.get('operating_cf_cr'))}</td><td>Book Val</td><td>{_cr(f.get('book_value'))}</td></tr>
        <tr><td>Free CF</td><td>{_cr(f.get('free_cf_cr'))}</td><td>Order Bk</td><td>{_cr(f.get('order_book_cr'))}</td></tr>"""

    # Flags
    green = _safe_list(narrative.get("bullish_factors", []))
    red   = _safe_list(narrative.get("bearish_factors", []))
    flags_html = ""
    for g in green: flags_html += f'<li style="color:#27ae60">✅ {g}</li>'
    for r in red:   flags_html += f'<li style="color:#e74c3c">⚠️ {r}</li>'

    # Concall
    if concall.get("status") == "announced":
        concall_html = f"""<p>📞 <b>CONCALL SCHEDULED</b><br>
        Date: {concall.get('date','TBD')} | Time: {concall.get('time','TBD')}<br>
        {concall.get('headline','')}</p>"""
    else:
        concall_html = f"<p>📞 <b>CONCALL:</b> {concall.get('message','Not yet announced')}</p>"

    # Peer comparison
    peer_html = ""
    if peers.get("available"):
        peer_rows = ""
        subj = peers.get("subject", {})
        peer_rows += f"""<tr style="background:#eaf4fb">
            <td><b>★ {symbol}</b></td>
            <td>{_arrow(subj.get('revenue_growth_yoy'))}</td>
            <td>{_arrow(subj.get('pat_growth_yoy'))}</td>
            <td>{str(subj.get('ebitda_margin_pct') or 'N/A')}%</td>
            <td>{_arrow(subj.get('eps_growth_yoy'))}</td>
        </tr>"""
        for p in peers.get("peers_data", []):
            if not p.get("available"): continue
            peer_rows += f"""<tr>
                <td>{p['name']}</td>
                <td>{_arrow(p.get('revenue_growth_yoy'))}</td>
                <td>{_arrow(p.get('pat_growth_yoy'))}</td>
                <td>{str(p.get('ebitda_margin_pct') or 'N/A')}%</td>
                <td>{_arrow(p.get('eps_growth_yoy'))}</td>
            </tr>"""
        peer_html = f"""
        <h3 style="color:#2c3e50">Peer Comparison [{peers.get('sector','')}]</h3>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;font-size:13px">
            <tr style="background:#2c3e50;color:white">
                <th>Company</th><th>Rev Gr</th><th>PAT Gr</th><th>Margin</th><th>EPS Gr</th>
            </tr>
            {peer_rows}
        </table>"""

    # Narrative sections
    def _section(title, text):
        if not text or not text.strip(): return ""
        return f'<h4 style="color:#2c3e50;margin-bottom:4px">{title}</h4><p style="margin-top:4px">{text}</p>'

    audit_html = (
        _section("PERFORMANCE OVERVIEW", narrative.get("executive_summary","")) +
        _section("GROWTH QUALITY", (narrative.get("qoq_analysis","") + " " + narrative.get("yoy_analysis","")).strip()) +
        _section("AUDITOR OBSERVATIONS", narrative.get("auditor_observations","")) +
        _section("TRADING NOTE", narrative.get("trading_notes","")) +
        _section("MANAGEMENT GUIDANCE", f.get("guidance_text",""))
    )

    marathi = narrative.get("marathi_summary", "")

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
  body {{ font-family: Arial, sans-serif; font-size: 14px; color: #2c3e50; max-width: 800px; margin: 0 auto; padding: 20px; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 16px; }}
  th {{ background: #2c3e50; color: white; padding: 8px; text-align: left; }}
  td {{ padding: 6px 8px; border: 1px solid #ddd; }}
  tr:nth-child(even) {{ background: #f9f9f9; }}
  h2 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 6px; }}
  h3 {{ color: #2c3e50; }}
  .score-box {{ background: {score_color}; color: white; padding: 10px 20px; border-radius: 8px; display: inline-block; font-size: 18px; font-weight: bold; }}
  .footer {{ color: #7f8c8d; font-size: 12px; border-top: 1px solid #ddd; margin-top: 20px; padding-top: 10px; }}
</style>
</head>
<body>

<p style="color:#7f8c8d;font-size:12px">Generated: {now_str}</p>

<h2>📊 {company} ({symbol}) — {quarter} {fy}</h2>
<p>Result Type: <b>{f.get('result_type','Consolidated')}</b></p>

<div class="score-box">{overall:.0f}/100 — {cls}</div>

{concall_html}

<h3>Quarterly Comparison</h3>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;font-size:13px">
    <tr style="background:#2c3e50;color:white">
        <th>Metric</th><th>{cl}</th><th>{pl}</th><th>QoQ</th><th>{yl}</th><th>YoY</th>
    </tr>
    {comp_rows_html}
</table>

<h3>Intelligence Score</h3>
<table style="border:none;width:auto">
    {score_rows}
</table>

<h3>Financials</h3>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;font-size:13px">
    <tr style="background:#2c3e50;color:white">
        <th>Metric</th><th>Value</th><th>Metric</th><th>Value</th>
    </tr>
    {fin_rows}
</table>

{peer_html}

<h3>📝 Auditor Summary</h3>
{audit_html}

<h3>Red Flags & Green Flags</h3>
<ul style="list-style:none;padding-left:0">
    {flags_html}
</ul>

<h3>🎯 Verdict</h3>
<p><b>{cls}</b></p>

<h3>🇮🇳 WhatsApp Summary</h3>
<p style="background:#ecf0f1;padding:12px;border-radius:6px">{marathi}</p>

<div class="footer">
    Generated: {now_str} | {company} ({symbol}) | {SOURCE_TAG} | Designed by Onkar Ghadge | Earnings Intelligence Engine
</div>

</body>
</html>"""

    return subject, html
