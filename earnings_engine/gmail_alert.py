"""
Gmail Alert
Sends full HTML earnings report via Gmail SMTP.
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import time
from config import GMAIL_SENDER, GMAIL_APP_PASSWORD, GMAIL_RECEIVER
from alert_formatter import format_gmail_html


def send_gmail_alert(item: dict) -> bool:
    """
    Send full HTML earnings report to GMAIL_RECEIVER.
    Returns True on success.
    """
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD or not GMAIL_RECEIVER:
        print("[GMAIL] Credentials not configured — skipping")
        return False

    symbol  = item.get("symbol", "UNKNOWN")
    company = item.get("company_name", symbol)
    quarter = item.get("quarter", "")
    fy      = item.get("fiscal_year", "")

    print(f"[GMAIL] Sending report for {company} ({symbol}) {quarter} {fy}...")

    try:
        subject, html_body = format_gmail_html(item)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_SENDER
        msg["To"]      = GMAIL_RECEIVER

        # Plain text fallback
        plain = f"{company} ({symbol}) — {quarter} {fy}\nScore: {item.get('overall_score','N/A')}/100\n{item.get('classification','N/A')}"
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_SENDER, GMAIL_RECEIVER, msg.as_string())

        print(f"[GMAIL] Report sent to {GMAIL_RECEIVER} ✓")
        return True

    except smtplib.SMTPAuthenticationError:
        print(f"[GMAIL] Authentication failed — check GMAIL_APP_PASSWORD")
        return False
    except Exception as e:
        print(f"[GMAIL] Send failed: {e}")
        return False


def send_gmail_batch(items: list) -> int:
    """Send Gmail alerts for a batch of analyzed results."""
    sent = 0
    for item in items:
        if item.get("analysis_status") == "analyzed":
            if send_gmail_alert(item):
                sent += 1
            time.sleep(3)  # Avoid hitting Gmail rate limits
    print(f"[GMAIL] Batch done — {sent}/{len(items)} emails sent")
    return sent


if __name__ == "__main__":
    """Test with latest analyzed result from DB — using real extracted data."""
    import sys, os, sqlite3, json
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from database import init_db
    from config import DB_PATH
    from discord_alert import _build_full_item_from_db

    init_db()
    item = _build_full_item_from_db()
    if not item:
        print("No analyzed results in DB yet. Run: python3 lookup.py TIMEX first.")
        sys.exit(0)

    print(f"Testing Gmail alert for {item['company_name']} ({item['symbol']}) {item['quarter']} {item['fiscal_year']}...")
    result = send_gmail_alert(item)
    print(f"Gmail test: {'✓ Success' if result else '✗ Failed'}")
