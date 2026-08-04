"""
Indian Market + Crypto News Digest Bot
Runs at 8 AM, 2 PM, 6 PM IST daily + Sunday weekly digest
"""
import feedparser
import requests
import smtplib
import os
import re
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from collections import defaultdict
import pytz
from env_tag import SOURCE_TAG

# ─── CREDENTIALS ─────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
GMAIL_SENDER        = os.environ.get("GMAIL_SENDER")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD")
GMAIL_RECEIVER      = os.environ.get("GMAIL_RECEIVER")
GEMINI_API_KEY      = os.environ.get("GEMINI_API_KEY")
IST                 = pytz.timezone("Asia/Kolkata")
GEMINI_URL          = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"

# ─── RSS FEEDS — ordered by priority ─────────────────────────────────────────
INDIA_FEEDS = [
    # Tier 1 — Highest trust (4, 4, 3 articles)
    ("Livemint",          "https://www.livemint.com/rss/markets",                      4),
    ("Hindu BusinessLine","https://www.thehindubusinessline.com/markets/?service=rss", 4),
    ("Business Standard", "https://news.google.com/rss/search?q=business+standard+nifty+sensex+india+stock&hl=en-IN&gl=IN&ceid=IN:en", 3),
    # Tier 2 — Good trust (2, 2 articles)
    ("CNBC TV18",         "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml", 2),
    ("Moneycontrol",      "https://news.google.com/rss/search?q=moneycontrol+nifty+sensex+india+market&hl=en-IN&gl=IN&ceid=IN:en", 2),
    # Tier 3 — Fill-in (2, 2, 1, 1 articles)
    ("Economic Times",    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", 2),
    ("NDTV Profit",       "https://feeds.feedburner.com/ndtvprofit-latest",            2),
    ("ET Stocks",         "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms", 1),
    ("Google News India", "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pKVGlnQVAB?hl=en-IN&gl=IN&ceid=IN:en", 1),
]
TOTAL_INDIA_BUDGET = 22

# Tier 1 sources — trusted, bypass India keyword check
TIER1_SOURCES = {"Livemint", "Hindu BusinessLine", "Business Standard", "CNBC TV18"}
GLOBAL_FEEDS = [
    ("Reuters India",      "https://feeds.reuters.com/reuters/INbusinessNews"),
    ("Reuters Markets",    "https://feeds.reuters.com/reuters/businessNews"),
]
CRYPTO_FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Decrypt",  "https://decrypt.co/feed"),
]

# ─── SKIP PATTERNS — generic noise to filter out ─────────────────────────────
# ─── SKIP LIST — plain string contains, reliable ─────────────────────────────
SKIP_LIST = [
    # Pre-market and market wrap noise
    "ahead of market:", "gift nifty", "pre-open market", "market at a glance",
    "trade setup for", "week ahead:", "taking stock:", "market pulse:",
    "key triggers to watch", "market trading guide:", "10 things that will decide",
    "things that will decide stock", "closing bell", "opening bell",
    "stock market live", "market live update", "sensex today | stock market",
    "nifty today |", "market recap", "weekly wrap",
    # Stocks to watch / intraday
    "stocks to watch for", "stocks to watch:", "stock to watch",
    "top gainers & losers", "top gainers and losers",
    "gainers and losers", "buy or sell:", "stocks to watch tomorrow",
    "intraday picks", "intraday tips", "intraday strategy", "intraday stock",
    # Single broker notes
    "broker's call:", "brokerage call:", "brokers call:",
    # Old/irrelevant
    "pacl refund", "quote of the day", "thought of the day",
    # Speculative
    "price prediction", "price outlook 2026", "price forecast 2026",
    "could hit $", "could reach $", "might reach $",
    # Forex noise
    "nri deposit", "nri dollar", "nri parking",
    # US stock noise
    "riot platforms", "cleanspark", "applied digital",
    "wall street thinks", "wall street says",
    "outperform rating (", "buy the dip on",
    # Crypto noise — specific low quality articles
    "mica became law",
    "struggling nasdaq-listed company that tried to copy",
    "tried to copy s",
    "company that tried to copy",
    "jobs data to ig",
    "warsh's comments",
    "u.s. jobs data",
    "federal reserve comments",
    "set the stage for",
    # PR industry noise
    "pr industry grows",
    # Minor market commentary
    "market at a glance", "charts say", "technical view",
    "what will decide", "5 things", "10 things", "key things",
]

# ─── PRIORITY LIST — plain string contains, always include ────────────────────
PRIORITY_LIST = [
    # Earnings
    "q1 result", "q2 result", "q3 result", "q4 result",
    "quarterly result", "annual result",
    "net profit", "beats estimate", "misses estimate",
    "profit jumps", "profit falls", "profit rises", "profit drops",
    "revenue grows", "revenue declines", "revenue rises", "revenue falls",
    "ebitda", " pat of", "revenue of ₹", "net profit of ₹",
    # Corporate events
    "files ipo", "ipo papers", "files drhp", "drhp with sebi",
    "ipo opens", "ipo allotment", "ipo listing",
    "qip", "rights issue", "buyback announced", "buyback of ₹",
    "dividend declared", "dividend of ₹", "interim dividend",
    "merger announced", "acquisition of", "acquires ", "to acquire",
    "stake sale", "to raise ₹", "fundraise",
    "order win", "deal win", "order book of",
    # Analyst actions
    "target price", "price target", "target of ₹",
    "rating upgrade", "rating downgrade", "initiates coverage",
    "raises target", "cuts target",
    # Policy
    "rbi rate", "repo rate", "monetary policy committee",
    "sebi order", "sebi circular", "sebi penalty", "sebi ban",
    "sebi new rule",
    # Flows
    "fii net buy", "fii net sell", "fii data", "fii flow",
    "dii net buy", "dii net sell", "promoter stake",
    # Sector data
    "auto sales", "vehicle sales", "gst collection",
    "sector outlook", "industry outlook",
]

# ─── GLOBAL INDEX KEYWORDS — only include global news with these ──────────────
GLOBAL_INDEX_KEYWORDS = [
    "s&p 500", "s&p500", "dow jones", "nasdaq", "ftse", "nikkei",
    "hang seng", "dax", "global markets", "wall street",
    "us markets", "asian markets", "european markets",
    "federal reserve", "fed rate", "us gdp", "us inflation",
    "crude oil", "brent",
    "dollar index",
    "china economy", "us economy",
    # Gold/Silver only if India-specific impact
    "mcx gold", "sovereign gold bond", "gold etf india",
    "gold india", "jewellery stock", "gold import",
]

# ─── CRYPTO PRIORITY — only major crypto news ────────────────────────────────
CRYPTO_PRIORITY = [
    "bitcoin", "btc", "ethereum", "eth",
    "crypto regulation", "crypto india", "sebi crypto", "rbi crypto",
    "virtual asset", "crypto law", "crypto tax",
    "bitcoin etf", "ethereum etf", "crypto ban",
    "binance", "coinbase",  # major exchange news only
]

# ─── FILTERS ─────────────────────────────────────────────────────────────────
INDIA_MUST = [
    "nifty","sensex","bse","nse","sebi","rbi","rupee","inr","india","indian",
    "nifty bank","banknifty","nifty it","nifty pharma","nifty auto","nifty fmcg",
    "nifty metal","nifty energy","nifty realty","nifty infra","nifty midcap",
    "nifty smallcap","repo rate","mpc","fii","dii","fpi","dalal street",
    "ipo india","nse listing","bse listing","muhurat trading",
]
GLOBAL_IMPACT = [
    "crude oil","brent","oil price","opec","federal reserve","fed rate","fomc",
    "dollar index","gold price","silver price","china steel","china demand",
    "iron ore","us tech earnings","us inflation","us gdp","rupee dollar",
]
GLOBAL_BLOCK = [
    "white house","congress","senate","democrat","republican","trump","biden",
    "ukraine","russia","nato","pentagon","eu parliament","brexit",
    "nfl","nba","premier league","oscar","celebrity","hollywood",
    # US individual stocks — not relevant for India
    "riot platforms","cleanspark","applied digital","keel infrastructure",
    "strategy bitcoin","microstrategy","coinbase stock","tesla stock",
    "apple stock","nvidia stock","microsoft stock",
    # Generic US market noise
    "wall street thinks","wall street says","analyst says buy",
    "outperform rating","buy the dip on",
]
CRYPTO_KEYWORDS = [
    "bitcoin","btc","ethereum","eth","crypto","cryptocurrency","blockchain",
    "defi","nft","binance","coinbase","xrp","solana","dogecoin","virtual asset",
    "crypto india","sebi crypto","rbi crypto","crypto regulation",
]

# ─── SECTORS ─────────────────────────────────────────────────────────────────
SECTORS = {
    "Banking & Finance":       ["hdfc bank","icici bank","sbi","kotak bank","axis bank","yes bank","idfc first","bajaj finance","nifty bank","banknifty","bank nifty","rbl bank","indusind bank","federal bank","banking sector","bank stocks","repo rate cut","repo rate hike","credit growth","npa ratio","bank results","bank earnings","microfinance","nbfc sector"],
    "IT & Technology":         ["tcs","infosys","wipro","hcl tech","tech mahindra","nifty it","mphasis","persistent systems","coforge","ltimindtree","it sector","it stocks","it results","it rally","it index","software exports","technology sector","deep tech","data infra","semiconductor"],
    "Pharma & Healthcare":     ["sun pharma","dr reddy","cipla","divi's","biocon","lupin","alkem","glenmark","nifty pharma","usfda","fda approval","drug approval","pharma stocks","pharma sector","healthcare sector","clinical trial","pharmaceutical"],
    "Auto & EV":               ["maruti suzuki","tata motors","mahindra","bajaj auto","hero motocorp","tvs motor","eicher motors","ashok leyland","ola electric","nifty auto","ev sales","vehicle sales","auto sales","auto sector","auto stocks","passenger vehicle","commercial vehicle","two wheeler","electric vehicle","automobile"],
    "Infrastructure":          ["larsen toubro","l&t","irb infra","nhai","adani ports","knr constructions","pnc infratech","nifty infra","road project","highway project","infrastructure sector","infrastructure spending","smart city"],
    "Realty":                  ["dlf","godrej properties","oberoi realty","brigade","prestige","sobha","nifty realty","housing sales","real estate","property market","home loan","realty sector","housing sector"],
    "Energy & Power":          ["ntpc","power grid","adani green","tata power","torrent power","jsw energy","ongc","oil india","nifty energy","renewable energy","solar power","wind energy","power sector","electricity demand","energy sector"],
    "FMCG & Consumer":         ["hindustan unilever","hul","itc","nestle india","dabur","marico","godrej consumer","britannia","nifty fmcg","emami","colgate","varun beverages","fmcg sector","fmcg stocks","rural demand","volume growth","consumer staples"],
    "Metal & Mining":          ["tata steel","jsw steel","hindalco","vedanta","coal india","nmdc","jindal steel","sail","nifty metal","steel prices","iron ore","aluminium prices","copper prices","metal stocks","metal sector","mining sector"],
    "Telecom":                 ["reliance jio","bharti airtel","vodafone idea","vi telecom","nifty telecom","5g rollout","spectrum auction","telecom sector","telecom stocks","arpu growth","mobile tariff","telecom industry"],
    "Aviation":                ["indigo airlines","air india","spicejet","akasa air","aviation sector","passenger traffic","airline stocks","aviation fuel","airport capacity"],
    "Chemicals & Fertilizers": ["pidilite","aarti industries","navin fluorine","deepak nitrite","coromandel","chambal fertilizers","upl","specialty chemical","fertilizer stocks","chemical sector","agrochemical"],
    "Agriculture & Food":      ["kaveri seed","pi industries","dhanuka agri","rallis india","msp increase","kharif crop","rabi crop","monsoon progress","food inflation","agri stocks","crop output","agriculture sector","agri sector"],
    "Defence & Aerospace":     ["hal","bhel","bel","cochin shipyard","mazagon dock","garden reach","drdo","defence stocks","defence order","defence export","defence sector","aerospace sector"],
    "Insurance":               ["lic","sbi life","hdfc life","icici lombard","star health","max life","new india assurance","irda","insurance sector","insurance stocks","premium growth"],
    "Logistics & Shipping":    ["blue dart","delhivery","gati","allcargo","container corporation","concor","transport corporation","logistics sector","freight rates","logistics stocks","shipping sector"],
    "Textiles & Apparel":      ["raymond","arvind mills","welspun india","trident","vardhman textile","page industries","textile export","apparel sector","cotton prices","textile sector"],
    "Media & Entertainment":   ["zee entertainment","sun tv","network18","pvr inox","ott platform","streaming revenue","multiplex","box office collection","media sector"],
    "Education":               ["career point","mt educare","neet result","jee result","edtech sector","online education","education sector"],
    "Gems & Jewellery":        ["titan company","kalyan jewellers","pc jeweller","senco gold","muthoot finance","manappuram finance","gold loan","jewellery stocks","gems sector"],
    "Hospitality & Tourism":   ["indian hotels","lemon tree","mahindra holidays","irctc","eih hotels","hotel occupancy","travel demand","hospitality sector","tourism sector"],
    "Retail":                  ["dmart","avenue supermarts","shoppers stop","trent","v-mart","reliance retail","retail sector","consumer spending","retail stocks"],
}
SECTOR_EMOJI = {
    "Banking & Finance":"🏦","IT & Technology":"💻","Pharma & Healthcare":"💊",
    "Auto & EV":"🚗","Infrastructure":"🏗️","Realty":"🏘️","Energy & Power":"⚡",
    "FMCG & Consumer":"🛒","Metal & Mining":"🏭","Telecom":"📡","Aviation":"✈️",
    "Chemicals & Fertilizers":"🧪","Agriculture & Food":"🌾","Defence & Aerospace":"🛡️",
    "Insurance":"🏥","Logistics & Shipping":"🚢","Textiles & Apparel":"👗",
    "Media & Entertainment":"🎬","Education":"🏫","Gems & Jewellery":"💎",
    "Hospitality & Tourism":"🏨","Retail":"🏪",
}
STOCK_NAMES = [
    "reliance","tcs","hdfc","infosys","icici","sbi","hindustan unilever","hul",
    "itc","kotak","bajaj finance","larsen","l&t","titan","asian paints","maruti",
    "sun pharma","wipro","hcl","ultratech","nestle","adani","tata motors",
    "tata steel","power grid","ntpc","ongc","coal india","bajaj auto",
    "hero motocorp","britannia","dabur","cipla","dr reddy","divis","eicher",
    "indusind","axis bank","yes bank","idfc","jsw steel","hindalco","vedanta",
    "grasim","tech mahindra","airtel","jio","indigo","zomato","paytm",
    "nykaa","delhivery","irctc","lic","blinkit","eternal","swiggy",
]

# article type tags
TYPE_GROWTH  = "🚀 Growth"
TYPE_RISK    = "📉 Risk"
TYPE_OPP     = "🎯 Opportunity"
TYPE_GENERIC = "📰 General"

# ─── GEMINI HELPERS ───────────────────────────────────────────────────────────
def gemini_call(prompt, max_tokens=1500):
    try:
        resp = requests.post(
            GEMINI_URL,
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens}},
            timeout=45
        )
        if resp.status_code == 200:
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        print(f"[WARN] Gemini {resp.status_code}: {resp.text[:100]}")
        return None
    except Exception as e:
        print(f"[WARN] Gemini error: {e}")
        return None

def complete_sentences(t):
    if not t: return t
    last = max(t.rfind('.'), t.rfind('!'), t.rfind('?'))
    return t[:last+1].strip() if last > 0 else t.strip()

def classify_article(title, summary):
    """Reliable plain string classification."""
    t = title.lower()

    # RISK — negative events
    risk_words = [
        "misses estimate","profit falls","profit drops","revenue falls",
        "revenue declines","downgrade","target cut","target reduced",
        "fraud","probe","investigation","penalty","ban on","default",
        "npa rises","guidance cut","write-off","impairment",
        "plunges","crashes","slumps","lower circuit",
        "stock falls","shares fall","shares drop","shares plunge",
        "shares crash","shares decline","stock drops","stock plunges",
        "reports loss","net loss","loss widens","loss of ₹",
        "fii selling","fii net sell","fii outflow",
        "sebi ban","sebi penalty","sebi probe",
    ]
    if any(w in t for w in risk_words): return TYPE_RISK

    # GROWTH — positive events with specific action
    growth_words = [
        "beats estimate","profit jumps","profit rises","profit grows",
        "revenue grows","revenue rises","record profit","record revenue",
        "record quarterly","record q1","record q2","record q3","record q4",
        "all-time high","52-week high","52 week high",
        "order win","deal win","new contract",
        "upgrade","target raised","target hiked","target increased",
        "expands capacity","new plant","commercial production",
        "shares jump","shares surge","shares rise","shares rally",
        "stock jumps","stock surges","stock rises","stock rallies",
        "fii buying","fii net buy","fii inflow",
        "margin expansion","record mined","record output","record production",
        "record sales","record dispatch","record exports","record supplies",
        "rebounds ₹","rebounds $","jumps ₹","surges ₹",
        "market leader","undisputed leader","gains market share",
        "supplies rise","output rises","exports rise","volumes rise",
    ]
    if any(w in t for w in growth_words): return TYPE_GROWTH

    # OPPORTUNITY — actionable upcoming events
    opp_words = [
        "files ipo","ipo papers","ipo opens","ipo allotment","ipo listing",
        "drhp","files drhp","qip","rights issue",
        "buyback","dividend declared","dividend of ₹","interim dividend",
        "merger announced","acquisition of","to acquire","acquires ",
        "to raise ₹","stake sale","fundraise",
        "raises stake","sells stake","to sell stake","sell ₹",
        "sector outlook","industry outlook",
        "initiates coverage","q1 preview","q2 preview","earnings preview",
        "ipo wave","ipo pipeline","ipo to raise",
        "analyst upgrades","sets target","launches new fund",
        "sell up to $","to sell $","to sell ₹",
    ]
    if any(w in t for w in opp_words): return TYPE_OPP

    # DATA — sector/macro data points
    data_words = [
        "auto sales","vehicle sales","two-wheeler sales","ev sales",
        "gst collection","iip data","cpi data","wpi data",
        "fii net","dii net","fii data","dii data",
        "cement dispatch","steel output","power consumption",
        "exports rise","exports grow","exports volumes","exports value",
        "supplies rise","supplies grow","production rises",
        "coffee exports","textile exports","pharma exports",
        "q1 update","q1 growth","q1 volumes","q1 output",
        "june sales","july sales","monthly data",
    ]
    if any(w in t for w in data_words): return "📊 Data"

    return TYPE_GENERIC

def gemini_summarize(title, raw_text, bucket="india"):
    context = (
        "cryptocurrency and blockchain markets"
        if bucket == "crypto" else
        "global financial developments impacting Indian stock market"
        if bucket == "global" else
        "Indian stock market — focus on sector impact, investment opportunity, or risk"
    )
    prompt = f"""You are a sharp financial analyst summarizing news for Indian retail stock market investors.

Article Title: {title}
Article Content: {raw_text[:3000]}

Write TWO summaries. Each must have EXACTLY 5 complete sentences. Do NOT cut any sentence short.

ENGLISH SUMMARY rules:
- State what happened with exact numbers, percentages, company names
- Explain WHY it happened (cause/reason)
- Explain the IMPACT on {context}
- Mention if this is a buying/selling opportunity or a risk to watch
- Use plain simple English — no jargon

MARATHI SUMMARY rules:
- Translate exact same content into simple conversational Marathi
- Write as if explaining to a friend — every sentence must be complete
- MUST have exactly 5 full Marathi sentences

Reply ONLY in this format — nothing before or after:
ENGLISH_SUMMARY: [exactly 5 complete English sentences here]
MARATHI_SUMMARY: [exactly 5 complete Marathi sentences here]"""

    text = gemini_call(prompt, 1800)
    if not text:
        return None, None

    eng, mar = "", ""
    if "ENGLISH_SUMMARY:" in text and "MARATHI_SUMMARY:" in text:
        eng = text.split("ENGLISH_SUMMARY:")[1].split("MARATHI_SUMMARY:")[0].strip()
        mar = text.split("MARATHI_SUMMARY:")[1].strip()
    elif "ENGLISH_SUMMARY:" in text:
        eng = text.split("ENGLISH_SUMMARY:")[1].strip()
    else:
        eng = text.strip()

    eng = complete_sentences(re.sub(r'\*+', '', eng).strip())
    mar = complete_sentences(re.sub(r'\*+', '', mar).strip())

    # retry Marathi if still missing or too short
    if len(mar) < 100 and eng:
        time.sleep(2)
        mar_text = gemini_call(
            f"""Translate the following financial news summary into simple conversational Marathi.
Write EXACTLY 5 complete Marathi sentences. Each sentence must end with a full stop.
Do NOT stop in the middle. Reply with ONLY the Marathi text.

English: {eng}""", 900)
        if mar_text:
            mar = complete_sentences(re.sub(r'\*+', '', mar_text).strip())

    return eng, mar

def gemini_market_intelligence(india, glbl, crypto):
    """Generate flash summary only. Sentiment is rule-based."""
    titles = "\n".join([f"- {a['title']}" for a in (india + glbl + crypto)[:35]])
    prompt = f"""You are a senior Indian stock market analyst giving a live briefing.

Today's headlines from Indian markets:
{titles}

Write a FLASH SUMMARY in exactly 3 complete sentences.
- Cover the OVERALL market picture from ALL the headlines above — not just one article
- Mention specific sectors, companies, or events that matter
- Tell investors what action to take or what to watch
- Each sentence MUST be fully complete — never end mid-sentence
- Do not use asterisks, bullets, or any formatting
- Write as one flowing paragraph of 3 sentences

Reply with ONLY the 3 sentences. Nothing else."""

    text = gemini_call(prompt, 600)
    if not text: return {"flash": "", "watchlist": []}
    # ensure complete sentences only
    flash = complete_sentences(re.sub(r'\*+', '', text).strip())
    return {"flash": flash, "watchlist": []}

def gemini_weekly_digest(all_week_titles):
    """Generate weekly wrap summary."""
    titles = "\n".join([f"- {t}" for t in all_week_titles[:50]])
    prompt = f"""You are an Indian stock market analyst writing a weekly summary for retail investors.

This week's major headlines:
{titles}

Write a weekly digest with:
1. TOP 5 stories of the week — each with 2 sentence explanation
2. SECTOR WINNERS this week — top 3 sectors that did well and why
3. SECTOR LOSERS this week — top 2 sectors that struggled and why
4. STOCKS IN FOCUS — 5 stocks that were most talked about
5. OUTLOOK for next week — what to watch, upcoming events

Reply in this format:
WEEKLY_TOP5:
1. [headline] — [2 sentence explanation]
2. [headline] — [2 sentence explanation]
3. [headline] — [2 sentence explanation]
4. [headline] — [2 sentence explanation]
5. [headline] — [2 sentence explanation]
SECTOR_WINNERS: [3 sectors with brief reason]
SECTOR_LOSERS: [2 sectors with brief reason]
STOCKS_IN_FOCUS: [5 stock names with brief note]
WEEKLY_OUTLOOK: [3 sentences about next week]"""

    return gemini_call(prompt, 1500)

# ─── ARTICLE FETCHER ──────────────────────────────────────────────────────────
def fetch_article_text(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(url, headers=headers, timeout=12)
        t = re.sub(r'<script[^>]*>.*?</script>', ' ', r.text, flags=re.DOTALL)
        t = re.sub(r'<style[^>]*>.*?</style>', ' ', t, flags=re.DOTALL)
        t = re.sub(r'<[^>]+>', ' ', t)
        t = re.sub(r'\s+', ' ', t).strip()
        return t[:4500] if len(t) > 300 else ""
    except:
        return ""

def build_article(source, title, rss, link, bucket):
    full = fetch_article_text(link)
    content = full if len(full) > 300 else rss
    if not content or len(content) < 50:
        content = title + ". " + rss
    print(f"  -> [{bucket.upper()}] {title[:55]}...")
    eng, mar = gemini_summarize(title, content, bucket)
    time.sleep(3)
    if not eng:
        eng = clean_rss(rss)[:600] or "Summary not available."
        mar = ""
    article_type = classify_article(title, eng)
    return {
        "source":  source, "title": title, "eng": eng, "mar": mar,
        "link":    link,   "sectors": detect_sectors(title, eng),
        "stocks":  detect_stocks(title, eng), "bucket": bucket, "type": article_type,
    }

def is_india_news(title, summary=""):
    text = (title + " " + summary).lower()
    if any(b in text for b in GLOBAL_BLOCK) and not any(k in text for k in INDIA_MUST):
        return False
    return any(k in text for k in INDIA_MUST)

def is_global_impact(title, summary=""):
    text = (title + " " + summary).lower()
    if any(b in text for b in GLOBAL_BLOCK):
        return False
    return any(k in text for k in GLOBAL_INDEX_KEYWORDS) or any(k in text for k in GLOBAL_IMPACT)

def is_crypto(title, summary=""):
    text = (title + " " + summary).lower()
    return any(k in text for k in CRYPTO_PRIORITY)

def detect_sectors(title, summary=""):
    title_lower = title.lower()
    matched = []
    for s, kws in SECTORS.items():
        if any(kw in title_lower for kw in kws):
            matched.append(s)
        if len(matched) >= 2:
            break
    return matched

def detect_stocks(title, summary=""):
    text = (title + " " + summary).lower()
    return [s.title() for s in STOCK_NAMES if re.search(r'\b' + re.escape(s) + r'\b', text)]

def clean_rss(text):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()

def article_quality_score(title, summary, source):
    """Score article quality 0-100. Min 45 to include."""
    score = 0
    text = (title + " " + summary).lower()

    # Factor 1 — Information value (0-40)
    has_numbers = bool(re.search(r'₹[\d,]+|[\d,]+\s*crore|[\d.]+%|\$[\d,]+', title + " " + summary))
    # Factor 2 — Has company + event (0-15)
    quality_companies = ["reliance","tcs","hdfc","infosys","icici","sbi","hul","itc","kotak",
                         "bajaj","larsen","l&t","titan","maruti","sun pharma","wipro","hcl",
                         "ntpc","ongc","coal india","adani","tata","carlsberg","policybazaar",
                         "bpcl","hindustan zinc","zee","m&m","sterlite","airtel","indigo","irctc"]
    quality_events = ["ipo","result","acquisition","merger","stake","order","deal","upgrade",
                      "downgrade","dividend","buyback","listing","penalty","fundraise","qip"]
    if any(c in t for c in quality_companies) and any(e in t for e in quality_events):
        score += 15
    if has_forward:        score += 10

    # Factor 2 — Market impact (0-30)
    high_impact  = any(p in text for p in ["q1 result","q2 result","q3 result","q4 result","net profit","revenue","upgrade","downgrade","order win","ipo","merger","acquisition","fii","dii","rbi","sebi order","sebi penalty"])
    mid_impact   = any(p in text for p in ["sector","outlook","policy","capex","dividend","buyback","stake","stake sale","qip"])
    low_impact   = any(p in text for p in ["global","crude","oil","gold","dollar"])
    if high_impact:  score += 30
    elif mid_impact: score += 20
    elif low_impact: score += 10

    # Factor 3 — Source credibility (0-20)
    src_scores = {"Livemint": 20, "Hindu BusinessLine": 20, "Business Standard": 18,
                  "CNBC TV18": 15, "Moneycontrol": 15, "Economic Times": 10,
                  "NDTV Profit": 10, "ET Stocks": 8, "Google News India": 8}
    score += src_scores.get(source, 10)

    # Factor 4 — Freshness signals (0-10)
    if any(w in text for w in ["today","this week","this month","q1 fy","q2 fy","q3 fy","q4 fy","2026","2027"]):
        score += 10

    return min(100, score)

def get_article_fingerprint(title):
    """Kept for compatibility — calls get_dedup_key."""
    return get_dedup_key(title)

def similar_title(t1, t2):
    """Check if two titles are similar — used in dedup."""
    w1 = set(re.sub(r'[^a-z0-9 ]', '', t1.lower()).split())
    w2 = set(re.sub(r'[^a-z0-9 ]', '', t2.lower()).split())
    if not w1 or not w2: return False
    return len(w1 & w2) / min(len(w1), len(w2)) > 0.55

def should_skip(title):
    """Plain string check — no regex, reliable."""
    t = title.lower()
    for s in SKIP_LIST:
        if s in t:
            return True
    # Skip "N things/stocks/reasons" — simple number + word check
    words = t.split()
    for i, w in enumerate(words[:-1]):
        if w.isdigit() and int(w) >= 3:
            next_w = words[i+1].rstrip('s,.:')
            if next_w in ["thing","stock","reason","tip","pick","way","point","sector","fund"]:
                return True
    return False

def is_priority(title, summary=""):
    """Plain string check — always include these."""
    t = (title + " " + summary).lower()
    for p in PRIORITY_LIST:
        if p in t:
            return True
    return False

def get_dedup_key(title):
    """Extract (company, event) key for dedup."""
    t = title.lower()
    dedup_companies = [
        "carlsberg","reliance","tcs","hdfc","infosys","icici","sbi","hul","itc",
        "kotak","bajaj","larsen","l&t","titan","maruti","sun pharma","wipro","hcl",
        "ultratech","nestle","adani","tata motors","tata steel","power grid","ntpc",
        "ongc","coal india","hero motocorp","britannia","dabur","cipla","dr reddy",
        "divis","eicher","indusind","axis bank","yes bank","jsw steel","hindalco",
        "vedanta","tech mahindra","airtel","jio","indigo","zomato","irctc","lic",
        "policybazaar","paytm","nykaa","delhivery","bpcl","hindustan zinc","zee",
        "m&m","mahindra","sterlite","functional foods","devson","csm tech","ratnadeep",
    ]
    dedup_events = [
        "ipo","qip","drhp","results","acquisition","merger","stake sale",
        "order win","deal win","upgrade","downgrade","dividend","buyback",
        "rights issue","listing","penalty","circular","fundraise",
    ]
    company = next((c for c in dedup_companies if c in t), None)
    event   = next((e for e in dedup_events if e in t), None)
    return (company, event)

def is_duplicate(title, seen_keys, seen_titles):
    """Two-layer dedup — fast and reliable."""
    stopwords = {"the","a","an","in","of","to","for","on","at","by","as","is","it",
                 "and","or","but","not","with","from","has","have","was","were","be","been",
                 "its","that","this","are","will","can","do","into","up","after","before"}
    t_words = set(re.sub(r'[^a-z0-9 ]','',title.lower()).split()) - stopwords
    # Layer 1 — word overlap at 50% threshold
    for st in seen_titles:
        st_words = set(re.sub(r'[^a-z0-9 ]','',st.lower()).split()) - stopwords
        if len(t_words) >= 3 and len(st_words) >= 3:
            overlap = len(t_words & st_words) / min(len(t_words), len(st_words))
            if overlap >= 0.50:
                return True
    # Layer 2 — company + event fingerprint
    key = get_dedup_key(title)
    if key[0] and key[1] and key in seen_keys:
        return True
    return False

def article_quality_score(title, summary, source):
    """Score article 0-100. Min 45 required."""
    score = 0
    t = (title + " " + summary).lower()

    # Factor 1 — Has specific numbers (0-15)
    if re.search(r'₹[\d,]+|[\d,]+\s*crore|[\d.]+%|\$[\d,.]+\s*(million|billion)', title + " " + summary):
        score += 15

    # Factor 2 — Has company + event (0-15)
    dedup_companies = ["reliance","tcs","hdfc","infosys","icici","sbi","hul","itc","kotak",
                       "bajaj","larsen","l&t","titan","maruti","sun pharma","wipro","hcl",
                       "ntpc","ongc","coal india","adani","tata","carlsberg","policybazaar",
                       "bpcl","hindustan zinc","zee","m&m","sterlite","airtel","indigo","irctc"]
    dedup_events = ["ipo","result","acquisition","merger","stake","order","deal","upgrade",
                    "downgrade","dividend","buyback","listing","penalty","fundraise","qip"]
    if any(c in t for c in dedup_companies) and any(e in t for e in dedup_events):
        score += 15

    # Factor 3 — Forward looking (0-10)
    if any(w in t for w in ["guidance","outlook","forecast","target","preview","next quarter","estimate"]):
        score += 10

    # Factor 4 — Market impact type (0-30)
    if any(p in t for p in ["result","net profit","revenue","upgrade","downgrade","ipo","merger","acquisition","fii","rbi","sebi order","sebi penalty"]):
        score += 30
    elif any(p in t for p in ["sector","policy","capex","dividend","buyback","stake","qip"]):
        score += 20
    elif any(p in t for p in ["global","crude","oil","gold","dollar"]):
        score += 10

    # Factor 5 — Source credibility (0-20)
    src_score = {"Livemint":20,"Hindu BusinessLine":20,"Business Standard":18,
                 "CNBC TV18":15,"Moneycontrol":15,"Economic Times":10,
                 "NDTV Profit":10,"ET Stocks":8,"Google News India":8}
    score += src_score.get(source, 10)

    # Factor 6 — Freshness (0-10)
    if any(w in t for w in ["today","this week","q1 fy","q2 fy","q3 fy","q4 fy","2026","2027","fy27","fy26"]):
        score += 10

    return min(100, score)

def fetch_all_news():
    india, glbl, crypto = [], [], []
    seen_titles = []
    seen_keys   = set()
    total_india = 0

    # ── India — tiered budget fetching ──────────────────────────────────────
    for source, url, budget in INDIA_FEEDS:
        if total_india >= TOTAL_INDIA_BUDGET:
            break
        try:
            # Use requests with browser headers for Google News and feedburner
            if "news.google.com" in url or "feedburner" in url:
                resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}, timeout=12)
                feed = feedparser.parse(resp.content)
            else:
                feed = feedparser.parse(url)

            source_count   = 0
            priority_extra = []
            entries_checked = 0

            for e in feed.entries:
                if entries_checked >= 20:
                    break
                if source_count >= budget and total_india >= TOTAL_INDIA_BUDGET:
                    break
                entries_checked += 1
                t = e.get("title", "").strip()
                r = clean_rss(e.get("summary", e.get("description", "")))
                l = e.get("link", "").strip()
                if not t or not l:
                    continue

                # dedup check
                if is_duplicate(t, seen_keys, seen_titles):
                    continue

                article_is_priority = is_priority(t, r)
                # Skip list is ABSOLUTE — bad format articles are never included
                # regardless of whether they match priority patterns
                if should_skip(t):
                    continue

                # India keyword check for non-tier1 sources
                is_tier1 = source in TIER1_SOURCES
                if not is_tier1 and not is_india_news(t, r):
                    continue

                # Quality score check — priority articles bypass this
                qscore = article_quality_score(t, r, source)
                if qscore < 45 and not article_is_priority:
                    continue

                if source_count < budget:
                    seen_titles.append(t)
                    _k = get_dedup_key(t)
                    if _k[0] and _k[1]: seen_keys.add(_k)
                    india.append(build_article(source, t, r, l, "india"))
                    source_count += 1
                    total_india  += 1
                elif article_is_priority and len(priority_extra) < 2:
                    seen_titles.append(t)
                    _k = get_dedup_key(t)
                    if _k[0] and _k[1]: seen_keys.add(_k)
                    priority_extra.append(build_article(source, t, r, l, "india"))
                    total_india += 1

            india.extend(priority_extra)
            print(f"  [{source}] {source_count} articles fetched")

        except Exception as ex:
            print(f"[WARN] {source}: {ex}")

    # sort: Growth first, Risk second, Opportunity third, Generic last
    order = {TYPE_GROWTH: 0, TYPE_RISK: 1, TYPE_OPP: 2, TYPE_GENERIC: 3}
    india.sort(key=lambda x: order.get(x["type"], 3))

    # ── Global — max 5, major index/macro only ───────────────────────────────
    glbl_count = 0
    for source, url in GLOBAL_FEEDS:
        if glbl_count >= 5:
            break
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                if glbl_count >= 5:
                    break
                t = e.get("title", "").strip()
                r = clean_rss(e.get("summary", e.get("description", "")))
                l = e.get("link", "").strip()
                if not t or not l:
                    continue
                if should_skip(t):
                    continue
                if is_duplicate(t, seen_keys, seen_titles):
                    continue
                if is_india_news(t, r):
                    seen_titles.append(t)
                    india.append(build_article(source, t, r, l, "india"))
                    glbl_count += 1
                elif is_global_impact(t, r):
                    seen_titles.append(t)
                    glbl.append(build_article(source, t, r, l, "global"))
                    glbl_count += 1
        except Exception as ex:
            print(f"[WARN] {source}: {ex}")

    # ── Crypto — max 3, Bitcoin/Ethereum/regulation only ────────────────────
    crypto_count = 0
    for source, url in CRYPTO_FEEDS:
        if crypto_count >= 3:
            break
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                if crypto_count >= 3:
                    break
                t = e.get("title", "").strip()
                r = clean_rss(e.get("summary", e.get("description", "")))
                l = e.get("link", "").strip()
                if not t or not l:
                    continue
                if should_skip(t):
                    continue
                if is_duplicate(t, seen_keys, seen_titles):
                    continue
                if is_crypto(t, r):
                    seen_titles.append(t)
                    crypto.append(build_article(source, t, r, l, "crypto"))
                    crypto_count += 1
        except Exception as ex:
            print(f"[WARN] {source}: {ex}")

    return india, glbl, crypto

# ─── ANALYTICS ────────────────────────────────────────────────────────────────
def get_trending_sectors(arts):
    sc = defaultdict(int)
    for a in arts:
        for s in a["sectors"]: sc[s] += 1
    return sorted(sc.items(), key=lambda x: x[1], reverse=True)[:5]

def get_top_stocks(arts):
    sc = defaultdict(int)
    for a in arts:
        for s in a["stocks"]: sc[s] += 1
    return sorted(sc.items(), key=lambda x: x[1], reverse=True)[:8]

def get_session_label():
    h = datetime.now(IST).hour
    if h < 12: return "🌅 Morning Digest — 8 AM"
    if h < 15: return "☀️ Afternoon Digest — 2 PM"
    return "🌆 Evening Digest — 6 PM"

def fetch_market_data():
    """Fetch live Nifty 50 and Sensex data from Yahoo Finance."""
    result = {"nifty": None, "sensex": None}
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        # Nifty 50
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?interval=1d&range=2d",
            headers=headers, timeout=10
        )
        if r.status_code == 200:
            data = r.json()["chart"]["result"][0]
            closes = data["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 2:
                prev, curr = closes[-2], closes[-1]
                chg = curr - prev
                pct = (chg / prev) * 100
                result["nifty"] = {"price": curr, "change": chg, "pct": pct}
    except Exception as e:
        print(f"[WARN] Nifty fetch: {e}")
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        # Sensex
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EBSESN?interval=1d&range=2d",
            headers=headers, timeout=10
        )
        if r.status_code == 200:
            data = r.json()["chart"]["result"][0]
            closes = data["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 2:
                prev, curr = closes[-2], closes[-1]
                chg = curr - prev
                pct = (chg / prev) * 100
                result["sensex"] = {"price": curr, "change": chg, "pct": pct}
    except Exception as e:
        print(f"[WARN] Sensex fetch: {e}")
    return result

def format_market_data(mdata):
    """Format Nifty/Sensex for Discord."""
    lines = []
    if mdata.get("nifty"):
        n = mdata["nifty"]
        arrow = "▲" if n["change"] >= 0 else "▼"
        emoji = "🟢" if n["change"] >= 0 else "🔴"
        lines.append(f"{emoji} **Nifty 50:** {n['price']:,.2f}  {arrow} {abs(n['change']):,.2f} pts ({n['pct']:+.2f}%)")
    if mdata.get("sensex"):
        s = mdata["sensex"]
        arrow = "▲" if s["change"] >= 0 else "▼"
        emoji = "🟢" if s["change"] >= 0 else "🔴"
        lines.append(f"{emoji} **Sensex:** {s['price']:,.2f}  {arrow} {abs(s['change']):,.2f} pts ({s['pct']:+.2f}%)")
    return "\n".join(lines) if lines else ""

def format_market_data_html(mdata):
    """Format Nifty/Sensex for Gmail HTML."""
    if not mdata.get("nifty") and not mdata.get("sensex"):
        return ""
    html = "<div style='background:#f5f5f5;padding:14px 18px;border-radius:8px;margin-bottom:18px;display:flex;gap:20px;flex-wrap:wrap;'>"
    if mdata.get("nifty"):
        n = mdata["nifty"]
        color = "#2e7d32" if n["change"] >= 0 else "#c62828"
        arrow = "▲" if n["change"] >= 0 else "▼"
        html += (f"<div><p style='margin:0;font-size:12px;color:#888;'>Nifty 50</p>"
                 f"<p style='margin:0;font-size:18px;font-weight:bold;color:#1a1a1a;'>{n['price']:,.2f}</p>"
                 f"<p style='margin:0;font-size:13px;color:{color};'>{arrow} {abs(n['change']):,.2f} ({n['pct']:+.2f}%)</p></div>")
    if mdata.get("sensex"):
        s = mdata["sensex"]
        color = "#2e7d32" if s["change"] >= 0 else "#c62828"
        arrow = "▲" if s["change"] >= 0 else "▼"
        html += (f"<div style='border-left:1px solid #ddd;padding-left:20px;'>"
                 f"<p style='margin:0;font-size:12px;color:#888;'>Sensex</p>"
                 f"<p style='margin:0;font-size:18px;font-weight:bold;color:#1a1a1a;'>{s['price']:,.2f}</p>"
                 f"<p style='margin:0;font-size:13px;color:{color};'>{arrow} {abs(s['change']):,.2f} ({s['pct']:+.2f}%)</p></div>")
    html += "</div>"
    return html

def calculate_sentiment(india, glbl):
    """Multi-factor rule-based sentiment scoring."""
    score = 50

    # Factor 1 — Article types (capped at ±4 per article)
    growth_count = sum(1 for a in india if a["type"] == TYPE_GROWTH)
    risk_count   = sum(1 for a in india if a["type"] == TYPE_RISK)
    opp_count    = sum(1 for a in india if a["type"] == TYPE_OPP)
    score += min(growth_count * 4, 16)   # max +16 from growth
    score -= min(risk_count * 4, 16)     # max -16 from risk
    score += min(opp_count * 2, 8)       # max +8 from opportunity
    # Dampen: too many Opportunity articles = noise, not signal
    if opp_count > 10: score -= 8

    # Factor 2 — Keyword signals in titles (max ±15 total)
    bullish_kw = ["record high","all time high","52 week high","beats estimate",
                  "upgrade","order win","profit jumps","guidance raised","fii net buy",
                  "fii inflow","record profit","record revenue","buyback"]
    bearish_kw = ["miss estimate","downgrade","fraud","probe","penalty",
                  "fii net sell","fii outflow","profit falls","revenue declines",
                  "guidance cut","default","npa rises"]
    all_titles = " ".join(a["title"].lower() for a in india + glbl)
    bull_hits = sum(1 for kw in bullish_kw if kw in all_titles)
    bear_hits = sum(1 for kw in bearish_kw if kw in all_titles)
    score += min(bull_hits * 3, 15)
    score -= min(bear_hits * 3, 15)

    # Factor 3 — FII/DII signal
    fii_titles = " ".join(a["title"].lower() for a in india)
    if any(k in fii_titles for k in ["fii net buy","fii buying","foreign inflow"]): score += 4
    if any(k in fii_titles for k in ["fii net sell","fii selling","foreign outflow"]): score -= 4
    if "dii net buy" in fii_titles: score += 2

    # Factor 4 — Global cues
    glbl_titles = " ".join(a["title"].lower() for a in glbl)
    if any(k in glbl_titles for k in ["crude falls","oil falls","fed holds"]): score += 3
    if any(k in glbl_titles for k in ["crude rises","oil rises","fed hikes"]): score -= 3

    # Final cap — realistic range
    score = max(20, min(85, int(score)))  # never show 0 or 100 — unrealistic
    if score >= 75:   label = "VERY BULLISH"
    elif score >= 60: label = "BULLISH"
    elif score >= 45: label = "NEUTRAL"
    elif score >= 30: label = "BEARISH"
    else:             label = "VERY BEARISH"
    return score, label

def get_market_data():
    """Fetch live Nifty 50 and Sensex data."""
    result = {"nifty": None, "sensex": None}
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        for key, symbol in [("nifty", "%5ENSEI"), ("sensex", "%5EBSESN")]:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                data  = r.json()
                meta  = data["chart"]["result"][0]["meta"]
                price = meta.get("regularMarketPrice", 0)
                prev  = meta.get("chartPreviousClose", meta.get("previousClose", price))
                change = price - prev
                pct    = (change / prev * 100) if prev else 0
                arrow  = "▲" if change >= 0 else "▼"
                result[key] = {
                    "price":  f"{price:,.2f}",
                    "change": f"{change:+,.2f}",
                    "pct":    f"{pct:+.2f}%",
                    "arrow":  arrow,
                    "up":     change >= 0,
                }
    except Exception as e:
        print(f"[WARN] Market data: {e}")
    return result

def sentiment_color(score):
    if score >= 80: return "#1b5e20"
    if score >= 60: return "#2e7d32"
    if score >= 40: return "#f57f17"
    if score >= 20: return "#c62828"
    return "#7f0000"

def sentiment_emoji(score):
    if score >= 80: return "🟢🟢"
    if score >= 60: return "🟢"
    if score >= 40: return "🟡"
    if score >= 20: return "🔴"
    return "🔴🔴"

def truncate_flash(text, max_chars=280):
    """Truncate flash at last complete sentence within max_chars."""
    if not text: return ""
    text = re.sub(r'\*+', '', text).strip()
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last = max(truncated.rfind('.'), truncated.rfind('!'), truncated.rfind('?'))
    return truncated[:last+1].strip() if last > 0 else truncated.strip()

def watchlist_emoji(action):
    action = action.upper()
    if "BUY"   in action: return "🟢"
    if "WATCH" in action: return "🟡"
    if "AVOID" in action: return "🔴"
    return "⚪"

# ─── DISCORD ──────────────────────────────────────────────────────────────────
def discord_post(text):
    if not text.strip():
        return
    requests.post(DISCORD_WEBHOOK_URL, json={"content": text})
    time.sleep(0.6)

def send_discord(india, glbl, crypto, intel, mdata, is_weekly=False):
    now_str  = datetime.now(IST).strftime("%d %B %Y, %I:%M %p IST")
    session  = "📅 Weekly Digest — Sunday" if is_weekly else get_session_label()
    all_arts = india + glbl + crypto
    sectors  = get_trending_sectors(all_arts)
    stocks   = get_top_stocks(all_arts)
    score    = intel["score"]
    label    = intel["label"]
    sem      = sentiment_emoji(score)

    # Header
    discord_post(
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **INDIAN MARKET + CRYPTO DIGEST**  |  {SOURCE_TAG}\n"
        f"{session} | 🕐 {now_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    # Flash — plain text
    if intel["flash"]:
        discord_post(f"⚡ **WHAT YOU NEED TO KNOW RIGHT NOW**\n{intel['flash']}")

    # Nifty/Sensex live data
    market_line = format_market_data(mdata)
    if market_line:
        discord_post(f"**📊 MARKET DATA**\n{market_line}")

    # Top stocks only
    if stocks:
        discord_post(
            "**📌 MOST MENTIONED STOCKS TODAY**\n  " +
            " | ".join(f"**{s}** ({c}x)" for s, c in stocks)
        )

    # Articles
    def post_section(header, articles):
        if not articles:
            return
        discord_post(f"\n{'━'*36}\n{header} ({len(articles)} stories)\n{'━'*36}")
        for i, a in enumerate(articles, 1):
            sector_line = "  ".join(
                f"{SECTOR_EMOJI.get(s, '📌')} `{s}`"
                for s in a["sectors"][:3]
            ) if a["sectors"] else ""
            eng_text  = a['eng'] if a['eng'] else "Summary not available."
            mar_text  = a['mar'] if a['mar'] else ""
            title_txt = a['title'] if a['title'] else "No title"
            msg = (
                f"**{i}. {title_txt}**\n"
                f"📰 _{a['source']}_ | {a['type']}\n"
                + (f"{sector_line}\n" if sector_line else "")
                + f"\n🔵 **English:**\n{eng_text}\n\n"
                + (f"🟡 **मराठी:**\n{mar_text}\n\n" if mar_text else "")
                + f"🔗 {a['link']}"
            )
            try:
                discord_post(msg)
            except Exception as e:
                print(f"[WARN] Discord post failed for article {i}: {e}")

    post_section("🇮🇳  INDIA MARKET NEWS", india)
    post_section("🌐  GLOBAL NEWS IMPACTING INDIA", glbl)
    post_section("🪙  CRYPTO NEWS", crypto)

    # Total story count at bottom
    total = len(india) + len(glbl) + len(crypto)
    discord_post(
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 **Today's total: {len(india)} India · {len(glbl)} Global · {len(crypto)} Crypto = {total} stories**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    print(f"[OK] Discord: {len(india)} India | {len(glbl)} Global | {len(crypto)} Crypto")

# ─── GMAIL ────────────────────────────────────────────────────────────────────
def send_gmail(india, glbl, crypto, intel, mdata, is_weekly=False):
    now_str  = datetime.now(IST).strftime("%d %B %Y, %I:%M %p IST")
    session  = "Weekly Digest — Sunday" if is_weekly else get_session_label().replace("🌅","").replace("☀️","").replace("🌆","").strip()
    all_arts = india + glbl + crypto
    sectors  = get_trending_sectors(all_arts)
    stocks   = get_top_stocks(all_arts)
    score    = intel["score"]
    label    = intel["label"]
    sc       = sentiment_color(score)
    sem      = sentiment_emoji(score)
    subject  = f"[{SOURCE_TAG}] 📊 {label} | {session} | {datetime.now(IST).strftime('%d %b %Y')}"

    # Flash block
    flash_html = ""
    if intel["flash"]:
        flash_html = (
            f"<div style='background:#e8f5e9;border-left:4px solid #2e7d32;padding:14px 18px;"
            f"border-radius:6px;margin-bottom:18px;'>"
            f"<p style='margin:0 0 6px;font-weight:bold;font-size:14px;color:#1b5e20;'>"
            f"⚡ What You Need To Know Right Now</p>"
            f"<p style='margin:0;font-size:14px;color:#333;line-height:1.8;'>{intel['flash']}</p>"
            f"</div>"
        )

    # Sentiment block
    sent_html = ""  # replaced by live market data

    mdata_html = format_market_data_html(mdata)

    # Stocks block
    stk_html = ""
    if stocks:
        badges = "".join(
            f"<span style='background:#e8f0fe;color:#1a73e8;padding:4px 10px;border-radius:12px;"
            f"font-size:12px;margin:3px;display:inline-block;'>{s} <b>({c}x)</b></span>"
            for s, c in stocks
        )
        stk_html = (
            f"<div style='margin-bottom:18px;'>"
            f"<p style='font-weight:bold;font-size:14px;margin:0 0 8px;'>📌 Most Mentioned Stocks</p>"
            f"{badges}</div>"
        )

    def type_badge(article_type):
        colors = {TYPE_GROWTH:"#2e7d32", TYPE_RISK:"#c62828", TYPE_OPP:"#e65100", TYPE_GENERIC:"#546e7a"}
        return (f"<span style='background:{colors.get(article_type,'#546e7a')};color:white;"
                f"padding:1px 7px;border-radius:3px;font-size:10px;margin-right:4px;'>{article_type}</span>")

    def make_rows(articles, color):
        rows = ""
        for i, a in enumerate(articles, 1):
            sec_badges = "".join(
                f"<span style='background:{color};color:white;padding:1px 7px;border-radius:3px;"
                f"font-size:10px;margin-right:3px;'>{SECTOR_EMOJI.get(s,'')}{s[:14]}</span>"
                for s in a["sectors"][:3]
            )
            mar_block = (
                f"<div style='background:#fff8e1;border-left:4px solid #f9a825;padding:10px 14px;"
                f"margin-top:8px;border-radius:0 6px 6px 0;font-size:13px;color:#444;line-height:1.9;'>"
                f"<b style='color:#e65100;'>💡 मराठी:</b><br>{a['mar']}</div>"
            ) if a['mar'] else ""
            rows += (
                f"<tr style='border-bottom:1px solid #f2f2f2;'>"
                f"<td style='padding:16px 8px;font-weight:bold;color:#bbb;vertical-align:top;"
                f"font-size:14px;width:28px;'>{i}</td>"
                f"<td style='padding:16px 10px;'>"
                f"<div style='margin-bottom:7px;'>"
                f"<span style='background:#f5f5f5;color:#666;padding:2px 8px;border-radius:3px;"
                f"font-size:10px;margin-right:5px;'>{a['source']}</span>"
                f"{type_badge(a['type'])}{sec_badges}</div>"
                f"<a href='{a['link']}' style='font-size:16px;font-weight:bold;color:#1a1a1a;"
                f"text-decoration:none;line-height:1.5;display:block;margin-bottom:10px;'>{a['title']}</a>"
                f"<div style='background:#f0f4ff;border-left:4px solid {color};padding:10px 14px;"
                f"border-radius:0 6px 6px 0;font-size:13px;color:#333;line-height:1.9;margin-bottom:8px;'>"
                f"<b style='color:{color};'>💡 English:</b><br>{a['eng']}</div>"
                f"{mar_block}"
                f"<a href='{a['link']}' style='font-size:12px;color:{color};text-decoration:none;"
                f"margin-top:10px;display:inline-block;font-weight:bold;border:1px solid {color};"
                f"padding:4px 12px;border-radius:4px;'>📖 Read Full Article →</a>"
                f"</td></tr>"
            )
        return rows

    def section_block(title, articles, color):
        if not articles:
            return ""
        return (
            f"<div style='margin-bottom:30px;'>"
            f"<div style='background:{color};color:white;padding:13px 18px;border-radius:8px 8px 0 0;"
            f"font-weight:bold;font-size:15px;'>{title} "
            f"<span style='opacity:.8;font-size:12px;'>({len(articles)} stories)</span></div>"
            f"<table width='100%' cellspacing='0' style='border:1px solid #eee;border-top:none;"
            f"border-radius:0 0 8px 8px;'>{make_rows(articles, color)}</table></div>"
        )

    body = (
        f"<html><body style='font-family:Arial,sans-serif;max-width:780px;margin:auto;"
        f"background:#f0f2f5;padding:20px;'>"
        f"<div style='background:linear-gradient(135deg,#1a73e8,#0d47a1);color:white;padding:28px;"
        f"border-radius:12px 12px 0 0;text-align:center;'>"
        f"<h2 style='margin:0;font-size:24px;'>📊 Indian Market + Crypto Digest</h2>"
        f"<p style='margin:6px 0 0;opacity:.85;font-size:14px;'>{session} · {now_str}</p></div>"
        f"<div style='background:white;padding:26px;border-radius:0 0 12px 12px;"
        f"box-shadow:0 2px 16px rgba(0,0,0,.08);'>"
        f"{flash_html}{mdata_html}{stk_html}"
        f"{section_block('🇮🇳 India Market News', india, '#1a73e8')}"
        f"{section_block('🌐 Global News Impacting India', glbl, '#e65100')}"
        f"{section_block('🪙 Crypto News', crypto, '#6200ea')}"
        f"<hr style='border:none;border-top:1px solid #eee;margin:24px 0;'>"
        f"<p style='color:#555;font-size:12px;text-align:center;margin:0 0 8px;font-weight:bold;'>"
        f"📋 Today's total: {len(india)} India · {len(glbl)} Global · {len(crypto)} Crypto = {len(india)+len(glbl)+len(crypto)} stories</p>"
        f"<p style='color:#aaa;font-size:11px;text-align:center;margin:0;'>"
        f"Sources: Business Standard · Livemint · Hindu BusinessLine · CNBC TV18 · Financial Express · "
        f"Moneycontrol · Economic Times · Reuters India · CoinDesk · Decrypt<br>"
        f"Summaries & Intelligence powered by Gemini AI · Delivered at 8 AM, 2 PM, 6 PM IST"
        f"</p></div></body></html>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = GMAIL_RECEIVER
    msg.attach(MIMEText(body, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            s.sendmail(GMAIL_SENDER, GMAIL_RECEIVER, msg.as_string())
        print(f"[OK] Gmail sent to {GMAIL_RECEIVER}")
    except Exception as e:
        print(f"[ERROR] Gmail: {e}")

# ─── WEEKLY DIGEST ────────────────────────────────────────────────────────────
def send_weekly_digest(india, glbl, crypto, intel):
    """Send enhanced Sunday weekly digest."""
    print("[INFO] Generating weekly digest...")
    all_titles = [a["title"] for a in india + glbl + crypto]
    weekly_text = gemini_weekly_digest(all_titles)

    # post to Discord
    discord_post(
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 **WEEKLY MARKET WRAP — SUNDAY DIGEST**\n"
        f"🕐 {datetime.now(IST).strftime('%d %B %Y, %I:%M %p IST')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    if weekly_text:
        # split into chunks for Discord
        chunks = [weekly_text[i:i+1800] for i in range(0, len(weekly_text), 1800)]
        for chunk in chunks:
            discord_post(f"```\n{chunk}\n```")

    # also send regular digest
    send_discord(india, glbl, crypto, intel, is_weekly=True)
    send_gmail(india, glbl, crypto, intel, is_weekly=True)

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    now = datetime.now(IST)
    is_sunday = now.weekday() == 6
    is_weekly = is_sunday and now.hour == 8

    print(f"[START] {now.strftime('%d %b %Y %I:%M %p IST')} — Fetching + summarizing news...")
    india, glbl, crypto = fetch_all_news()
    print(f"[INFO] India: {len(india)} | Global: {len(glbl)} | Crypto: {len(crypto)}")

    # show source breakdown
    src_counts = defaultdict(int)
    for a in india:
        src_counts[a["source"]] += 1
    for src, cnt in src_counts.items():
        print(f"  {src}: {cnt}")

    types = defaultdict(int)
    for a in india:
        types[a["type"]] += 1
    print(f"[INFO] Types — {dict(types)}")

    trending = get_trending_sectors(india + glbl + crypto)
    if trending:
        print(f"[INFO] Trending: {', '.join(s for s, _ in trending)}")

    # Multi-factor rule-based sentiment
    score, label = calculate_sentiment(india, glbl)
    print(f"[INFO] Sentiment (multi-factor): {label} ({score}/100)")

    print("[INFO] Fetching live Nifty/Sensex data...")
    mdata = fetch_market_data()
    if mdata.get("nifty"):
        print(f"[INFO] Nifty: {mdata['nifty']['price']:,.2f} ({mdata['nifty']['pct']:+.2f}%)")
    if mdata.get("sensex"):
        print(f"[INFO] Sensex: {mdata['sensex']['price']:,.2f} ({mdata['sensex']['pct']:+.2f}%)")

    print("[INFO] Generating flash summary via Gemini...")
    intel_raw = gemini_market_intelligence(india, glbl, crypto)
    intel = {
        "score":     score,
        "label":     label,
        "flash":     intel_raw.get("flash", ""),
        "watchlist": [],
    }

    if is_weekly:
        send_weekly_digest(india, glbl, crypto, intel)
    else:
        send_discord(india, glbl, crypto, intel, mdata)
        send_gmail(india, glbl, crypto, intel, mdata)

    print("[DONE] All notifications sent.")

if __name__ == "__main__":
    main()
