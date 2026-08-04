"""
Concall Intelligence Bot
Runs at 6:30 PM and 9:00 PM IST daily during earnings season
Scrapes BSE/NSE announcements, analyses concall transcripts
Applies management behavior scoring dictionary
Posts to #concall-intel channel
"""
import requests
import json
import os
import re
import time
from datetime import datetime
from config import WEBHOOK_CONCALL, GEMINI_URL, IST
from env_tag import SOURCE_TAG

# ─── STATE FILE ───────────────────────────────────────────────────────────────
# Relative path: resolves under the script's working directory on the VM,
# the container's /app on Cloud Run, and is round-tripped through GCS by
# gcs_sync.py so dedup state survives between Cloud Run Job executions.
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "concall_state.json")

# ─── BSE ANNOUNCEMENT FEEDS ───────────────────────────────────────────────────
BSE_RESULTS_URL = "https://www.bseindia.com/corporates/ann.html"
NSE_RESULTS_RSS = "https://www.nseindia.com/api/corporate-announcements?index=equities&category=Financial+Results"

# Fallback — scrape from financial news
CONCALL_SOURCES = [
    "https://economictimes.indiatimes.com/markets/earnings/rssfeeds/2143117.cms",
    "https://www.moneycontrol.com/rss/results.xml",
    "https://www.business-standard.com/rss/companies-0.rss",
]

# ─── SCORING DICTIONARY ───────────────────────────────────────────────────────
SCORE_MAP = {
    # FINANCIAL DISTRESS / SOLVENCY (-10)
    "going concern": (-10, "🚨 CRITICAL", "Survival risk — company may not continue"),
    "debt restructuring": (-10, "🚨 CRITICAL", "Financial distress — banks involved"),
    "lender discussions": (-10, "🚨 CRITICAL", "Banks are worried about repayment"),
    "refinancing required": (-10, "🚨 CRITICAL", "Debt maturity problem"),
    "covenant discussions": (-9, "🚨 CRITICAL", "Loan stress — breaching bank conditions"),
    "promoter support": (-9, "🚨 CRITICAL", "Company cannot stand on its own"),
    "funding constraints": (-8, "🔴 DANGER", "Capital availability issue"),
    "tight liquidity": (-8, "🔴 DANGER", "Serious cash concern"),

    # GOVERNANCE / ACCOUNTING (-8 to -10)
    "whistleblower complaint": (-10, "🚨 CRITICAL", "Governance issue — serious concern"),
    "internal investigation": (-9, "🚨 CRITICAL", "Possible fraud or misconduct"),
    "auditor resigned": (-9, "🚨 CRITICAL", "Major governance red flag"),
    "independent director resigned": (-8, "🔴 DANGER", "Board governance concern"),
    "cfo resigned": (-8, "🔴 DANGER", "CFO exit — often signals problems"),
    "regulatory observations": (-8, "🔴 DANGER", "Investigation risk"),
    "show cause notice": (-8, "🔴 DANGER", "Regulatory action imminent"),
    "compliance issue": (-7, "🔴 DANGER", "Legal or regulatory problem"),
    "related party review": (-7, "🔴 DANGER", "Governance concern"),

    # ACCOUNTING RED FLAGS (-8)
    "restatement": (-8, "🔴 DANGER", "Prior numbers were wrong — serious concern"),
    "prior period adjustment": (-7, "🔴 DANGER", "Accounting correction"),
    "change in accounting policy": (-6, "🟠 WATCH", "Policy change may hide deterioration"),
    "revenue recognition": (-5, "🟠 WATCH", "Accounting treatment under scrutiny"),
    "impairment": (-8, "🔴 DANGER", "Asset value destroyed"),
    "goodwill write-off": (-7, "🔴 DANGER", "Acquisition has failed"),
    "write-down": (-6, "🟠 WATCH", "Asset value reduction"),
    "write-off": (-6, "🟠 WATCH", "Assets written off"),
    "provisioning increased": (-6, "🟠 WATCH", "Losses expected ahead"),
    "deferred tax asset": (-4, "🟡 MONITOR", "May indicate future loss expectation"),
    "non-cash adjustment": (-3, "🟡 MONITOR", "Verify what is being adjusted"),

    # DEMAND DETERIORATION (-7)
    "challenging environment": (-7, "🔴 DANGER", "Business is slowing"),
    "demand softness": (-7, "🔴 DANGER", "Customers not buying"),
    "weak demand": (-7, "🔴 DANGER", "Sales falling"),
    "demand visibility limited": (-6, "🟠 WATCH", "Future uncertain"),
    "volatile demand": (-5, "🟠 WATCH", "Orders unpredictable"),
    "order deferment": (-6, "🟠 WATCH", "Orders postponed"),
    "customer postponement": (-5, "🟠 WATCH", "Clients delaying purchases"),
    "project delays": (-5, "🟠 WATCH", "Revenue delayed"),
    "execution challenges": (-4, "🟡 MONITOR", "Unable to deliver on time"),
    "inventory correction": (-5, "🟠 WATCH", "Previous overproduction"),
    "elevated inventory": (-4, "🟡 MONITOR", "Products not selling"),
    "channel inventory": (-4, "🟡 MONITOR", "Dealer inventory high"),
    "capacity utilization declined": (-5, "🟠 WATCH", "Factory running idle"),
    "utilization below expectations": (-4, "🟡 MONITOR", "Lower production"),

    # MARGIN / COST PRESSURE (-6)
    "margin pressure": (-6, "🟠 WATCH", "Profit falling"),
    "pricing pressure": (-5, "🟠 WATCH", "Unable to increase prices"),
    "competitive intensity": (-5, "🟠 WATCH", "Losing market share"),
    "inflationary pressure": (-4, "🟡 MONITOR", "Costs rising"),
    "input cost inflation": (-4, "🟡 MONITOR", "Raw materials expensive"),
    "supply chain disruption": (-4, "🟡 MONITOR", "Operational issue"),

    # CASH FLOW WARNING (-6)
    "elongated working capital": (-6, "🟠 WATCH", "Cash flow stress"),
    "slow collection": (-5, "🟠 WATCH", "Cash not coming in"),
    "delayed payments": (-5, "🟠 WATCH", "Customers struggling to pay"),
    "liquidity management": (-5, "🟠 WATCH", "Cash shortage"),
    "cash conservation": (-4, "🟡 MONITOR", "Preserving cash — weak business"),
    "working capital increase": (-4, "🟡 MONITOR", "More cash locked up"),
    "receivable increase": (-3, "🟡 MONITOR", "Collections delayed"),
    "borrowing increase": (-4, "🟡 MONITOR", "Debt going up"),
    "interest burden": (-3, "🟡 MONITOR", "Debt cost increasing"),

    # RESTRUCTURING / CUTS (-5 to -8)
    "rationalization": (-6, "🟠 WATCH", "Layoffs or shutdown likely"),
    "rightsizing": (-6, "🟠 WATCH", "Layoffs coming"),
    "workforce optimization": (-5, "🟠 WATCH", "Employee reduction"),
    "efficiency initiative": (-4, "🟡 MONITOR", "Cost cutting mode"),
    "cost optimization": (-4, "🟡 MONITOR", "Cutting expenses — business weak"),
    "strategic review": (-5, "🟠 WATCH", "Possible sale or closure"),
    "portfolio optimization": (-5, "🟠 WATCH", "Selling business units"),
    "asset monetization": (-4, "🟡 MONITOR", "Selling assets for cash"),
    "deferred capex": (-5, "🟠 WATCH", "Cash preservation mode"),

    # MANAGEMENT EVASIVENESS (-4)
    "too early to comment": (-4, "🟡 MONITOR", "Management avoiding the question"),
    "cannot quantify": (-3, "🟡 MONITOR", "Dodging analyst question"),
    "we will update later": (-3, "🟡 MONITOR", "No clarity on issue"),
    "under evaluation": (-3, "🟡 MONITOR", "Decision not made"),
    "situation evolving": (-3, "🟡 MONITOR", "Management unsure"),
    "wait and watch": (-3, "🟡 MONITOR", "No visibility"),
    "we remain cautious": (-4, "🟡 MONITOR", "Subdued outlook"),
    "conservative guidance": (-4, "🟡 MONITOR", "Expect weak future"),
    "guidance withdrawn": (-7, "🔴 DANGER", "Situation deteriorating"),
    "no guidance": (-5, "🟠 WATCH", "Management uncertain"),
    "macro uncertainty": (-3, "🟡 MONITOR", "Excuse for poor performance"),
    "temporary headwinds": (-4, "🟡 MONITOR", "Often repeated — may not be temporary"),
    "normalizing demand": (-3, "🟡 MONITOR", "Growth slowing"),
    "mixed trends": (-2, "🟡 MONITOR", "Business uneven"),

    # PROMOTER WARNING (-5 to -8)
    "promoter pledge": (-6, "🟠 WATCH", "Promoter using shares as collateral"),
    "insider transaction": (-5, "🟠 WATCH", "Monitor for pattern"),
    "related party transaction": (-5, "🟠 WATCH", "Governance concern"),

    # ─── POSITIVE SIGNALS ─────────────────────────────────────────────────────

    # VERY STRONG BULLISH (+10)
    "unable to meet demand": (10, "🟢🟢 VERY BULLISH", "Demand exceeds capacity — expansion likely"),
    "order book at all-time high": (10, "🟢🟢 VERY BULLISH", "Record visibility into future revenue"),
    "capacity sold out": (10, "🟢🟢 VERY BULLISH", "Excellent demand"),
    "fully booked": (9, "🟢🟢 VERY BULLISH", "Strong visibility"),
    "capacity expansion already sold out": (10, "🟢🟢 VERY BULLISH", "Future revenue secured"),
    "guidance has been revised upward": (9, "🟢🟢 VERY BULLISH", "Management confidence high"),
    "guidance raised": (9, "🟢🟢 VERY BULLISH", "Strong confidence"),
    "multi-year contract": (9, "🟢 BULLISH", "Stable durable revenue"),
    "largest deal win": (10, "🟢🟢 VERY BULLISH", "Future growth secured"),

    # STRONG POSITIVE (+7 to +9)
    "record order book": (9, "🟢 BULLISH", "Future revenue visibility high"),
    "highest ever order inflow": (9, "🟢 BULLISH", "Strong demand"),
    "gaining market share": (7, "🟢 BULLISH", "Competitive advantage"),
    "market share gain": (7, "🟢 BULLISH", "Competitive position improving"),
    "new customer wins": (7, "🟢 BULLISH", "Business expanding"),
    "customer addition": (6, "🟢 BULLISH", "Revenue expanding"),
    "repeat orders": (7, "🟢 BULLISH", "Happy customers — sticky revenue"),
    "export growth": (6, "🟢 BULLISH", "New markets opening"),
    "debt free": (8, "🟢 BULLISH", "Strong balance sheet"),
    "net cash": (8, "🟢 BULLISH", "Financial strength"),
    "zero debt": (8, "🟢 BULLISH", "No financial risk"),

    # MARGIN / PROFITABILITY (+6 to +8)
    "margin expansion": (6, "🟢 BULLISH", "Better profitability"),
    "operating leverage": (6, "🟢 BULLISH", "Earnings can grow faster than revenue"),
    "roce improvement": (7, "🟢 BULLISH", "Efficient capital use"),
    "margin guidance upgraded": (8, "🟢 BULLISH", "Positive earnings surprise ahead"),
    "pricing power": (7, "🟢 BULLISH", "Strong brand — can raise prices"),
    "free cash flow improved": (7, "🟢 BULLISH", "High quality earnings"),
    "strong cash generation": (7, "🟢 BULLISH", "Healthy operations"),

    # GROWTH SIGNALS (+5 to +7)
    "capacity expansion": (5, "🟢 BULLISH", "Management confident in growth"),
    "new plant commissioned": (6, "🟢 BULLISH", "Growth investment paying off"),
    "commercial production started": (6, "🟢 BULLISH", "Revenue begins"),
    "entry into new geography": (5, "🟢 BULLISH", "Growth opportunity"),
    "product pipeline strong": (5, "🟢 BULLISH", "Innovation ahead"),
    "commercial launch": (6, "🟢 BULLISH", "Revenue catalyst"),
    "structural demand": (6, "🟢 BULLISH", "Long-term trend"),
    "multi-year growth opportunity": (7, "🟢 BULLISH", "Sustainable expansion"),
    "accelerating demand": (7, "🟢 BULLISH", "Growth momentum building"),
    "demand exceeds capacity": (10, "🟢🟢 VERY BULLISH", "Expansion likely"),

    # SHAREHOLDER VALUE (+6 to +9)
    "buyback approved": (8, "🟢 BULLISH", "Management confidence — stock undervalued"),
    "dividend increased": (7, "🟢 BULLISH", "Cash strength"),
    "special dividend": (8, "🟢 BULLISH", "Exceptional cash position"),
    "debt reduction": (7, "🟢 BULLISH", "Balance sheet improving"),

    # MANAGEMENT CONFIDENCE (+4 to +6)
    "strong visibility": (5, "🟢 BULLISH", "Predictable growth ahead"),
    "robust demand": (5, "🟢 BULLISH", "Strong order flow"),
    "healthy order book": (5, "🟢 BULLISH", "Revenue secured"),
    "confident of achieving guidance": (6, "🟢 BULLISH", "Management conviction high"),
    "momentum continues": (5, "🟢 BULLISH", "Growth trajectory intact"),
    "long runway": (5, "🟢 BULLISH", "Growth opportunity ahead"),
}

# Weights for category scoring
CATEGORY_WEIGHTS = {
    "🚨 CRITICAL": -10,
    "🔴 DANGER":   -7,
    "🟠 WATCH":    -4,
    "🟡 MONITOR":  -2,
    "🟢🟢 VERY BULLISH": 9,
    "🟢 BULLISH":  6,
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def discord_post(webhook, text):
    if not text.strip():
        return
    requests.post(webhook, json={"content": text})
    time.sleep(0.6)

def gemini_call(prompt, max_tokens=2000):
    try:
        resp = requests.post(
            GEMINI_URL,
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens}},
            timeout=45
        )
        if resp.status_code == 200:
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        print(f"[WARN] Gemini {resp.status_code}")
        return None
    except Exception as e:
        print(f"[WARN] Gemini: {e}")
        return None

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {"processed": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def clean_text(html):
    t = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', t).strip()

# ─── SCORING ENGINE ───────────────────────────────────────────────────────────
def score_concall_text(text):
    text_lower = text.lower()
    found_signals = []
    total_score = 0

    for phrase, (score, category, meaning) in SCORE_MAP.items():
        if phrase in text_lower:
            found_signals.append({
                "phrase":   phrase,
                "score":    score,
                "category": category,
                "meaning":  meaning,
            })
            total_score += score

    # cap score between -100 and +100
    total_score = max(-100, min(100, total_score))

    # sort by absolute score impact
    found_signals.sort(key=lambda x: abs(x["score"]), reverse=True)

    return total_score, found_signals

def score_label(score):
    if score >= 60:  return "🟢🟢 VERY POSITIVE"
    if score >= 30:  return "🟢 POSITIVE"
    if score >= 10:  return "🟡 MILDLY POSITIVE"
    if score >= -10: return "⚪ NEUTRAL"
    if score >= -30: return "🟠 CAUTIOUS"
    if score >= -60: return "🔴 NEGATIVE"
    return "🚨 VERY NEGATIVE"

def numbers_tone_match(text, score):
    """Check if management tone matches financial numbers."""
    positive_tone = sum(1 for p in ["confident","strong","robust","excellent","record","highest"] if p in text.lower())
    negative_tone = sum(1 for p in ["challenging","pressure","weak","decline","concern","cautious"] if p in text.lower())
    if score < -20 and positive_tone > negative_tone:
        return "⚠️ MISMATCH DETECTED — Management sounds positive but signals are negative"
    if score > 20 and negative_tone > positive_tone:
        return "✅ ALIGNED — Numbers and tone both positive"
    if score > 20 and positive_tone > 0:
        return "✅ ALIGNED — Management tone matches positive signals"
    if score < -20 and negative_tone > 0:
        return "✅ ALIGNED — Management tone matches negative signals"
    return "⚪ NEUTRAL ALIGNMENT"

# ─── GEMINI CONCALL ANALYSIS ─────────────────────────────────────────────────
def gemini_analyse_concall(company, quarter, text, score, signals):
    top_signals = "\n".join([
        f"  {s['phrase']} → {s['meaning']} (score: {s['score']:+d})"
        for s in signals[:10]
    ])
    prompt = f"""You are a senior equity research analyst specialising in Indian companies.

Company: {company}
Quarter: {quarter}
Concall Quality Score: {score}/100 ({score_label(score)})

Key signals detected from the concall:
{top_signals}

Concall text excerpt:
{text[:2500]}

Provide a sharp, actionable analysis covering:

1. VERDICT (2 sentences): Overall assessment of this concall — bullish, bearish or neutral?
2. KEY POSITIVES (2-3 bullet points): What management said that is genuinely good
3. KEY CONCERNS (2-3 bullet points): What should investors watch carefully
4. MANIPULATION CHECK: Is management using language to hide problems? Any mismatch between words and numbers?
5. SECTOR IMPACT: Which other companies in the same sector are affected by these results?
6. TRADING IMPLICATION: What should a retail investor do — Buy / Hold / Avoid / Wait? One clear sentence.
7. MARATHI VERDICT (2 sentences): Translate the verdict into simple Marathi for retail investors.

Keep all points crisp and specific. No generic statements.

Format:
VERDICT: [2 sentences]
KEY_POSITIVES:
- [point]
- [point]
KEY_CONCERNS:
- [point]
- [point]
MANIPULATION_CHECK: [assessment]
SECTOR_IMPACT: [companies and how affected]
TRADING_IMPLICATION: [clear action]
MARATHI_VERDICT: [2 Marathi sentences]"""

    return gemini_call(prompt, 1800)

# ─── FETCH EARNINGS NEWS ─────────────────────────────────────────────────────
def fetch_earnings_news():
    import feedparser
    results = []
    earnings_keywords = [
        "q1 results", "q2 results", "q3 results", "q4 results",
        "quarterly results", "net profit", "quarterly earnings",
        "beats estimates", "misses estimates", "revenue growth",
        "ebitda", "pat", "concall", "conference call", "earnings call",
        "financial results", "quarterly performance",
    ]
    state   = load_state()
    processed = state.get("processed", [])

    for url in CONCALL_SOURCES:
        try:
            import feedparser as fp
            feed = fp.parse(url)
            for entry in feed.entries[:20]:
                title   = entry.get("title", "").strip()
                summary = clean_text(entry.get("summary", entry.get("description", "")))
                link    = entry.get("link", "").strip()
                key     = title[:80]

                if key in processed:
                    continue

                text = (title + " " + summary).lower()
                if any(kw in text for kw in earnings_keywords):
                    # extract company name (first 2-3 words usually)
                    company = re.split(r'[,\-\|]', title)[0].strip()[:50]
                    results.append({
                        "company": company,
                        "title":   title,
                        "summary": summary,
                        "link":    link,
                        "key":     key,
                    })
        except Exception as e:
            print(f"[WARN] Feed error: {e}")

    return results, state, processed

# ─── FORMAT AND POST CONCALL ANALYSIS ────────────────────────────────────────
def format_and_post(company, quarter, title, summary, link, score, signals, analysis):
    now_str = datetime.now(IST).strftime("%d %B %Y, %I:%M %p IST")
    label   = score_label(score)
    tone    = numbers_tone_match(summary, score)

    # Build signal summary
    positives = [s for s in signals if s["score"] > 0][:5]
    negatives = [s for s in signals if s["score"] < 0][:5]

    # Discord message
    msg = (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 **CONCALL INTELLIGENCE** | {now_str} | {SOURCE_TAG}\n"
        f"🏢 **{company.upper()}** | {quarter}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"**📊 CONCALL QUALITY SCORE: {score:+d}/100 — {label}**\n"
        f"`{'█' * min(abs(score)//10, 10)}{'░' * (10 - min(abs(score)//10, 10))}` {'positive' if score >= 0 else 'negative'}\n\n"
        f"**{tone}**\n\n"
    )

    if negatives:
        msg += "**🔴 RED FLAGS DETECTED:**\n"
        for s in negatives[:4]:
            msg += f"  {s['category']} `\"{s['phrase']}\"` — {s['meaning']}\n"
        msg += "\n"

    if positives:
        msg += "**🟢 POSITIVE SIGNALS:**\n"
        for s in positives[:4]:
            msg += f"  {s['category']} `\"{s['phrase']}\"` — {s['meaning']}\n"
        msg += "\n"

    discord_post(WEBHOOK_CONCALL, msg)

    # Post AI analysis
    if analysis:
        # parse sections
        sections = {
            "VERDICT":              "",
            "KEY_POSITIVES":        "",
            "KEY_CONCERNS":         "",
            "MANIPULATION_CHECK":   "",
            "SECTOR_IMPACT":        "",
            "TRADING_IMPLICATION":  "",
            "MARATHI_VERDICT":      "",
        }
        current = None
        for line in analysis.split("\n"):
            for key in sections:
                if line.startswith(key + ":"):
                    current = key
                    sections[key] = line.split(":", 1)[1].strip()
                    break
            else:
                if current and line.strip():
                    sections[current] += "\n" + line.strip()

        analysis_msg = (
            f"**🤖 AI ANALYSIS — {company.upper()}**\n\n"
            f"**Verdict:**\n{sections['VERDICT']}\n\n"
        )
        if sections['KEY_POSITIVES']:
            analysis_msg += f"**✅ Key Positives:**\n{sections['KEY_POSITIVES']}\n\n"
        if sections['KEY_CONCERNS']:
            analysis_msg += f"**⚠️ Key Concerns:**\n{sections['KEY_CONCERNS']}\n\n"
        if sections['MANIPULATION_CHECK']:
            analysis_msg += f"**🔍 Manipulation Check:**\n{sections['MANIPULATION_CHECK']}\n\n"
        if sections['SECTOR_IMPACT']:
            analysis_msg += f"**🏭 Sector Impact:**\n{sections['SECTOR_IMPACT']}\n\n"
        if sections['TRADING_IMPLICATION']:
            analysis_msg += f"**🎯 Trading Implication:**\n```\n{sections['TRADING_IMPLICATION']}\n```\n"
        if sections['MARATHI_VERDICT']:
            analysis_msg += f"**मराठी निष्कर्ष:**\n{sections['MARATHI_VERDICT']}\n\n"
        analysis_msg += f"🔗 {link}"

        discord_post(WEBHOOK_CONCALL, analysis_msg)

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    now = datetime.now(IST)
    print(f"[CONCALL] {now.strftime('%d %b %Y %I:%M %p IST')} — Scanning for earnings/concall news...")

    results, state, processed = fetch_earnings_news()
    print(f"[CONCALL] Found {len(results)} new earnings items")

    if not results:
        print("[CONCALL] No new concall news found")
        return

    new_processed = []
    for item in results[:8]:  # max 8 per run
        company = item["company"]
        title   = item["title"]
        summary = item["summary"]
        link    = item["link"]
        quarter = datetime.now(IST).strftime("Q%q FY%y").replace(
            "Q1","Q1").replace("Q2","Q2").replace("Q3","Q3").replace("Q4","Q4")
        # determine quarter from date
        month = now.month
        if month in [4,5,6]:   quarter = "Q1 FY" + str(now.year+1)[-2:]
        elif month in [7,8,9]: quarter = "Q2 FY" + str(now.year+1)[-2:]
        elif month in [10,11,12]: quarter = "Q3 FY" + str(now.year+1)[-2:]
        else:                  quarter = "Q4 FY" + str(now.year)[-2:]

        print(f"  -> Analysing: {company}...")
        score, signals = score_concall_text(title + " " + summary)
        analysis = gemini_analyse_concall(company, quarter, title + "\n\n" + summary, score, signals)
        time.sleep(3)

        format_and_post(company, quarter, title, summary, link, score, signals, analysis)
        new_processed.append(item["key"])
        time.sleep(2)

    # update state
    state["processed"] = (processed + new_processed)[-300:]
    save_state(state)
    print(f"[CONCALL] Done — analysed {len(new_processed)} items")

if __name__ == "__main__":
    main()
