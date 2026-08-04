"""
Peer Comparison Engine
Fetches same-quarter metrics for sector peers and ranks the subject company.

Output structure:
  {
    "peers_data": [
        {symbol, name, revenue_cr, pat_cr, ebitda_margin_pct, eps,
         revenue_growth_yoy, pat_growth_yoy, eps_growth_yoy}
    ],
    "rankings": {
        "revenue_growth": {"rank": 2, "total": 5, "value": 8.2},
        "pat_growth":     {"rank": 1, "total": 5, "value": 11.4},
        "ebitda_margin":  {"rank": 3, "total": 5, "value": 12.7},
        "eps_growth":     {"rank": 2, "total": 5, "value": 11.3},
    },
    "verdict": "ABOVE AVERAGE — Titan leads peers on PAT growth...",
    "sector": "Consumer/Jewellery"
  }
"""
from history_fetcher import get_history, fetch_from_yahoo

# ─── PEER GROUPS ─────────────────────────────────────────────────────────────
# Format: symbol → (sector_name, [peer_symbols_with_names])
# Each peer: (NSE_SYMBOL, display_name)

PEER_GROUPS = {
    # Consumer / Jewellery
    "TITAN":       ("Consumer / Jewellery",   [("KALYANKJIL","Kalyan Jewellers"), ("SENCO","Senco Gold"), ("TRENT","Trent"), ("PCJEWELLER","PC Jeweller")]),

    # IT Services
    "TCS":         ("IT Services",            [("INFY","Infosys"), ("WIPRO","Wipro"), ("HCLTECH","HCL Tech"), ("TECHM","Tech Mahindra")]),
    "INFY":        ("IT Services",            [("TCS","TCS"), ("WIPRO","Wipro"), ("HCLTECH","HCL Tech"), ("TECHM","Tech Mahindra")]),
    "WIPRO":       ("IT Services",            [("TCS","TCS"), ("INFY","Infosys"), ("HCLTECH","HCL Tech"), ("TECHM","Tech Mahindra")]),
    "HCLTECH":     ("IT Services",            [("TCS","TCS"), ("INFY","Infosys"), ("WIPRO","Wipro"), ("TECHM","Tech Mahindra")]),
    "TECHM":       ("IT Services",            [("TCS","TCS"), ("INFY","Infosys"), ("WIPRO","Wipro"), ("HCLTECH","HCL Tech")]),
    "LTIM":        ("IT Services",            [("TCS","TCS"), ("INFY","Infosys"), ("WIPRO","Wipro"), ("HCLTECH","HCL Tech")]),

    # Private Banks
    "HDFCBANK":    ("Private Banks",          [("ICICIBANK","ICICI Bank"), ("KOTAKBANK","Kotak Bank"), ("AXISBANK","Axis Bank"), ("INDUSINDBK","IndusInd Bank")]),
    "ICICIBANK":   ("Private Banks",          [("HDFCBANK","HDFC Bank"), ("KOTAKBANK","Kotak Bank"), ("AXISBANK","Axis Bank"), ("INDUSINDBK","IndusInd Bank")]),
    "KOTAKBANK":   ("Private Banks",          [("HDFCBANK","HDFC Bank"), ("ICICIBANK","ICICI Bank"), ("AXISBANK","Axis Bank"), ("INDUSINDBK","IndusInd Bank")]),
    "AXISBANK":    ("Private Banks",          [("HDFCBANK","HDFC Bank"), ("ICICIBANK","ICICI Bank"), ("KOTAKBANK","Kotak Bank"), ("INDUSINDBK","IndusInd Bank")]),

    # PSU Banks
    "SBIN":        ("PSU Banks",              [("BANKBARODA","Bank of Baroda"), ("PNB","Punjab National Bank"), ("CANBK","Canara Bank"), ("UNIONBANK","Union Bank")]),
    "BANKBARODA":  ("PSU Banks",              [("SBIN","SBI"), ("PNB","Punjab National Bank"), ("CANBK","Canara Bank")]),

    # FMCG
    "HINDUNILVR":  ("FMCG",                   [("ITC","ITC"), ("DABUR","Dabur"), ("MARICO","Marico"), ("BRITANNIA","Britannia")]),
    "ITC":         ("FMCG",                   [("HINDUNILVR","HUL"), ("DABUR","Dabur"), ("MARICO","Marico"), ("BRITANNIA","Britannia")]),
    "DABUR":       ("FMCG",                   [("HINDUNILVR","HUL"), ("ITC","ITC"), ("MARICO","Marico"), ("BRITANNIA","Britannia")]),
    "MARICO":      ("FMCG",                   [("HINDUNILVR","HUL"), ("ITC","ITC"), ("DABUR","Dabur"), ("BRITANNIA","Britannia")]),

    # Auto
    "MARUTI":      ("Automobiles",            [("TATAMOTORS","Tata Motors"), ("BAJAJ-AUTO","Bajaj Auto"), ("EICHERMOT","Eicher Motors"), ("HEROMOTOCO","Hero Moto")]),
    "TATAMOTORS":  ("Automobiles",            [("MARUTI","Maruti"), ("BAJAJ-AUTO","Bajaj Auto"), ("EICHERMOT","Eicher Motors"), ("HEROMOTOCO","Hero Moto")]),
    "BAJAJ-AUTO":  ("Automobiles",            [("MARUTI","Maruti"), ("TATAMOTORS","Tata Motors"), ("EICHERMOT","Eicher Motors"), ("HEROMOTOCO","Hero Moto")]),
    "EICHERMOT":   ("Automobiles",            [("MARUTI","Maruti"), ("TATAMOTORS","Tata Motors"), ("BAJAJ-AUTO","Bajaj Auto"), ("HEROMOTOCO","Hero Moto")]),

    # Pharma
    "SUNPHARMA":   ("Pharmaceuticals",        [("DRREDDY","Dr. Reddy's"), ("CIPLA","Cipla"), ("DIVISLAB","Divi's Labs"), ("AUROPHARMA","Aurobindo")]),
    "DRREDDY":     ("Pharmaceuticals",        [("SUNPHARMA","Sun Pharma"), ("CIPLA","Cipla"), ("DIVISLAB","Divi's Labs"), ("AUROPHARMA","Aurobindo")]),
    "CIPLA":       ("Pharmaceuticals",        [("SUNPHARMA","Sun Pharma"), ("DRREDDY","Dr. Reddy's"), ("DIVISLAB","Divi's Labs"), ("AUROPHARMA","Aurobindo")]),
    "DIVISLAB":    ("Pharmaceuticals",        [("SUNPHARMA","Sun Pharma"), ("DRREDDY","Dr. Reddy's"), ("CIPLA","Cipla"), ("AUROPHARMA","Aurobindo")]),

    # Oil & Gas
    "RELIANCE":    ("Oil & Gas / Conglomerate",[("ONGC","ONGC"), ("BPCL","BPCL"), ("IOC","IOC")]),
    "ONGC":        ("Oil & Gas",              [("RELIANCE","Reliance"), ("BPCL","BPCL"), ("IOC","IOC")]),
    "BPCL":        ("Oil & Gas",              [("RELIANCE","Reliance"), ("ONGC","ONGC"), ("IOC","IOC")]),

    # Cement
    "ULTRACEMCO":  ("Cement",                 [("SHREECEM","Shree Cement"), ("AMBUJACEM","Ambuja Cement"), ("ACC","ACC")]),
    "SHREECEM":    ("Cement",                 [("ULTRACEMCO","UltraTech"), ("AMBUJACEM","Ambuja Cement"), ("ACC","ACC")]),
    "AMBUJACEM":   ("Cement",                 [("ULTRACEMCO","UltraTech"), ("SHREECEM","Shree Cement"), ("ACC","ACC")]),

    # Steel / Metals
    "TATASTEEL":   ("Steel & Metals",         [("JSWSTEEL","JSW Steel"), ("HINDALCO","Hindalco"), ("SAIL","SAIL"), ("VEDL","Vedanta")]),
    "JSWSTEEL":    ("Steel & Metals",         [("TATASTEEL","Tata Steel"), ("HINDALCO","Hindalco"), ("SAIL","SAIL"), ("VEDL","Vedanta")]),
    "HINDALCO":    ("Steel & Metals",         [("TATASTEEL","Tata Steel"), ("JSWSTEEL","JSW Steel"), ("SAIL","SAIL"), ("VEDL","Vedanta")]),

    # Telecom
    "BHARTIARTL":  ("Telecom",                [("IDEA","Vodafone Idea"), ("TATACOMM","Tata Comms")]),

    # Power
    "NTPC":        ("Power",                  [("POWERGRID","Power Grid"), ("TATAPOWER","Tata Power"), ("ADANIGREEN","Adani Green"), ("JSWENERGY","JSW Energy")]),
    "TATAPOWER":   ("Power",                  [("NTPC","NTPC"), ("POWERGRID","Power Grid"), ("ADANIGREEN","Adani Green"), ("JSWENERGY","JSW Energy")]),

    # Consumer Durables
    "HAVELLS":     ("Consumer Durables",      [("VOLTAS","Voltas"), ("BLUESTAR","Blue Star"), ("CROMPTON","Crompton"), ("ORIENTELEC","Orient Electric")]),
    "VOLTAS":      ("Consumer Durables",      [("HAVELLS","Havells"), ("BLUESTAR","Blue Star"), ("CROMPTON","Crompton")]),

    # Paints
    "ASIANPAINT":  ("Paints",                 [("BERGEPAINT","Berger Paints"), ("KANSAINER","Kansai Nerolac"), ("INDIGO","Indigo Paints")]),
    "BERGEPAINT":  ("Paints",                 [("ASIANPAINT","Asian Paints"), ("KANSAINER","Kansai Nerolac"), ("INDIGO","Indigo Paints")]),

    # NBFCs
    "BAJFINANCE":  ("NBFC",                   [("BAJAJFINSV","Bajaj Finserv"), ("MUTHOOTFIN","Muthoot Finance"), ("CHOLAFIN","Chola Finance"), ("M&MFIN","M&M Finance")]),
    "CHOLAFIN":    ("NBFC",                   [("BAJFINANCE","Bajaj Finance"), ("BAJAJFINSV","Bajaj Finserv"), ("MUTHOOTFIN","Muthoot Finance")]),

    # PSU Infrastructure / Railways
    "RVNL":        ("PSU Infrastructure",     [("IRFC","IRFC"), ("IRCTC","IRCTC"), ("RITES","RITES"), ("RAILTEL","RailTel")]),
    "IRFC":        ("PSU Infrastructure",     [("RVNL","RVNL"), ("IRCTC","IRCTC"), ("RITES","RITES"), ("HUDCO","HUDCO")]),
    "IRCTC":       ("PSU Infrastructure",     [("RVNL","RVNL"), ("IRFC","IRFC"), ("RITES","RITES"), ("RAILTEL","RailTel")]),

    # Defence / Aerospace
    "HAL":         ("Defence",                [("BEL","BEL"), ("BEML","BEML"), ("MIDHANI","Midhani"), ("COCHINSHIP","Cochin Shipyard")]),
    "BEL":         ("Defence",                [("HAL","HAL"), ("BEML","BEML"), ("MIDHANI","Midhani"), ("COCHINSHIP","Cochin Shipyard")]),

    # Food Delivery / New Age Tech
    "ZOMATO":      ("New Age Tech",           [("NYKAA","Nykaa"), ("PAYTM","Paytm"), ("POLICYBZR","PolicyBazaar"), ("CARTRADE","CarTrade")]),
    "NYKAA":       ("New Age Tech",           [("ZOMATO","Zomato"), ("PAYTM","Paytm"), ("POLICYBZR","PolicyBazaar")]),

    # Electronics Manufacturing
    "DIXON":       ("Electronics Mfg",        [("AMBER","Amber Enterprises"), ("KAYNES","Kaynes Tech"), ("SYRMA","Syrma SGS"), ("PGEL","PG Electroplast")]),
    "AMBER":       ("Electronics Mfg",        [("DIXON","Dixon Tech"), ("KAYNES","Kaynes Tech"), ("SYRMA","Syrma SGS")]),

    # PSU Power / Energy
    "NHPC":        ("PSU Power",              [("NTPC","NTPC"), ("SJVN","SJVN"), ("THDC","THDC"), ("NEEPCO","NEEPCO")]),
    "SJVN":        ("PSU Power",              [("NHPC","NHPC"), ("NTPC","NTPC"), ("TATAPOWER","Tata Power")]),
    "COALINDIA":   ("Mining",                 [("NMDC","NMDC"), ("MOIL","MOIL"), ("GMDC","GMDC")]),
    "NMDC":        ("Mining",                 [("COALINDIA","Coal India"), ("MOIL","MOIL"), ("GMDC","GMDC")]),

    # Retail
    "DMART":       ("Retail",                 [("TRENT","Trent"), ("VMART","V-Mart"), ("SHOPERSTOP","Shoppers Stop")]),
    "TRENT":       ("Retail",                 [("DMART","DMart"), ("VMART","V-Mart"), ("SHOPERSTOP","Shoppers Stop")]),

    # Insurance
    "HDFCLIFE":    ("Life Insurance",         [("SBILIFE","SBI Life"), ("ICICIPRULI","ICICI Pru Life"), ("MAXFINSERV","Max Financial")]),
    "SBILIFE":     ("Life Insurance",         [("HDFCLIFE","HDFC Life"), ("ICICIPRULI","ICICI Pru Life"), ("MAXFINSERV","Max Financial")]),

    # Logistics
    "DELHIVERY":   ("Logistics",              [("BLUEDART","Blue Dart"), ("GATI","Gati"), ("XPRESSBEES","XpressBees")]),

    # Hospitals
    "APOLLOHOSP":  ("Hospitals",              [("FORTIS","Fortis"), ("MAXHEALTH","Max Healthcare"), ("NARAYANA","Narayana Health")]),
    "MAXHEALTH":   ("Hospitals",              [("APOLLOHOSP","Apollo Hospitals"), ("FORTIS","Fortis"), ("NARAYANA","Narayana Health")]),}


# ─── FETCH PEER METRICS ───────────────────────────────────────────────────────

def _pct_change(curr, prev):
    try:
        if prev and prev != 0 and curr is not None:
            return round(((curr - prev) / abs(prev)) * 100, 1)
    except Exception:
        pass
    return None


def _get_peer_metrics(symbol: str, display_name: str,
                      current_quarter: str, current_fy: str) -> dict:
    """
    Get current quarter + YoY prior quarter metrics for a peer.
    Returns dict with key financial metrics and growth rates.
    """
    # Fetch enough quarters to find current + YoY
    current_data = get_history(symbol, limit=5)
    # Try to match the same quarter
    curr = None
    for h in current_data:
        if h.get("quarter") == current_quarter and h.get("fiscal_year") == current_fy:
            curr = h
            break
    # If exact quarter not found, use latest available
    if curr is None and current_data:
        curr = current_data[0]

    if not curr:
        # Try direct Yahoo fetch
        yahoo = fetch_from_yahoo(symbol, quarters=2)
        curr = yahoo[0] if yahoo else None

    if not curr:
        return {
            "symbol":             symbol,
            "name":               display_name,
            "available":          False,
        }

    # YoY prior (same quarter last FY)
    history = get_history(symbol, current_quarter=curr.get("quarter"),
                          current_fy=curr.get("fiscal_year"), limit=4)
    yoy_data = None
    for h in history:
        if h.get("quarter") == curr.get("quarter"):
            yoy_data = h
            break

    return {
        "symbol":              symbol,
        "name":                display_name,
        "available":           True,
        "quarter":             curr.get("quarter"),
        "fiscal_year":         curr.get("fiscal_year"),
        "revenue_cr":          curr.get("revenue_cr"),
        "pat_cr":              curr.get("pat_cr"),
        "ebitda_margin_pct":   curr.get("ebitda_margin_pct"),
        "eps":                 curr.get("eps"),
        "revenue_growth_yoy":  _pct_change(curr.get("revenue_cr"),   yoy_data.get("revenue_cr")  if yoy_data else None),
        "pat_growth_yoy":      _pct_change(curr.get("pat_cr"),        yoy_data.get("pat_cr")       if yoy_data else None),
        "eps_growth_yoy":      _pct_change(curr.get("eps"),           yoy_data.get("eps")          if yoy_data else None),
    }


# ─── RANK HELPERS ─────────────────────────────────────────────────────────────

def _rank_metric(all_peers: list, subject_val, metric_key: str,
                 higher_is_better: bool = True) -> dict:
    """
    Rank subject_val among all peers (including subject) on a metric.
    Returns {rank, total, value, all_values_sorted}
    """
    values = []
    for p in all_peers:
        v = p.get(metric_key)
        if v is not None:
            values.append((p["name"], v))

    if subject_val is not None:
        # Check if subject already in list (it should be)
        subject_in = any(abs(v - subject_val) < 0.01 for _, v in values)
        if not subject_in:
            values.append(("Subject", subject_val))

    if not values:
        return {"rank": None, "total": 0, "value": subject_val}

    values.sort(key=lambda x: x[1], reverse=higher_is_better)
    total = len(values)

    rank = None
    for i, (name, val) in enumerate(values):
        if subject_val is not None and abs(val - subject_val) < 0.01:
            rank = i + 1
            break

    return {
        "rank":   rank,
        "total":  total,
        "value":  subject_val,
        "sorted": values,   # [(name, value), ...] sorted best→worst
    }


def _ordinal(n):
    if n is None:
        return "N/A"
    s = ["th", "st", "nd", "rd"] + ["th"] * 16
    return f"{n}{s[n % 20] if n <= 20 else 'th'}"


# ─── GROWTH ANALYSIS ─────────────────────────────────────────────────────────

def _growth_trend_label(values: list) -> str:
    """
    Given list of metric values newest→oldest,
    return trend label: Accelerating / Stable / Decelerating / Recovering / Deteriorating / Insufficient Data
    """
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return "Insufficient Data"

    # Compute sequential changes
    changes = [vals[i] - vals[i + 1] for i in range(len(vals) - 1)]

    all_positive = all(c > 0 for c in changes)
    all_negative = all(c < 0 for c in changes)
    last_positive = changes[0] > 0 if changes else False
    last_negative = changes[0] < 0 if changes else False

    if all_positive:
        # Is it speeding up or consistent?
        if len(changes) >= 2 and changes[0] > changes[1]:
            return "Accelerating ↑↑"
        return "Consistently Growing ↑"
    elif all_negative:
        return "Deteriorating ↓↓"
    elif last_positive and not all_positive:
        return "Recovering ↑"
    elif last_negative and not all_negative:
        return "Decelerating ↓"
    else:
        return "Stable →"


def _growth_verdict(subject_symbol: str, rankings: dict, growth_trends: dict) -> str:
    """
    Generate a 2-3 sentence professional growth verdict.
    """
    lines = []

    rev_rank = rankings.get("revenue_growth", {})
    pat_rank = rankings.get("pat_growth", {})
    margin   = rankings.get("ebitda_margin", {})

    if rev_rank.get("rank") and rev_rank.get("total"):
        lines.append(
            f"{subject_symbol} ranks {_ordinal(rev_rank['rank'])} of "
            f"{rev_rank['total']} peers on Revenue Growth "
            f"({'+' if (rev_rank['value'] or 0) >= 0 else ''}"
            f"{rev_rank['value']:.1f}% YoY)."
            if rev_rank.get("value") is not None else ""
        )

    if pat_rank.get("rank") and pat_rank.get("total"):
        lines.append(
            f"PAT growth ranks {_ordinal(pat_rank['rank'])} of "
            f"{pat_rank['total']} peers "
            f"({'+' if (pat_rank['value'] or 0) >= 0 else ''}"
            f"{pat_rank['value']:.1f}% YoY)."
            if pat_rank.get("value") is not None else ""
        )

    # Growth trend summary
    rev_trend = growth_trends.get("revenue", "")
    pat_trend = growth_trends.get("pat", "")
    if rev_trend and pat_trend:
        lines.append(f"Revenue trend: {rev_trend}. PAT trend: {pat_trend}.")

    return " ".join(l for l in lines if l)


# ─── MAIN PUBLIC FUNCTION ─────────────────────────────────────────────────────

def run_peer_comparison(symbol: str, current_quarter: str, current_fy: str,
                        subject_financials: dict, subject_history: list) -> dict:
    """
    Full peer comparison for a subject company.

    Returns dict with:
        sector, peers_data, rankings, growth_trends, verdict, available
    """
    symbol_upper = symbol.upper()

    if symbol_upper not in PEER_GROUPS:
        print(f"[PEERS] {symbol}: no peer group defined — skipping comparison")
        return {"available": False, "sector": "Unknown"}

    sector, peer_list = PEER_GROUPS[symbol_upper]
    print(f"[PEERS] {symbol} | Sector: {sector} | Peers: {len(peer_list)}")

    # ── Collect peer metrics ──────────────────────────────────────────────────
    all_peers_data = []
    for peer_sym, peer_name in peer_list:
        print(f"[PEERS]   Fetching {peer_name} ({peer_sym})...")
        peer_data = _get_peer_metrics(peer_sym, peer_name, current_quarter, current_fy)
        all_peers_data.append(peer_data)

    # Add subject itself for ranking context
    subject_entry = {
        "symbol":            symbol_upper,
        "name":              symbol_upper,
        "available":         True,
        "revenue_cr":        subject_financials.get("revenue_cr"),
        "pat_cr":            subject_financials.get("pat_cr"),
        "ebitda_margin_pct": subject_financials.get("ebitda_margin_pct"),
        "eps":               subject_financials.get("eps"),
        "revenue_growth_yoy":_pct_change(
            subject_financials.get("revenue_cr"),
            next((h.get("revenue_cr") for h in subject_history
                  if h.get("quarter") == current_quarter), None)
        ),
        "pat_growth_yoy":    _pct_change(
            subject_financials.get("pat_cr"),
            next((h.get("pat_cr") for h in subject_history
                  if h.get("quarter") == current_quarter), None)
        ),
        "eps_growth_yoy":    _pct_change(
            subject_financials.get("eps"),
            next((h.get("eps") for h in subject_history
                  if h.get("quarter") == current_quarter), None)
        ),
    }

    available_peers = [p for p in all_peers_data if p.get("available")]
    all_for_ranking = available_peers + [subject_entry]

    # ── Rankings ─────────────────────────────────────────────────────────────
    rankings = {
        "revenue_growth": _rank_metric(all_for_ranking, subject_entry.get("revenue_growth_yoy"), "revenue_growth_yoy"),
        "pat_growth":     _rank_metric(all_for_ranking, subject_entry.get("pat_growth_yoy"),     "pat_growth_yoy"),
        "eps_growth":     _rank_metric(all_for_ranking, subject_entry.get("eps_growth_yoy"),     "eps_growth_yoy"),
        "ebitda_margin":  _rank_metric(all_for_ranking, subject_entry.get("ebitda_margin_pct"),  "ebitda_margin_pct"),
    }

    # ── Growth Trends (subject, 3 quarters) ──────────────────────────────────
    rev_vals = [subject_financials.get("revenue_cr")] + \
               [h.get("revenue_cr") for h in subject_history[:3]]
    pat_vals = [subject_financials.get("pat_cr")] + \
               [h.get("pat_cr") for h in subject_history[:3]]

    growth_trends = {
        "revenue": _growth_trend_label(rev_vals),
        "pat":     _growth_trend_label(pat_vals),
    }

    verdict = _growth_verdict(symbol_upper, rankings, growth_trends)

    return {
        "available":     True,
        "sector":        sector,
        "peers_data":    all_peers_data,
        "subject":       subject_entry,
        "rankings":      rankings,
        "growth_trends": growth_trends,
        "verdict":       verdict,
    }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from database import init_db
    init_db()
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else "TITAN"
    result = run_peer_comparison(sym, "Q1", "FY26", {
        "revenue_cr": 12430, "pat_cr": 786,
        "ebitda_margin_pct": 12.7, "eps": 8.85,
    }, [])
    import json
    print(json.dumps({k: v for k, v in result.items() if k != "peers_data"}, indent=2))
    print("\nPeer data:")
    for p in result.get("peers_data", []):
        print(f"  {p['name']:<20} Rev: {p.get('revenue_cr')} | PAT: {p.get('pat_cr')} | Margin: {p.get('ebitda_margin_pct')} | RevG: {p.get('revenue_growth_yoy')}%")
