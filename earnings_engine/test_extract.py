"""Quick test: extract one PDF and show raw Gemini output."""
import sys, os, json, base64, requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import GEMINI_URL, IST
from database import init_db
from datetime import datetime

init_db()

# Use smallest PDF
PDF = os.path.join("pdf_archive", "CORNE_f5d892bf-c12f-441d-b.pdf")
if not os.path.exists(PDF):
    print("PDF not found"); sys.exit(1)

print(f"Testing with: {PDF}  ({os.path.getsize(PDF)//1024} KB)")

with open(PDF, "rb") as f:
    pdf_b64 = base64.b64encode(f.read()).decode("utf-8")

prompt = """You are an expert Indian financial analyst.
Extract the following from this BSE quarterly result PDF.
Return ONLY valid JSON (no markdown):
{
  "company_name": "full name",
  "quarter": "Q1/Q2/Q3/Q4",
  "fiscal_year": "FY25 format",
  "result_type": "Standalone or Consolidated",
  "revenue_cr": number or null,
  "ebitda_cr": number or null,
  "ebitda_margin_pct": number or null,
  "pat_cr": number or null,
  "eps": number or null,
  "cash_cr": number or null,
  "debt_cr": number or null,
  "net_debt_cr": number or null,
  "guidance_text": "any guidance statement or null",
  "result_notes": "brief key observation"
}"""

payload = {
    "contents": [{"parts": [
        {"text": prompt},
        {"inline_data": {"mime_type": "application/pdf", "data": pdf_b64}}
    ]}],
    "generationConfig": {
        "temperature": 0.1,
        "maxOutputTokens": 1024,
        "responseMimeType": "application/json"
    }
}

print("Calling Gemini... (may take 20-40s for PDF)")
resp = requests.post(GEMINI_URL, json=payload, timeout=120)
print(f"HTTP Status: {resp.status_code}")

if resp.status_code == 200:
    data = resp.json()
    candidates = data.get("candidates", [])
    if candidates:
        raw = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        print("\n--- RAW GEMINI OUTPUT ---")
        print(raw)
        try:
            parsed = json.loads(raw)
            print("\n--- PARSED JSON ---")
            print(json.dumps(parsed, indent=2))
        except Exception as e:
            print(f"JSON parse error: {e}")
    else:
        print("No candidates:", json.dumps(data, indent=2)[:500])
else:
    print("Error response:", resp.text[:500])
