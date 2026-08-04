"""
Financial Extractor — Phase 2B
Single Gemini call extracts current quarter + prior quarters from PDF comparison table.
"""
import os
import json
import base64
import sqlite3
import re
import requests
from datetime import datetime
from config import GEMINI_URL, DB_PATH, IST


EXTRACTION_PROMPT = """You are an expert Indian financial analyst and chartered accountant.
Analyze this quarterly/annual financial result document (BSE filing) and extract the following fields.

RULES:
1. Prefer CONSOLIDATED figures over Standalone. If only Standalone exists, use that.
2. All monetary values in Indian Crores. Convert if needed.
3. If a field is not present, return null.
4. Return ONLY valid JSON — no markdown, no explanation.
5. Quarter format: Q1/Q2/Q3/Q4. Fiscal year format: FY25 (two digits).
6. For EPS use basic EPS.
7. EBITDA = Operating Profit before depreciation and interest.
8. Revenue = Net Sales / Revenue from Operations only (NOT total income).
9. The PDF comparison table has 3 columns: current quarter, previous quarter, same quarter last year. Extract all three.

Return this JSON (fields are ordered by priority — most important first):
{
  "quarter": "Q4",
  "fiscal_year": "FY26",
  "result_type": "Consolidated",
  "period_months": 3,
  "revenue_cr": null,
  "pat_cr": null,
  "eps": null,
  "ebitda_cr": null,
  "ebitda_margin_pct": null,
  "prev_quarter": "Q3",
  "prev_fy": "FY26",
  "prev_revenue_cr": null,
  "prev_pat_cr": null,
  "prev_eps": null,
  "prev_ebitda_cr": null,
  "prev_ebitda_margin_pct": null,
  "yoy_quarter": "Q4",
  "yoy_fy": "FY25",
  "yoy_revenue_cr": null,
  "yoy_pat_cr": null,
  "yoy_eps": null,
  "yoy_ebitda_cr": null,
  "yoy_ebitda_margin_pct": null,
  "cash_cr": null,
  "debt_cr": null,
  "net_debt_cr": null,
  "operating_cf_cr": null,
  "free_cf_cr": null,
  "capex_cr": null,
  "dividend_per_share": null,
  "book_value": null,
  "order_book_cr": null,
  "employee_count": null,
  "guidance_text": null,
  "result_notes": null
}"""


def _pdf_to_base64(pdf_path: str) -> str:
    with open(pdf_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _call_gemini(pdf_path: str) -> dict:
    try:
        pdf_b64 = _pdf_to_base64(pdf_path)
    except Exception as e:
        print(f"[EXTRACTOR] Cannot read PDF: {e}")
        return {}

    payload = {
        "contents": [{
            "parts": [
                {"text": EXTRACTION_PROMPT},
                {"inline_data": {"mime_type": "application/pdf", "data": pdf_b64}}
            ]
        }],
        "generationConfig": {
            "temperature":      0.1,
            "maxOutputTokens":  8192,
            "responseMimeType": "application/json"
        }
    }

    raw_text = ""
    try:
        resp = requests.post(GEMINI_URL, json=payload, timeout=120)
        if resp.status_code != 200:
            print(f"[EXTRACTOR] Gemini HTTP {resp.status_code}: {resp.text[:200]}")
            return {}

        data      = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return {}

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            return {}

        raw_text = parts[0].get("text", "").strip()
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```\s*$", "", raw_text.strip()).strip()

        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, list):
                parsed = parsed[0] if parsed else {}
            return parsed
        except json.JSONDecodeError:
            pass

        # Repair truncated JSON
        print("[EXTRACTOR] JSON truncated — attempting field repair...")
        repaired = {}
        for m in re.finditer(r'"(\w+)"\s*:\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?=[,}\s\n])', raw_text):
            try:
                v = m.group(2)
                repaired[m.group(1)] = float(v) if '.' in v or 'e' in v.lower() else int(v)
            except Exception:
                pass
        for m in re.finditer(r'"(\w+)"\s*:\s*"([^"]*)"', raw_text):
            repaired[m.group(1)] = m.group(2)
        for m in re.finditer(r'"(\w+)"\s*:\s*(null|true|false)(?=[,}\s\n])', raw_text):
            repaired[m.group(1)] = {"null": None, "true": True, "false": False}[m.group(2)]

        if repaired:
            print(f"[EXTRACTOR] Repaired {len(repaired)} fields")
            return repaired

        return {}

    except Exception as e:
        print(f"[EXTRACTOR] Gemini call failed: {e}")
        return {}


def _infer_quarter_from_date(result_date: str):
    try:
        dt    = datetime.strptime(result_date[:10], "%Y-%m-%d")
        month = dt.month
        year  = dt.year
        if month in (7, 8):    return "Q1", f"FY{str(year+1)[2:]}"
        elif month in (10,11): return "Q2", f"FY{str(year+1)[2:]}"
        elif month in (1, 2):  return "Q3", f"FY{str(year)[2:]}"
        elif month in (4, 5):  return "Q4", f"FY{str(year)[2:]}"
        else:                  return None, None
    except Exception:
        return None, None


def _normalise_fy(fy_raw) -> str | None:
    if not fy_raw: return None
    s = str(fy_raw).strip().upper()
    if re.match(r'^FY\d{2}$', s): return s
    m = re.match(r'^FY(\d{4})$', s)
    if m: return f"FY{m.group(1)[2:]}"
    m = re.match(r'^(\d{4})$', s)
    if m: return f"FY{m.group(1)[2:]}"
    m = re.match(r'^\d{2,4}-(\d{2})$', s)
    if m: return f"FY{m.group(1)}"
    return s


def _clean(val):
    if val is None: return None
    try: return float(val)
    except: return None


def _clean_int(val):
    if val is None: return None
    try: return int(val)
    except: return None


def _validate(extracted: dict, symbol: str, result_date: str) -> dict:
    q_inf, fy_inf = _infer_quarter_from_date(result_date)
    rev    = _clean(extracted.get("revenue_cr"))
    ebitda = _clean(extracted.get("ebitda_cr"))
    pat    = _clean(extracted.get("pat_cr"))
    cash   = _clean(extracted.get("cash_cr"))
    debt   = _clean(extracted.get("debt_cr"))
    nd     = _clean(extracted.get("net_debt_cr"))
    margin = _clean(extracted.get("ebitda_margin_pct"))

    if nd is None and debt is not None and cash is not None:
        nd = round(debt - cash, 2)
    if margin is None and ebitda is not None and rev and rev > 0:
        margin = round(ebitda / rev * 100, 1)

    return {
        "quarter":            extracted.get("quarter") or q_inf,
        "fiscal_year":        _normalise_fy(extracted.get("fiscal_year")) or fy_inf,
        "result_type":        extracted.get("result_type", "Standalone"),
        "period_months":      _clean_int(extracted.get("period_months")) or 3,
        "revenue_cr":         rev,
        "ebitda_cr":          ebitda,
        "ebitda_margin_pct":  margin,
        "pat_cr":             pat,
        "eps":                _clean(extracted.get("eps")),
        "cash_cr":            cash,
        "debt_cr":            debt,
        "net_debt_cr":        nd,
        "operating_cf_cr":    _clean(extracted.get("operating_cf_cr")),
        "free_cf_cr":         _clean(extracted.get("free_cf_cr")),
        "capex_cr":           _clean(extracted.get("capex_cr")),
        "dividend_per_share": _clean(extracted.get("dividend_per_share")),
        "book_value":         _clean(extracted.get("book_value")),
        "order_book_cr":      _clean(extracted.get("order_book_cr")),
        "employee_count":     _clean_int(extracted.get("employee_count")),
        "guidance_text":      extracted.get("guidance_text") or None,
        "result_notes":       extracted.get("result_notes") or None,
    }


def _build_pnl_comparison(extracted: dict, fields: dict) -> dict:
    """Build structured pnl_comparison from flat extracted fields."""
    return {
        "current": {
            "quarter":           fields["quarter"],
            "fiscal_year":       fields["fiscal_year"],
            "result_type":       fields["result_type"],
            "revenue_cr":        fields["revenue_cr"],
            "ebitda_cr":         fields["ebitda_cr"],
            "ebitda_margin_pct": fields["ebitda_margin_pct"],
            "pat_cr":            fields["pat_cr"],
            "eps":               fields["eps"],
        },
        "prev_quarter": {
            "quarter":           extracted.get("prev_quarter"),
            "fiscal_year":       _normalise_fy(extracted.get("prev_fy")),
            "revenue_cr":        _clean(extracted.get("prev_revenue_cr")),
            "ebitda_cr":         _clean(extracted.get("prev_ebitda_cr")),
            "ebitda_margin_pct": _clean(extracted.get("prev_ebitda_margin_pct")),
            "pat_cr":            _clean(extracted.get("prev_pat_cr")),
            "eps":               _clean(extracted.get("prev_eps")),
        },
        "same_qtr_last_year": {
            "quarter":           extracted.get("yoy_quarter"),
            "fiscal_year":       _normalise_fy(extracted.get("yoy_fy")),
            "revenue_cr":        _clean(extracted.get("yoy_revenue_cr")),
            "ebitda_cr":         _clean(extracted.get("yoy_ebitda_cr")),
            "ebitda_margin_pct": _clean(extracted.get("yoy_ebitda_margin_pct")),
            "pat_cr":            _clean(extracted.get("yoy_pat_cr")),
            "eps":               _clean(extracted.get("yoy_eps")),
        },
    }


def _save_to_db(symbol, result_date, pdf_url, pdf_path, fields, pnl, raw):
    quarter     = fields["quarter"]
    fiscal_year = fields["fiscal_year"]
    if not quarter or not fiscal_year:
        print(f"[EXTRACTOR] {symbol}: cannot save — quarter/FY unknown")
        return False

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
        INSERT INTO earnings_financials (
            symbol, quarter, fiscal_year, result_date, result_type,
            revenue_cr, ebitda_cr, ebitda_margin_pct, pat_cr, eps,
            cash_cr, debt_cr, net_debt_cr, operating_cf_cr, free_cf_cr,
            capex_cr, dividend_per_share, book_value, order_book_cr,
            employee_count, guidance_text, pdf_url, pdf_path, source, raw_data
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol, quarter, fiscal_year) DO UPDATE SET
            result_type=excluded.result_type,
            revenue_cr=excluded.revenue_cr, ebitda_cr=excluded.ebitda_cr,
            ebitda_margin_pct=excluded.ebitda_margin_pct, pat_cr=excluded.pat_cr,
            eps=excluded.eps, cash_cr=excluded.cash_cr, debt_cr=excluded.debt_cr,
            net_debt_cr=excluded.net_debt_cr, operating_cf_cr=excluded.operating_cf_cr,
            free_cf_cr=excluded.free_cf_cr, capex_cr=excluded.capex_cr,
            dividend_per_share=excluded.dividend_per_share, book_value=excluded.book_value,
            order_book_cr=excluded.order_book_cr, employee_count=excluded.employee_count,
            guidance_text=excluded.guidance_text, pdf_url=excluded.pdf_url,
            pdf_path=excluded.pdf_path, raw_data=excluded.raw_data
        """, (
            symbol, quarter, fiscal_year, result_date,
            fields.get("result_type", "Consolidated"),
            fields["revenue_cr"], fields["ebitda_cr"], fields["ebitda_margin_pct"],
            fields["pat_cr"], fields["eps"], fields["cash_cr"], fields["debt_cr"],
            fields["net_debt_cr"], fields["operating_cf_cr"], fields["free_cf_cr"],
            fields["capex_cr"], fields["dividend_per_share"], fields["book_value"],
            fields["order_book_cr"], fields["employee_count"], fields["guidance_text"],
            pdf_url, pdf_path, "GEMINI_VISION", json.dumps(raw)
        ))
        conn.commit()
        print(f"[EXTRACTOR] {symbol} {quarter} {fiscal_year}: saved ✓")

        # Save prior quarters from comparison table
        for pq in [pnl.get("prev_quarter", {}), pnl.get("same_qtr_last_year", {})]:
            pq_q  = pq.get("quarter")
            pq_fy = pq.get("fiscal_year")
            if not pq_q or not pq_fy or pq.get("revenue_cr") is None:
                continue
            pm = pq.get("ebitda_margin_pct")
            if pm is None:
                pr = pq.get("revenue_cr"); pe = pq.get("ebitda_cr")
                if pr and pe and pr > 0: pm = round(pe / pr * 100, 1)
            try:
                conn.execute("""
                INSERT INTO earnings_financials
                    (symbol, quarter, fiscal_year, revenue_cr, ebitda_cr,
                     ebitda_margin_pct, pat_cr, eps, source)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol, quarter, fiscal_year) DO NOTHING
                """, (symbol, pq_q, pq_fy, pq.get("revenue_cr"), pq.get("ebitda_cr"),
                      pm, pq.get("pat_cr"), pq.get("eps"), "PDF_COMPARISON"))
                conn.commit()
                print(f"[EXTRACTOR] Prior quarter saved: {pq_q} {pq_fy}")
            except Exception as e:
                print(f"[EXTRACTOR] Prior quarter save error: {e}")

        return True
    except Exception as e:
        print(f"[EXTRACTOR] DB save error: {e}")
        return False
    finally:
        conn.close()


def extract_financials(item: dict) -> dict:
    symbol       = item.get("symbol", "")
    company_name = item.get("company_name", symbol)
    pdf_path     = item.get("pdf_path", "")
    pdf_url      = item.get("pdf_url", "")
    result_date  = item.get("announcement_date", datetime.now(IST).strftime("%Y-%m-%d"))

    print(f"\n[EXTRACTOR] {company_name} ({symbol}) — extracting...")

    if not pdf_path or not os.path.exists(pdf_path):
        print(f"[EXTRACTOR] {symbol}: no PDF on disk")
        item["extraction_status"] = "no_pdf"
        return item

    file_size_kb = os.path.getsize(pdf_path) // 1024
    print(f"[EXTRACTOR] PDF size: {file_size_kb} KB")

    raw_extracted = _call_gemini(pdf_path)

    if not raw_extracted:
        print(f"[EXTRACTOR] {symbol}: Gemini returned empty")
        item["extraction_status"] = "failed"
        return item

    fields = _validate(raw_extracted, symbol, result_date)
    pnl    = _build_pnl_comparison(raw_extracted, fields)
    saved  = _save_to_db(symbol, result_date, pdf_url, pdf_path, fields, pnl, raw_extracted)

    item["extraction_status"]  = "extracted" if saved else "failed"
    item["quarter"]            = fields["quarter"]
    item["fiscal_year"]        = fields["fiscal_year"]
    item["revenue_cr"]         = fields["revenue_cr"]
    item["pat_cr"]             = fields["pat_cr"]
    item["eps"]                = fields["eps"]
    item["ebitda_margin_pct"]  = fields["ebitda_margin_pct"]
    item["financials"]         = fields
    item["pnl_comparison"]     = pnl

    if saved:
        rev  = f"₹{fields['revenue_cr']:.0f}Cr"  if fields["revenue_cr"]  else "N/A"
        pat  = f"₹{fields['pat_cr']:.0f}Cr"      if fields["pat_cr"]      else "N/A"
        marg = f"{fields['ebitda_margin_pct']:.1f}%" if fields["ebitda_margin_pct"] else "N/A"
        print(f"[EXTRACTOR] {symbol}: Rev={rev} PAT={pat} EBITDA%={marg} EPS={fields['eps']}")

    return item


def extract_batch(items: list) -> list:
    enriched = []
    for item in items:
        if item.get("download_status") in ("downloaded", "already_exists"):
            item = extract_financials(item)
        else:
            item["extraction_status"] = "no_pdf"
        enriched.append(item)
    extracted = sum(1 for i in enriched if i.get("extraction_status") == "extracted")
    failed    = sum(1 for i in enriched if i.get("extraction_status") == "failed")
    no_pdf    = sum(1 for i in enriched if i.get("extraction_status") == "no_pdf")
    print(f"\n[EXTRACTOR] Batch — extracted:{extracted} failed:{failed} no_pdf:{no_pdf}")
    return enriched
