"""Debug all 3 Gemini calls on TIMEX PDF."""
import sys, os, base64, requests, json
sys.path.insert(0, '.')
from config import GEMINI_URL

pdf = 'pdf_archive/TIMEX_4f16c0a1-5925-431d-a.pdf'
if not os.path.exists(pdf):
    pdfs = [f for f in os.listdir('pdf_archive') if 'TIMEX' in f]
    if pdfs: pdf = f'pdf_archive/{pdfs[0]}'
    else: print("No TIMEX PDF found"); sys.exit(1)

print(f"Using: {pdf} ({os.path.getsize(pdf)//1024} KB)")
with open(pdf, 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()

def call(prompt, label, tokens=512):
    payload = {
        "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "application/pdf", "data": b64}}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": tokens, "responseMimeType": "application/json"}
    }
    resp = requests.post(GEMINI_URL, json=payload, timeout=120)
    print(f"\n{'='*50}")
    print(f"CALL: {label} | HTTP: {resp.status_code}")
    if resp.status_code == 200:
        try:
            raw = resp.json()['candidates'][0]['content']['parts'][0]['text']
            print(f"RAW (first 600): {raw[:600]}")
        except Exception as e:
            print(f"Parse error: {e}")
            print(f"Response: {resp.text[:300]}")
    else:
        print(f"Error: {resp.text[:300]}")

call('Extract from this BSE result PDF as JSON: {"revenue_cr": number, "pat_cr": number, "eps": number, "quarter": "Q4", "fiscal_year": "FY26"}', "SIMPLE TEST", 256)

call("""From this BSE result PDF, find the P&L comparison table (3 columns: current quarter, previous quarter, same quarter last year).
Return JSON: {"current": {"quarter": "Q4", "fiscal_year": "FY26", "revenue_cr": 0, "pat_cr": 0, "eps": 0}, "prev_quarter": {"quarter": "Q3", "fiscal_year": "FY26", "revenue_cr": 0, "pat_cr": 0, "eps": 0}, "same_qtr_last_year": {"quarter": "Q4", "fiscal_year": "FY25", "revenue_cr": 0, "pat_cr": 0, "eps": 0}}""", "CALL 1 PNL", 1024)
