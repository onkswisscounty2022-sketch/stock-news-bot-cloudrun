"""
Weekly Wrap Bot — runs every Sunday at 8:30 AM IST (3:00 UTC)
Posts to #weekly-wrap channel
Covers: Top stories, sector rotation, stocks in focus, next week preview
"""
import feedparser
import requests
import smtplib
import os
import re
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from config import WEBHOOK_WEEKLY_WRAP, GEMINI_URL, IST, GMAIL_SENDER, GMAIL_APP_PASSWORD, GMAIL_RECEIVER
from env_tag import SOURCE_TAG

def gemini_call(prompt, max_tokens=2000):
    try:
        resp = requests.post(
            GEMINI_URL,
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens}},
            timeout=45
        )
        if resp.status_code == 200:
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        print(f"[WARN] Gemini {resp.status_code}")
        return None
    except Exception as e:
        print(f"[WARN] Gemini: {e}")
        return None

def discord_post(text):
    if not text.strip():
        return
    requests.post(WEBHOOK_WEEKLY_WRAP, json={"content": text})
    time.sleep(0.6)

def clean_text(html):
    t = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', t).strip()

def fetch_week_news():
    """Fetch last 7 days of major India market news."""
    sources = [
        "https://www.business-standard.com/rss/markets-106.rss",
        "https://www.livemint.com/rss/markets",
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "https://www.moneycontrol.com/rss/marketsindia.xml",
    ]
    india_kw = ["nifty","sensex","india","indian","rbi","sebi","rupee","bse","nse","fii"]
    all_titles = []
    for url in sources:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:15]:
                t = e.get("title","").strip()
                if t and any(k in t.lower() for k in india_kw):
                    all_titles.append(t)
        except:
            pass
    # deduplicate
    seen = set()
    unique = []
    for t in all_titles:
        k = t[:60].lower()
        if k not in seen:
            seen.add(k)
            unique.append(t)
    return unique[:50]

def generate_weekly_digest(titles):
    titles_str = "\n".join([f"{i+1}. {t}" for i, t in enumerate(titles[:40])])
    prompt = f"""You are a senior Indian stock market analyst writing the Sunday weekly wrap for retail investors.

This week's major headlines:
{titles_str}

Write a comprehensive weekly digest covering:

1. TOP_STORIES: Top 5 most important stories of the week. Each story: title + 3 sentence explanation of what happened, why it matters, and what to watch.

2. SECTOR_WINNERS: Top 3 sectors that performed well this week. Each: sector name + 2 sentence reason.

3. SECTOR_LOSERS: Top 2 sectors that struggled. Each: sector name + 2 sentence reason.

4. STOCKS_IN_FOCUS: 6 stocks most mentioned/moved this week. Each: stock name + one line observation.

5. FII_DII_WATCH: Assessment of institutional money flow this week and what it signals.

6. NEXT_WEEK_PREVIEW: 3 key events/data points to watch next week. Be specific.

7. WEEKLY_OUTLOOK: Overall market outlook for next week — 3 sentences. Be direct and actionable.

8. MARATHI_SUMMARY: 4 sentence simple Marathi summary of this week for retail investors.

Format exactly:
TOP_STORIES:
1. [title] — [3 sentences]
2. [title] — [3 sentences]
3. [title] — [3 sentences]
4. [title] — [3 sentences]
5. [title] — [3 sentences]
SECTOR_WINNERS:
- [sector]: [2 sentences]
- [sector]: [2 sentences]
- [sector]: [2 sentences]
SECTOR_LOSERS:
- [sector]: [2 sentences]
- [sector]: [2 sentences]
STOCKS_IN_FOCUS:
- [stock]: [one line]
- [stock]: [one line]
- [stock]: [one line]
- [stock]: [one line]
- [stock]: [one line]
- [stock]: [one line]
FII_DII_WATCH: [assessment]
NEXT_WEEK_PREVIEW:
1. [event/data point]
2. [event/data point]
3. [event/data point]
WEEKLY_OUTLOOK: [3 sentences]
MARATHI_SUMMARY: [4 Marathi sentences]"""

    return gemini_call(prompt, 2500)

def parse_and_post(text, now_str):
    if not text:
        discord_post("⚠️ Could not generate weekly digest. Please check manually.")
        return

    discord_post(
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 **WEEKLY MARKET WRAP** | Sunday Digest | {SOURCE_TAG}\n"
        f"🕐 {now_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    def extract(section, next_section=None):
        if section + ":" not in text:
            return ""
        start = text.find(section + ":") + len(section) + 1
        if next_section and next_section + ":" in text:
            end = text.find(next_section + ":")
            return text[start:end].strip()
        return text[start:].strip()

    # Top Stories
    stories = extract("TOP_STORIES", "SECTOR_WINNERS")
    if stories:
        msg = "**📰 TOP 5 STORIES OF THE WEEK**\n\n" + stories[:1800]
        discord_post(msg)

    # Sector Winners/Losers
    winners = extract("SECTOR_WINNERS", "SECTOR_LOSERS")
    losers  = extract("SECTOR_LOSERS", "STOCKS_IN_FOCUS")
    if winners or losers:
        msg = ""
        if winners:
            msg += "**🟢 SECTOR WINNERS THIS WEEK**\n" + winners + "\n\n"
        if losers:
            msg += "**🔴 SECTOR LOSERS THIS WEEK**\n" + losers
        discord_post(msg[:1900])

    # Stocks in Focus
    stocks = extract("STOCKS_IN_FOCUS", "FII_DII_WATCH")
    if stocks:
        discord_post("**📌 STOCKS IN FOCUS THIS WEEK**\n" + stocks[:1800])

    # FII/DII Watch
    fii = extract("FII_DII_WATCH", "NEXT_WEEK_PREVIEW")
    if fii:
        discord_post("**💰 FII/DII MONEY FLOW THIS WEEK**\n" + fii[:1500])

    # Next Week Preview
    preview = extract("NEXT_WEEK_PREVIEW", "WEEKLY_OUTLOOK")
    outlook = extract("WEEKLY_OUTLOOK", "MARATHI_SUMMARY")
    if preview or outlook:
        msg = ""
        if preview:
            msg += "**📅 NEXT WEEK — WHAT TO WATCH**\n" + preview + "\n\n"
        if outlook:
            msg += "**🔮 MARKET OUTLOOK FOR NEXT WEEK**\n```\n" + outlook + "\n```"
        discord_post(msg[:1900])

    # Marathi Summary
    marathi = extract("MARATHI_SUMMARY")
    if marathi:
        discord_post("**🇮🇳 मराठी साप्ताहिक सारांश**\n" + marathi[:1500])

def send_weekly_email(text, now_str):
    """Send weekly digest as formatted email."""
    subject = f"[{SOURCE_TAG}] 📅 Weekly Market Wrap | {datetime.now(IST).strftime('%d %b %Y')}"

    # simple HTML version
    html = (
        f"<html><body style='font-family:Arial,sans-serif;max-width:760px;margin:auto;background:#f4f4f4;padding:20px;'>"
        f"<div style='background:linear-gradient(135deg,#1a73e8,#0d47a1);color:white;padding:26px;border-radius:10px 10px 0 0;text-align:center;'>"
        f"<h2 style='margin:0;'>📅 Weekly Market Wrap</h2>"
        f"<p style='margin:6px 0 0;opacity:.85;font-size:14px;'>Sunday Digest · {now_str}</p></div>"
        f"<div style='background:white;padding:24px;border-radius:0 0 10px 10px;box-shadow:0 2px 12px rgba(0,0,0,.08);'>"
        f"<pre style='white-space:pre-wrap;font-family:Arial,sans-serif;font-size:13px;line-height:1.7;color:#333;'>{text}</pre>"
        f"</div></body></html>"
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = GMAIL_RECEIVER
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            s.sendmail(GMAIL_SENDER, GMAIL_RECEIVER, msg.as_string())
        print("[WEEKLY] Gmail sent")
    except Exception as e:
        print(f"[WEEKLY] Gmail error: {e}")

def main():
    now     = datetime.now(IST)
    now_str = now.strftime("%d %B %Y, %I:%M %p IST")
    print(f"[WEEKLY] {now_str} — Generating weekly digest...")

    titles = fetch_week_news()
    print(f"[WEEKLY] Fetched {len(titles)} headlines from this week")

    text = generate_weekly_digest(titles)
    parse_and_post(text, now_str)
    if text:
        send_weekly_email(text, now_str)
    print("[WEEKLY] Done")

if __name__ == "__main__":
    main()
