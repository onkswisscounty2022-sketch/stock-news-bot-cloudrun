"""
PDF Downloader
Downloads result filing PDFs from BSE for detected announcements.

Tier logic (per steering spec):
  Tier A/B — download + queue for full AI extraction
  Tier C/D — download + store path only, skip AI pipeline
"""
import requests
import sqlite3
import os
from datetime import datetime
from config import DB_PATH, PDF_ARCHIVE, IST

BSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept":  "application/pdf, application/octet-stream, */*",
    "Referer": "https://www.bseindia.com/",
}


def _archive_url(pdf_url: str) -> str:
    """Convert AttachLive URL to AttachHis (archive) URL."""
    return pdf_url.replace("/AttachLive/", "/AttachHis/")


def _try_download_url(url: str, local_path: str, company_name: str) -> bool:
    """
    Attempt to download a PDF from url to local_path.
    Returns True on success, False on failure.
    """
    try:
        resp = requests.get(url, headers=BSE_HEADERS, timeout=30, stream=True)
        if resp.status_code == 200:
            content_type = resp.headers.get("Content-Type", "")
            if "html" in content_type.lower():
                return False
            # Validate first bytes are PDF signature
            first_chunk = b""
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if not first_chunk:
                        first_chunk = chunk[:5]
                    f.write(chunk)
            # Check PDF magic bytes
            if first_chunk and not first_chunk.startswith(b"%PDF"):
                os.remove(local_path)
                return False
            size_kb = os.path.getsize(local_path) // 1024
            print(f"[PDF] {company_name}: downloaded ({size_kb} KB) from {url[-40:]}")
            return True
        return False
    except Exception as e:
        print(f"[PDF] Download error from {url[-40:]}: {e}")
        return False


def _get_tier(symbol: str, bse_scrip_cd: str) -> str:
    """Look up tier from company_profile. Default D."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT tier FROM company_profile WHERE symbol=? OR bse_code=?",
        (symbol, bse_scrip_cd)
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] else "D"


def _make_filename(symbol: str, announcement_id: str) -> str:
    """Generate a clean filename for the PDF."""
    # Strip BSE: prefix if present, sanitize
    clean_sym = symbol.replace("BSE:", "").replace("/", "_").replace(" ", "_")
    clean_id  = announcement_id.replace("BSE_", "").replace("/", "_")[:20]
    return f"{clean_sym}_{clean_id}.pdf"


def download_pdf(item: dict) -> dict:
    """
    Download PDF for a single detection result.

    Returns updated item dict with:
        pdf_path  — local path if downloaded, empty string if skipped/failed
        tier      — resolved tier
        download_status — 'downloaded' | 'skipped_tier' | 'no_url' | 'failed'
    """
    symbol         = item.get("symbol", "")
    bse_scrip_cd   = item.get("bse_scrip_cd", "")
    pdf_url        = item.get("pdf_url", "")
    announcement_id = item.get("announcement_id", "")
    company_name   = item.get("company_name", symbol)

    # Resolve tier
    tier = item.get("tier") or _get_tier(symbol, bse_scrip_cd)
    item["tier"] = tier

    # No URL — can't download
    if not pdf_url:
        print(f"[PDF] {company_name}: no PDF URL — skipping")
        item["pdf_path"] = ""
        item["download_status"] = "no_url"
        return item

    # Ensure URL looks like a BSE filing
    if not any(x in pdf_url for x in ["AttachLive", "AttachHis", ".pdf", "bseindia"]):
        print(f"[PDF] {company_name}: unrecognised URL format — skipping")
        item["pdf_path"] = ""
        item["download_status"] = "no_url"
        return item

    # Build local path
    os.makedirs(PDF_ARCHIVE, exist_ok=True)
    filename   = _make_filename(symbol, announcement_id)
    local_path = os.path.join(PDF_ARCHIVE, filename)

    # Already downloaded — skip
    if os.path.exists(local_path) and os.path.getsize(local_path) > 1024:
        print(f"[PDF] {company_name}: already exists — {filename}")
        item["pdf_path"] = local_path
        item["download_status"] = "already_exists"
        _update_db_pdf_path(announcement_id, local_path)
        return item

    # Try AttachLive first, then AttachHis (archive fallback — Bug #13)
    urls_to_try = [pdf_url]
    if "/AttachLive/" in pdf_url:
        urls_to_try.append(_archive_url(pdf_url))
    elif "/AttachHis/" not in pdf_url:
        # Unknown path — also try both variants
        urls_to_try.append(pdf_url.replace("/AttachLive/", "/AttachHis/"))

    success = False
    for try_url in urls_to_try:
        if _try_download_url(try_url, local_path, company_name):
            success = True
            item["pdf_url"]          = try_url   # Update to working URL
            item["pdf_path"]         = local_path
            item["download_status"]  = "downloaded"
            _update_db_pdf_path(announcement_id, local_path)
            print(f"[PDF] {company_name} (Tier {tier}): saved as {filename}")
            break

    if not success:
        print(f"[PDF] {company_name}: all URLs failed — download failed")
        item["pdf_path"] = ""
        item["download_status"] = "failed"

    return item


def _update_db_pdf_path(announcement_id: str, local_path: str):
    """Store the local PDF path in result_detection_log."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE result_detection_log SET announcement_url=? WHERE announcement_id=?",
            (local_path, announcement_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[PDF] DB update error: {e}")


def download_batch(items: list) -> list:
    """
    Download PDFs for a list of detection results.
    Returns enriched list with pdf_path and download_status added.
    """
    enriched = []
    for item in items:
        enriched.append(download_pdf(item))

    # Summary
    downloaded = sum(1 for i in enriched if i.get("download_status") == "downloaded")
    skipped    = sum(1 for i in enriched if i.get("download_status") == "no_url")
    failed     = sum(1 for i in enriched if i.get("download_status") == "failed")
    existing   = sum(1 for i in enriched if i.get("download_status") == "already_exists")

    print(f"[PDF] Batch complete — downloaded:{downloaded} existing:{existing} no_url:{skipped} failed:{failed}")
    return enriched


if __name__ == "__main__":
    from database import init_db
    from symbol_resolver import ensure_cache_loaded
    init_db()
    ensure_cache_loaded()

    # Test with today's detections from DB
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT symbol, announcement_id, announcement_url FROM result_detection_log ORDER BY detected_at DESC LIMIT 5"
    ).fetchall()
    conn.close()

    test_items = [
        {"symbol": r[0], "announcement_id": r[1], "pdf_url": r[2], "company_name": r[0], "bse_scrip_cd": r[0].replace("BSE:", "")}
        for r in rows
    ]
    results = download_batch(test_items)
    for r in results:
        print(f"  {r['company_name']:35} | tier:{r['tier']} | {r['download_status']} | {r.get('pdf_path','')[:50]}")
