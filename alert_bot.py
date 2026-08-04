"""
Smart Alert Bot — runs every 30 minutes
Sends instant alerts to #smart-alerts and #earning-alerts channels
Covers: Market crashes, RBI actions, IPOs, circuit breakers,
        earnings surprises, SEBI actions, global shocks
"""
import feedparser
import requests
import json
import os
import re
import time
from datetime import datetime
from config import WEBHOOK_SMART_ALERTS, WEBHOOK_EARNINGS, GEMINI_URL, IST
from env_tag import SOURCE_TAG

# Relative path: resolves under the script's working directory on the VM,
# the container's /app on Cloud Run, and is round-tripped through GCS by
# gcs_sync.py so dedup state survives between Cloud Run Job executions.
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alert_state.json")

# ─── ALERT SOURCES ────────────────────────────────────────────────────────────
ALERT_SOURCES = [
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.moneycontrol.com/rss/marketsindia.xml",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://feeds.reuters.com/reuters/INbusinessNews",
    "https://www.livemint.com/rss/markets",
]

EARNINGS_SOURCES = [
    "https://economictimes.indiatimes.com/markets/earnings/rssfeeds/2143117.cms",
    "https://www.moneycontrol.com/rss/results.xml",
    "https://www.business-standard.com/rss/companies-0.rss",
]

# ─── ALERT TRIGGERS ───────────────────────────────────────────────────────────
SMART_ALERT_TRIGGERS = {
    "🚨 MARKET CRASH": {
        "keywords": ["nifty crash","sensex crash","market crash","lower circuit","nifty falls 2%","nifty falls 3%","sensex falls 1000","market meltdown","black day","circuit breaker","panic selling","market rout"],
        "priority": "CRITICAL",
    },
    "🚀 MARKET SURGE": {
        "keywords": ["nifty all time high","sensex all time high","nifty record","sensex record","nifty 52 week high","bull run","market rally 2%","nifty surges","sensex surges 1000"],
        "priority": "HIGH",
    },
    "🏦 RBI ACTION": {
        "keywords": ["rbi rate cut","rbi rate hike","rbi emergency","rbi repo rate","rbi mpc decision","rbi monetary policy","repo rate cut","repo rate hike","rbi governor statement"],
        "priority": "CRITICAL",
    },
    "📋 IPO ALERT": {
        "keywords": ["ipo listing today","ipo opens today","ipo gmp","ipo allotment","listing gain","listing loss","ipo subscribed","ipo closes today","ipo price band announced"],
        "priority": "HIGH",
    },
    "⚡ SEBI ACTION": {
        "keywords": ["sebi ban","sebi penalty","sebi order","sebi circular","sebi investigation","sebi probe","sebi suspension","sebi show cause"],
        "priority": "HIGH",
    },
    "🌍 GLOBAL SHOCK": {
        "keywords": ["fed emergency","fed surprise rate","us market crash","global market crash","oil price crash","gold price surge","dollar index surge","rupee hits record low"],
        "priority": "HIGH",
    },
    "💥 CORPORATE CRISIS": {
        "keywords": ["promoter arrested","ceo arrested","fraud detected","accounting fraud","company default","bond default","loan default","going concern","auditor resigned"],
        "priority": "CRITICAL",
    },
}

EARNINGS_TRIGGERS = {
    "✅ EARNINGS BEAT": {
        "keywords": ["beats estimates","beat estimates","profit jumps","revenue beats","net profit rises","earnings beat","strong quarterly","exceeds expectations","record quarterly","highest ever profit"],
        "verdict": "BEAT",
    },
    "❌ EARNINGS MISS": {
        "keywords": ["misses estimates","miss estimates","profit falls","revenue miss","net profit drops","earnings miss","weak quarterly","below expectations","profit down","revenue decline"],
        "verdict": "MISS",
    },
    "📊 RESULTS DECLARED": {
        "keywords": ["q1 results","q2 results","q3 results","q4 results","quarterly results","financial results","declares results","annual results","q1 fy","q2 fy","q3 fy","q4 fy"],
        "verdict": "NEUTRAL",
    },
}

# Earnings calendar — major companies by month
EARNINGS_CALENDAR = {
    7: ["TCS","Infosys","HDFC Bank","Reliance","Wipro","HCL Tech","ICICI Bank","Axis Bank","Kotak Bank","SBI","L&T","Bajaj Finance","Asian Paints","Maruti","Sun Pharma"],
    8: ["NTPC","Power Grid","Coal India","ONGC","Tata Motors","Bajaj Auto","Hero MotoCorp","Titan","Ultratech","Nestle","Dabur","Marico","Britannia"],
    10: ["TCS","Infosys","Wipro","HCL Tech","HDFC Bank","ICICI Bank","Axis Bank","Kotak Bank","Reliance","SBI"],
    11: ["L&T","Bajaj Finance","Asian Paints","Maruti","Sun Pharma","NTPC","Power Grid"],
    1: ["TCS","Infosys","Wipro","HCL Tech","HDFC Bank","ICICI Bank","Axis Bank"],
    2: ["Reliance","SBI","L&T","Bajaj Finance","Tata Motors","Bajaj Auto"],
    4: ["TCS","Infosys","Wipro","HDFC Bank","ICICI Bank"],
    5: ["Reliance","SBI","L&T","Asian Paints","Maruti"],
}

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {"alerted": [], "earnings_alerted": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def discord_post(webhook, text):
    if not text.strip():
        return
    requests.post(webhook, json={"content": text})
    time.sleep(0.5)

def clean_text(html):
    t = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', t).strip()

def gemini_call(prompt, max_tokens=600):
    try:
        resp = requests.post(
            GEMINI_URL,
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens}},
            timeout=30
        )
        if resp.status_code == 200:
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        return None
    except:
        return None

def gemini_earnings_verdict(company, title, summary):
    """Quick AI verdict on earnings."""
    prompt = f"""Indian company earnings result alert for retail investors.
Company: {company}
Headline: {title}
Details: {summary[:1000]}

Give a quick 3-line verdict:
1. BEAT/MISS/IN-LINE on revenue and profit (with numbers if available)
2. Key reason for the performance
3. What retail investor should do: BUY / HOLD / AVOID with one reason

Also give 1 line in Marathi.

Format:
VERDICT: [3 lines]
MARATHI: [1 Marathi sentence]"""
    return gemini_call(prompt, 400)

def send_earnings_calendar():
    """Send morning earnings calendar if companies expected today."""
    now = datetime.now(IST)
    month = now.month
    companies = EARNINGS_CALENDAR.get(month, [])
    if not companies:
        return
    msg = (
        f"📅 **EARNINGS SEASON ALERT** | {now.strftime('%d %B %Y')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Companies expected to declare results this month:**\n"
    )
    for c in companies:
        msg += f"• {c}\n"
    msg += (
        f"\n⚡ Watch these stocks for **high volatility**\n"
        f"📊 Results typically move stocks **5-20%**\n"
        f"🎯 Check positions before results declare"
    )
    discord_post(WEBHOOK_EARNINGS, msg)
    print(f"[ALERT] Earnings calendar sent for {len(companies)} companies")

def check_smart_alerts(state):
    """Check for market-wide alerts."""
    alerted = state.get("alerted", [])
    found = []
    for url in ALERT_SOURCES:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                title   = entry.get("title", "").strip()
                summary = clean_text(entry.get("summary", entry.get("description", "")))
                link    = entry.get("link", "").strip()
                key     = title[:80]
                if key in alerted:
                    continue
                text = (title + " " + summary).lower()
                for alert_type, config in SMART_ALERT_TRIGGERS.items():
                    if any(kw in text for kw in config["keywords"]):
                        found.append({
                            "type":     alert_type,
                            "priority": config["priority"],
                            "title":    title,
                            "summary":  summary[:300],
                            "link":     link,
                            "key":      key,
                        })
                        alerted.append(key)
                        break
        except Exception as e:
            print(f"[WARN] Alert source error: {e}")

    if found:
        now_str = datetime.now(IST).strftime("%d %B %Y, %I:%M %p IST")
        msg = (
            f"🚨 **BREAKING MARKET ALERT** 🚨  |  {SOURCE_TAG}\n"
            f"🕐 {now_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        for item in found:
            priority_tag = "🔴" if item["priority"] == "CRITICAL" else "🟠"
            msg += (
                f"{priority_tag} {item['type']}\n"
                f"**{item['title']}**\n"
                f"{item['summary']}\n"
                f"🔗 {item['link']}\n\n"
            )
        discord_post(WEBHOOK_SMART_ALERTS, msg)
        print(f"[ALERT] Sent {len(found)} smart alerts")

    state["alerted"] = alerted[-300:]
    return state

def check_earnings_alerts(state):
    """Check for earnings results."""
    earnings_alerted = state.get("earnings_alerted", [])
    found = []
    for url in EARNINGS_SOURCES:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:20]:
                title   = entry.get("title", "").strip()
                summary = clean_text(entry.get("summary", entry.get("description", "")))
                link    = entry.get("link", "").strip()
                key     = title[:80]
                if key in earnings_alerted:
                    continue
                text = (title + " " + summary).lower()
                for alert_type, config in EARNINGS_TRIGGERS.items():
                    if any(kw in text for kw in config["keywords"]):
                        company = re.split(r'[,\-\|:]', title)[0].strip()[:50]
                        found.append({
                            "type":    alert_type,
                            "verdict": config["verdict"],
                            "company": company,
                            "title":   title,
                            "summary": summary[:500],
                            "link":    link,
                            "key":     key,
                        })
                        earnings_alerted.append(key)
                        break
        except Exception as e:
            print(f"[WARN] Earnings source error: {e}")

    for item in found[:5]:  # max 5 per run
        now_str = datetime.now(IST).strftime("%d %B %Y, %I:%M %p IST")
        verdict_emoji = "✅" if item["verdict"] == "BEAT" else ("❌" if item["verdict"] == "MISS" else "📊")

        print(f"  -> Getting AI verdict for {item['company']}...")
        ai_verdict = gemini_earnings_verdict(item["company"], item["title"], item["summary"])
        time.sleep(3)

        msg = (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{verdict_emoji} **EARNINGS ALERT** | {now_str} | {SOURCE_TAG}\n"
            f"🏢 **{item['company'].upper()}**\n"
            f"{item['type']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"**{item['title']}**\n\n"
            f"{item['summary']}\n\n"
        )
        if ai_verdict:
            verdict_text = ""
            marathi_text = ""
            if "VERDICT:" in ai_verdict:
                verdict_text = ai_verdict.split("VERDICT:")[1].split("MARATHI:")[0].strip()
            if "MARATHI:" in ai_verdict:
                marathi_text = ai_verdict.split("MARATHI:")[1].strip()
            if verdict_text:
                msg += f"**🤖 AI Verdict:**\n```\n{verdict_text}\n```\n"
            if marathi_text:
                msg += f"**मराठी:** {marathi_text}\n\n"
        msg += f"🔗 {item['link']}"
        discord_post(WEBHOOK_EARNINGS, msg)
        print(f"[EARNINGS] Alert sent for {item['company']}")

    state["earnings_alerted"] = earnings_alerted[-300:]
    return state

def main():
    now = datetime.now(IST)
    print(f"[ALERT] {now.strftime('%d %b %Y %I:%M %p IST')} — Checking alerts...")

    state = load_state()

    # Send earnings calendar at 8 AM
    if now.hour == 8 and now.minute < 35:
        send_earnings_calendar()

    # Check smart alerts
    state = check_smart_alerts(state)

    # Check earnings alerts
    state = check_earnings_alerts(state)

    save_state(state)
    print("[ALERT] Done")

if __name__ == "__main__":
    main()
